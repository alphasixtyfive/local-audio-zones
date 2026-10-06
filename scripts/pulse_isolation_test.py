#!/usr/bin/env python3
"""Test named-output isolation against a private PulseAudio server and null sinks.

Run inside a disposable Linux image containing the built player, pulseaudio,
pulseaudio-utils and Python. No host audio socket or sound device is used.
"""

import importlib.util
import json
import math
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
    for executable in ("pulseaudio", "pactl", "pacat", "parec", "sendspin-cli"):
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

        def check_generated_route():
            source = Path(__file__).resolve().parents[1] / "local_audio_zones/rootfs/usr/lib/sendspin-cli/audio_routes.py"
            spec = importlib.util.spec_from_file_location("audio_routes", source)
            routing = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(routing)

            def private_pactl(*arguments):
                # A null master has no hardware card or PCM. Supply those identities only;
                # module creation, arguments, sink properties and cleanup stay native.
                if arguments == ("--format=json", "list", "cards"):
                    return json.dumps([{"index": 41, "properties": {"alsa.card": "7"}},
                                       {"index": 42, "properties": {"alsa.card": "8"}}])
                result = pactl(*arguments)
                if arguments == ("--format=json", "list", "sinks"):
                    sinks = json.loads(result)
                    for sink in sinks:
                        if sink["name"] == "physical":
                            sink.pop("card", None)
                            sink["properties"]["alsa.card"] = "7"
                            sink["properties"]["alsa.device"] = "0"
                            sink["description"] = 'Private "USB" café card'
                        elif sink["name"] == "aux_physical":
                            sink.pop("card", None)
                            sink["properties"]["alsa.card"] = "8"
                    return json.dumps(sinks)
                return result.rstrip("\n")

            routes = routing.AudioRoutes(pactl=private_pactl,
                                         device_resolver=lambda device: (7, 0 if device == "/dev/snd/pcmC7D0p" else None),
                                         registry=work / "owned-routes.json")
            def master_volume():
                return next(item["volume"] for item in json.loads(pactl("--format=json", "list", "sinks"))
                            if item["name"] == "physical")

            original_master_volume = master_volume()
            player = {"id": "rear", "name": 'Rear "room" café', "device": "/dev/snd/pcmC7D0p", "channel_pair": "rear"}
            try:
                routes.resolve([player, {**player, "id": "alias", "device": "/dev/snd/by-id/private-usb-card"}])
            except routing.RoutingError:
                pass
            else:
                raise AssertionError("two device aliases silently shared one physical stereo pair")
            if "local_audio_zones_rear" in sink_names() or (work / "owned-routes.json").exists():
                raise AssertionError("duplicate device aliases changed audio routes before rejection")
            result = routes.resolve([player])
            if result[0]["output"] != "pulse:local_audio_zones_rear":
                raise AssertionError("generated route has the wrong output name")
            sink = next(item for item in json.loads(pactl("--format=json", "list", "sinks"))
                        if item["name"] == "local_audio_zones_rear")
            if sink["channel_map"] != "front-left,front-right" or sink["description"] != "local_audio_zones_rear":
                raise AssertionError("native module rejected stereo mapping or stable output description")
            if master_volume() != original_master_volume:
                raise AssertionError("creating a route changed the physical master gain")
            original = sink["volume"]
            if routes.resolve([player]) != result:
                raise AssertionError("second resolution did not reuse the native remap")
            sink = next(item for item in json.loads(pactl("--format=json", "list", "sinks"))
                        if item["name"] == "local_audio_zones_rear")
            if sink["volume"] != original:
                raise AssertionError("reusing a native route changed its gain")
            routes.cleanup()
            if "local_audio_zones_rear" in sink_names() or "physical" not in sink_names():
                raise AssertionError("cleanup removed the physical master or retained its owned route")
            if master_volume() != original_master_volume:
                raise AssertionError("cleanup changed the physical master gain")
            borrowed = routes.resolve([{**player, "id": "front", "channel_pair": "front"}])
            if borrowed[0]["output"] != "pulse:study":
                raise AssertionError("existing host stereo remap was not reused")
            routes.cleanup()
            if "study" not in sink_names():
                raise AssertionError("cleanup removed a borrowed host remap")

            collision = pactl("load-module", "module-null-sink", "sink_name=local_audio_zones_side").strip()
            try:
                try:
                    routes.resolve([player, {**player, "id": "side", "channel_pair": "side"}])
                except routing.RoutingError:
                    pass
                else:
                    raise AssertionError("creation overwrote a foreign output name")
                if "local_audio_zones_rear" in sink_names() or not {"physical", "study", "local_audio_zones_side"}.issubset(sink_names()):
                    raise AssertionError("partial failure removed foreign outputs or retained its own route")
                if json.loads((work / "owned-routes.json").read_text()) or master_volume() != original_master_volume:
                    raise AssertionError("rollback retained stale ownership or changed master gain")
            finally:
                pactl("unload-module", collision)
            print("  ok   native PulseAudio accepts generated stereo routes, descriptions, reuse and cleanup", flush=True)

            channel_map = ",".join("aux" + str(index) for index in range(8))
            aux_master = pactl("load-module", "module-null-sink", "sink_name=aux_physical",
                               "rate=48000", "channels=8", "channel_map=" + channel_map).strip()
            custom_routes = routing.AudioRoutes(pactl=private_pactl, device_resolver=lambda _: (8, None),
                                                registry=work / "custom-routes.json")
            custom = {"id": "custom", "name": "Custom room", "device": "/dev/snd/controlC8",
                      "channels": ["aux5", "aux4"]}
            try:
                result = custom_routes.resolve([custom])
                if custom_routes.resolve([custom]) != result:
                    raise AssertionError("custom channels did not reuse their exact native route")
                owned = json.loads((work / "custom-routes.json").read_text())
                if len(owned) != 1 or "master_channel_map=aux5,aux4 remix=no" not in owned[0]["argument"]:
                    raise AssertionError("native route lost the custom channel order")
                capture_path = work / "custom-channel-capture.pcm"
                with capture_path.open("wb") as capture:
                    recorder = subprocess.Popen(
                        ["parec", "--raw", "--format=s16le", "--rate=48000", "--channels=8",
                         "--latency-msec=20", "--channel-map=" + channel_map, "--device=aux_physical.monitor"],
                        env=environment, stdout=capture, stderr=subprocess.PIPE,
                    )
                    try:
                        time.sleep(0.2)
                        tone = b"".join(struct.pack("<hh", int(5000 * math.sin(2 * math.pi * 440 * n / 48000)),
                                                   int(5000 * math.sin(2 * math.pi * 1100 * n / 48000)))
                                        for n in range(28800))
                        subprocess.run(["pacat", "--playback", "--raw", "--format=s16le", "--rate=48000",
                                        "--latency-msec=20", "--channels=2", "--channel-map=front-left,front-right",
                                        "--device=local_audio_zones_custom"],
                                       env=environment, input=tone, capture_output=True, check=True, timeout=5)
                        time.sleep(0.2)
                    finally:
                        recorder.terminate()
                        recorder.communicate(timeout=3)
                captured = capture_path.read_bytes()
                frames = list(struct.iter_unpack("<8h", captured[:len(captured) // 16 * 16]))
                if not frames:
                    raise AssertionError("custom channel monitor captured no audio")
                power = [sum(frame[channel] ** 2 for frame in frames) for channel in range(8)]
                print(f"  custom capture: {len(frames)} frames; channel energy {power}", flush=True)
                if min(power[4], power[5]) < 1_000_000 or any(power[channel] > max(power) * 0.001
                                                            for channel in (0, 1, 2, 3, 6, 7)):
                    raise AssertionError(f"custom aux4/aux5 audio missing or misrouted: energy {power}")
                def magnitude(channel, frequency):
                    real = sum(frame[channel] * math.cos(2 * math.pi * frequency * n / 48000)
                               for n, frame in enumerate(frames))
                    imaginary = sum(frame[channel] * math.sin(2 * math.pi * frequency * n / 48000)
                                    for n, frame in enumerate(frames))
                    return math.hypot(real, imaginary)
                if magnitude(5, 440) < 5 * magnitude(5, 1100) or magnitude(4, 1100) < 5 * magnitude(4, 440):
                    raise AssertionError("custom channels reversed left/right source order")
                custom_routes.cleanup()
                if "local_audio_zones_custom" in sink_names() or "aux_physical" not in sink_names():
                    raise AssertionError("custom cleanup retained its route or removed its master")
                print("  ok   native custom aux5/aux4 route preserves order, isolates audio and reuses safely", flush=True)
            finally:
                custom_routes.cleanup()
                pactl("unload-module", aux_master)

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
            check_generated_route()
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
            for arguments in (("list", "short", "modules"), ("--format=json", "list", "sinks")):
                try:
                    print(f"\n--- private pactl {' '.join(arguments)} ---\n{pactl(*arguments)}", flush=True)
                except (AssertionError, OSError, subprocess.SubprocessError):
                    pass
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
