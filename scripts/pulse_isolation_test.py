#!/usr/bin/env python3
"""Test named-output isolation against a private PulseAudio server and null sinks.

Run inside a disposable Linux image containing the built player, pulseaudio,
pulseaudio-utils and Python. No host audio socket or sound device is used.
"""

import json
import os
from pathlib import Path
import select
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time

import fake_server


class Stream(threading.Thread):
    """Keep sending PCM so native sink recovery receives real writes."""

    def __init__(self, port):
        super().__init__(daemon=True)
        self.port = port
        self.stopped = threading.Event()
        self.error = None
        self.connection = None

    def run(self):
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    self.connection = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            with self.connection as connection:
                if not fake_server.complete_handshake(connection, self.port):
                    raise AssertionError("player refused the WebSocket handshake")
                fake_server.send_message(
                    connection,
                    {
                        "type": "server/hello",
                        "payload": {
                            "server_id": "pulse-isolation-test",
                            "name": "PulseAudio isolation test",
                            "version": 1,
                            "active_roles": ["player"],
                            "connection_reason": "playback",
                        },
                    },
                )
                streaming = False
                next_audio = 0
                while not self.stopped.is_set():
                    now = time.monotonic()
                    if streaming and now >= next_audio:
                        # Pinned sendspin-cpp v0.7.2: type, 64-bit timestamp, PCM.
                        timestamp = fake_server.now_us() + 200_000
                        fake_server.send_frame(
                            connection, 2, struct.pack(">Bq", 4, timestamp) + bytes(960 * 4)
                        )
                        next_audio = now + 0.02
                    if not select.select([connection], [], [], 0.01)[0]:
                        continue
                    frame = fake_server.receive_frame(connection)
                    if frame is None:
                        raise AssertionError("player disconnected during isolation testing")
                    opcode, payload = frame
                    if opcode == fake_server.OPCODE_PING:
                        fake_server.send_frame(connection, fake_server.OPCODE_PONG, payload)
                    elif opcode == fake_server.OPCODE_TEXT:
                        message = json.loads(payload)
                        if message["type"] == "client/hello":
                            fake_server.send_message(
                                connection,
                                {
                                    "type": "stream/start",
                                    "payload": {
                                        "player": {
                                            "codec": "pcm",
                                            "sample_rate": 48000,
                                            "bit_depth": 16,
                                            "channels": 2,
                                        }
                                    },
                                },
                            )
                            streaming = True
                        elif message["type"] == "client/time":
                            fake_server.send_message(
                                connection,
                                {
                                    "type": "server/time",
                                    "payload": {
                                        "client_transmitted": message["payload"][
                                            "client_transmitted"
                                        ],
                                        "server_received": fake_server.now_us(),
                                        "server_transmitted": fake_server.now_us(),
                                    },
                                },
                            )
        except (OSError, ValueError, AssertionError) as error:
            if not self.stopped.is_set():
                self.error = error

    def stop(self):
        self.stopped.set()
        if self.connection is not None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.join(timeout=3)


def main():
    for executable in ("pulseaudio", "pactl", "sendspin-cli"):
        if shutil.which(executable) is None:
            raise SystemExit(f"Missing {executable}; use the disposable audio-test image.")

    processes = []
    stream = None
    # Keep Unix socket paths below the kernel's 108-byte limit.
    with tempfile.TemporaryDirectory(prefix="pulse-isolation-", dir="/tmp") as directory:
        work = Path(directory)
        runtime = work / "runtime"
        runtime.mkdir(mode=0o700)
        socket_path = runtime / "pulse.sock"
        environment = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), PULSE_SERVER=f"unix:{socket_path}")
        pulse_config = work / "pulse.pa"
        pulse_config.write_text(
            f"load-module module-native-protocol-unix socket={socket_path} auth-anonymous=1\n"
            "load-module module-null-sink sink_name=wrong rate=48000\n"
            "set-default-sink wrong\n"
        )
        pulse_log = work / "pulse.log"
        player_log = work / "player.log"

        def free_port():
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                return reservation.getsockname()[1]

        def pactl(*arguments):
            result = subprocess.run(
                ["pactl", *arguments], env=environment, capture_output=True, text=True, timeout=3
            )
            if result.returncode:
                raise AssertionError(f"pactl {' '.join(arguments)}: {result.stderr.strip()}")
            return result.stdout

        def inputs():
            sinks = {sink["index"]: sink["name"] for sink in json.loads(pactl("--format=json", "list", "sinks"))}
            return [
                sinks.get(item["sink"], "removed")
                for item in player_inputs()
            ]

        def player_inputs():
            return [
                item for item in json.loads(pactl("--format=json", "list", "sink-inputs"))
                if item.get("properties", {}).get("application.name") == "sendspin-cli"
            ]

        def sink_names():
            return {sink["name"] for sink in json.loads(pactl("--format=json", "list", "sinks"))}

        def wait_for(description, condition, timeout=15):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if stream is not None and stream.error is not None:
                    raise AssertionError(f"audio fixture failed: {stream.error}")
                if condition():
                    print(f"  ok   {description}", flush=True)
                    return
                time.sleep(0.05)
            raise AssertionError(description)

        def load_master():
            if "physical" in sink_names():
                raise AssertionError("old physical output still exists")
            module = pactl(
                "load-module", "module-null-sink", "sink_name=physical", "rate=48000",
                "channels=8", "channel_map=front-left,front-right,rear-left,rear-right,front-center,lfe,side-left,side-right",
            ).strip()
            if "physical" not in sink_names():
                raise AssertionError("physical output was renamed during registration")
            return module

        def load_room():
            if "study" in sink_names():
                raise AssertionError("old Study output still exists")
            module = pactl(
                "load-module", "module-remap-sink", "sink_name=study", "master=physical",
                "channels=2", "channel_map=front-left,front-right",
                "master_channel_map=front-left,front-right", "remix=no",
            ).strip()
            if "study" not in sink_names():
                raise AssertionError("Study output was renamed during registration")
            return module

        def assert_no_fallback():
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if "wrong" in inputs():
                    raise AssertionError("named Study stream moved to the default output")
                time.sleep(0.05)
            print("  ok   absent named output never routes through the default", flush=True)

        try:
            with pulse_log.open("w") as log:
                processes.append(subprocess.Popen(
                    ["pulseaudio", "--daemonize=no", "--exit-idle-time=-1", "--use-pid-file=no",
                     "--disable-shm", "--log-target=stderr", "-nF", str(pulse_config)],
                    env=environment, stdout=log, stderr=subprocess.STDOUT,
                ))
            wait_for("private PulseAudio server starts", socket_path.exists, timeout=5)
            master = load_master()
            room = load_room()
            player_port = free_port()
            config = work / "player.conf"
            config.write_text(
                "name = Study\noutput = pulse:study\nid = isolation-study\n"
                f"port = {player_port}\nlog-level = debug\nbuffer-ms = 50\n"
                "server = mdns:isolation-test-no-server\n"
            )
            with player_log.open("w") as log:
                processes.append(subprocess.Popen(
                    ["sendspin-cli", "--config", str(config), "--state-dir", str(work / "state"),
                     "--control-socket", str(work / "control.sock")],
                    env=environment, stdout=log, stderr=subprocess.STDOUT,
                ))
            wait_for("native player starts", lambda: (work / "control.sock").exists(), timeout=10)
            stream = Stream(player_port)
            stream.start()
            wait_for("native player stream opens on the named remap", lambda: inputs() == ["study"])
            pactl("unload-module", room)
            assert_no_fallback()
            wait_for("removed room is gone before recreation", lambda: "study" not in sink_names())
            load_room()
            wait_for("native recovery returns to the same room", lambda: inputs() == ["study"])

            # Upstream resets its bounded recovery budget at the next stream.
            previous_index = player_inputs()[0]["index"]
            stream.stop()
            stream = Stream(player_port)
            stream.start()
            wait_for(
                "a new stream resets recovery before the separate master test",
                lambda: inputs() == ["study"] and player_inputs()[0]["index"] != previous_index,
            )
            pactl("unload-module", master)
            assert_no_fallback()
            wait_for(
                "removed master and its room are gone before recreation",
                lambda: not {"study", "physical"} & sink_names(),
            )
            load_master()
            load_room()
            wait_for("master recovery returns to the same room", lambda: inputs() == ["study"])

            missing = subprocess.run(
                ["sendspin-cli", "--output", "pulse:absent", "--name", "Missing room", "--port", str(free_port())],
                env=environment, capture_output=True, text=True, timeout=10,
            )
            if (
                missing.returncode == 0
                or "has no sink by that name" not in missing.stdout + missing.stderr
                or inputs() != ["study"]
            ):
                raise AssertionError("a missing named output silently selected another sink")
            print("  ok   missing named output is rejected without fallback", flush=True)
        except BaseException:
            for path in (pulse_log, player_log):
                if path.exists():
                    print(f"\n--- {path.name} ---\n{path.read_text()}", flush=True)
            raise
        finally:
            if stream is not None:
                stream.stop()
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


if __name__ == "__main__":
    main()
