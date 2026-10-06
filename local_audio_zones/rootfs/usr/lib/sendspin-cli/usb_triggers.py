#!/usr/bin/env python3
"""Control explicitly configured USB serial relays from local player status."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import signal
import socket
import stat
import threading
import time

LOG = logging.getLogger("usb-triggers")
RUN = Path("/run/sendspin-cli")
PROTOCOLS = {"DSD TECH SH-UR01A": 1, "KMtronic": 1, "LCUS": 8}
DEVICE = re.compile(r"/dev/(?:serial/(?:by-id|by-path)/[^/]+|tty(?:USB|ACM)[0-9]+)\Z")


@dataclass(frozen=True)
class Trigger:
    name: str
    device: str
    protocol: str
    channel: int
    zones: tuple[str, ...]
    off_delay: int


def configuration(options, players):
    if not isinstance(options, dict) or not isinstance(players, list):
        raise ValueError("Expected app options and a player list")
    entries = options.get("usb_relays", [])
    if not isinstance(entries, list) or len(entries) > 16:
        raise ValueError("usb_relays must be a list of at most 16 relay channels")
    zone_ids = {player["id"] for player in players}
    result, channels, protocols, aliases = [], set(), {}, {}
    for index, entry in enumerate(entries):
        label = f"usb_relays[{index}]"
        if not isinstance(entry, dict) or set(entry) - {
            "name", "device", "protocol", "channel", "zones", "off_delay"
        }:
            raise ValueError(f"{label}: unknown option or invalid relay")
        for key in ("name", "device", "protocol"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip() or any(
                ord(char) < 32 or 127 <= ord(char) <= 159 for char in value
            ):
                raise ValueError(f"{label}.{key} must be nonempty text")
        device, protocol = entry["device"], entry["protocol"]
        if not DEVICE.fullmatch(device) or Path(device).name in (".", ".."):
            raise ValueError(f"{label}.device must be a USB serial device or stable serial link")
        if protocol not in PROTOCOLS:
            raise ValueError(f"{label}.protocol must be one of {', '.join(PROTOCOLS)}")
        channel, delay = entry.get("channel", 1), entry.get("off_delay", 60)
        if type(channel) is not int or not 1 <= channel <= PROTOCOLS[protocol]:
            raise ValueError(f"{label}.channel is invalid for {protocol}")
        if type(delay) is not int or not 0 <= delay <= 3600:
            raise ValueError(f"{label}.off_delay must be a whole number from 0 to 3600")
        zones = entry.get("zones")
        if not isinstance(zones, list) or not zones or any(
            not isinstance(zone, str) or zone not in zone_ids for zone in zones
        ):
            raise ValueError(f"{label}.zones must name configured player IDs")
        if len(set(zones)) != len(zones):
            raise ValueError(f"{label}.zones contains duplicate player IDs")
        real_device = os.path.realpath(device)
        if real_device in aliases and aliases[real_device] != device:
            raise ValueError(f"{label}: use the same device path for every channel on a board")
        aliases[real_device] = device
        if device in protocols and protocols[device] != protocol:
            raise ValueError(f"{label}: one device cannot use different relay protocols")
        protocols[device] = protocol
        if (device, channel) in channels:
            raise ValueError(f"{label}: relay channel is already assigned")
        channels.add((device, channel))
        result.append(Trigger(entry["name"], device, protocol, channel, tuple(zones), delay))
    return result


def command(protocol, channel, enabled):
    if protocol not in PROTOCOLS or type(channel) is not int or not 1 <= channel <= PROTOCOLS[protocol]:
        raise ValueError("Invalid relay protocol or channel")
    if protocol == "DSD TECH SH-UR01A":
        return f"AT+CH1={int(enabled)}\r\n".encode("ascii")
    if protocol == "KMtronic":
        return bytes((0xFF, channel, int(enabled)))
    frame = (0xA0, channel, int(enabled))
    return bytes((*frame, sum(frame) & 0xFF))


def stream_status(zone, run_dir=RUN):
    """None is unknown, not an idle stream or permission to turn an amp off."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            deadline = time.monotonic() + 0.8
            connection.settimeout(0.4)
            connection.connect(str(run_dir / "zones" / zone / "control.sock"))
            connection.sendall(b"status\n")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                connection.settimeout(min(0.4, remaining))
                chunk = connection.recv(4096)
                if not chunk:
                    break
                size += len(chunk)
                if size > 65536:
                    return None
                chunks.append(chunk)
        lines = b"".join(chunks).decode("utf-8").splitlines()
        if not lines or lines[0] != "ok":
            return None
        streams = [line.removeprefix("stream: ") for line in lines[1:] if line.startswith("stream: ")]
        if streams == ["receiving"]:
            return True
        if streams == ["idle"]:
            return False
    except (OSError, UnicodeError):
        pass
    return None


@dataclass
class Demand:
    enabled: bool = False
    idle_since: float | None = None

    def update(self, states, now, delay):
        if any(state is True for state in states):
            self.enabled, self.idle_since = True, None
        elif any(state is None for state in states):
            self.idle_since = None
        else:
            if self.idle_since is None:
                self.idle_since = now
            if now - self.idle_since >= delay:
                self.enabled = False
        return self.enabled


class Board:
    def __init__(self, device, protocol, serial_factory=None, stop=None):
        self.device, self.protocol = device, protocol
        self.serial_factory = serial_factory
        self.stop = stop
        self.port = None
        self.identity = None
        self.applied = {}
        self.retry_at = 0.0
        self.error = None

    def close(self):
        if self.port is not None:
            try:
                self.port.close()
            except OSError:
                pass
        self.port, self.identity = None, None
        self.applied.clear()

    def acknowledge(self):
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            line = self.port.read_until(b"\n", 128).strip()
            if line == b"OK":
                return
            if line == b"ERROR":
                break
        raise OSError("relay did not acknowledge the command")

    def write(self, data):
        if self.port.write(data) != len(data):
            raise OSError("incomplete relay command")
        if self.protocol == "DSD TECH SH-UR01A":
            self.acknowledge()

    def connect(self, identity):
        if self.serial_factory is None:
            import serial
            self.serial_factory = serial.Serial
        port = self.serial_factory(port=None, baudrate=9600, timeout=0.1,
                                   write_timeout=0.2, exclusive=True)
        self.port = port
        port.dtr, port.rts = False, False
        port.port = self.device
        port.open()
        self.identity = identity
        if self.protocol == "DSD TECH SH-UR01A":
            port.reset_input_buffer()
            self.write(b"AT\r\n")
        LOG.info("Connected %s relay at %s", self.protocol, self.device)

    def apply(self, desired, now):
        if now < self.retry_at:
            return
        try:
            info = os.stat(self.device)
            if not stat.S_ISCHR(info.st_mode):
                raise OSError("selected path is not a character device")
            identity = (os.path.realpath(self.device), info.st_rdev, info.st_ino)
            if self.port is not None and identity != self.identity:
                self.close()
            if self.port is None:
                self.connect(identity)
            for channel, enabled in desired.items():
                if self.stop is not None and self.stop.is_set():
                    return
                if self.applied.get(channel) is enabled:
                    continue
                self.write(command(self.protocol, channel, enabled))
                self.applied[channel] = enabled
                LOG.info("%s channel %s commanded %s", self.device, channel, "on" if enabled else "off")
            self.error = None
        except (OSError, ValueError) as error:
            self.close()
            self.retry_at = now + 5
            message = str(error)
            if message != self.error:
                LOG.warning("USB relay %s unavailable: %s; retrying", self.device, message)
                self.error = message

    def shutdown(self, channels):
        # Never open a previously unavailable port just to send a shutdown command.
        try:
            if self.port is not None:
                for channel in channels:
                    self.write(command(self.protocol, channel, False))
        except (OSError, ValueError) as error:
            LOG.warning("Could not switch off USB relay %s: %s", self.device, error)
        finally:
            self.close()


def run(triggers, stop):
    if not triggers:
        return
    boards = {item.device: Board(item.device, item.protocol, stop=stop) for item in triggers}
    demands = [Demand() for _ in triggers]
    zone_ids = sorted({zone for item in triggers for zone in item.zones})
    last_unknown = set()
    desired = {device: {} for device in boards}
    for trigger in triggers:
        LOG.info("%s: %s channel %s, rooms %s, standby delay %ss", trigger.name,
                 trigger.device, trigger.channel, ", ".join(trigger.zones), trigger.off_delay)
    with ThreadPoolExecutor(max_workers=max(len(zone_ids), len(boards))) as workers:
        try:
            while not stop.is_set():
                states = dict(zip(zone_ids, workers.map(stream_status, zone_ids)))
                unknown = {zone for zone, state in states.items() if state is None}
                if unknown != last_unknown:
                    if unknown:
                        LOG.warning("Playback status unavailable for %s; holding amplifier demand", ", ".join(sorted(unknown)))
                    else:
                        LOG.info("Playback status restored for all assigned rooms")
                    last_unknown = unknown
                now = time.monotonic()
                desired = {device: {} for device in boards}
                for trigger, demand in zip(triggers, demands):
                    desired[trigger.device][trigger.channel] = demand.update(
                        [states[zone] for zone in trigger.zones], now, trigger.off_delay)
                # Distinct paths that converge on the same port must never race commands.
                aliases = [os.path.realpath(device) for device in boards]
                if len(set(aliases)) != len(aliases):
                    raise ValueError("USB relay device paths now refer to the same port; use one path per board")
                list(workers.map(lambda board: board.apply(desired[board.device], now), boards.values()))
                stop.wait(0.5)
        finally:
            list(workers.map(lambda board: board.shutdown(desired.get(board.device, {})), boards.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "run"))
    parser.add_argument("--options", type=Path, default=Path("/data/options.json"))
    parser.add_argument("--players", type=Path, default=RUN / "players.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s usb-triggers: %(message)s")
    try:
        options = json.loads(args.options.read_text())
        players = json.loads(args.players.read_text())
        triggers = configuration(options, players)
        LOG.setLevel((options.get("log_level") or "info").upper())
        if args.action == "run":
            stop = threading.Event()
            for signum in (signal.SIGTERM, signal.SIGINT):
                signal.signal(signum, lambda *_: stop.set())
            run(triggers, stop)
    except (OSError, ValueError, KeyError, TypeError) as error:
        LOG.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
