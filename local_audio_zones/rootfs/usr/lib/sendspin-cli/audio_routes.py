"""Resolve native sound-device selections into named PulseAudio stereo outputs."""

import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time


PAIRS = {
    "front": ("front-left", "front-right"),
    "rear": ("rear-left", "rear-right"),
    "side": ("side-left", "side-right"),
    "center_sub": ("front-center", "lfe"),
}
REGISTRY = Path("/data/audio-routes.json")
STATUS = Path("/run/sendspin-cli/routes.json")


class RoutingError(Exception):
    pass


class HardwareUnavailable(RoutingError):
    pass


def pactl(*args):
    environment = dict(os.environ, LC_ALL="C")
    environment.setdefault("PULSE_SERVER", "unix:/run/audio/pulse.sock")
    try:
        result = subprocess.run(
            ["pactl", *args], env=environment, text=True, capture_output=True,
            check=True, timeout=3,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RoutingError("PulseAudio did not accept " + args[0]) from error
    return result.stdout.rstrip("\n")


def device_endpoint(device):
    """Return the ALSA card and, for a playback node, its selected PCM number."""
    try:
        path = Path(device).resolve(strict=True)
    except OSError as error:
        raise HardwareUnavailable("Selected sound device is unavailable: " + device) from error
    match = re.fullmatch(r"/dev/snd/(?:controlC([0-9]+)|pcmC([0-9]+)D([0-9]+)p)", str(path))
    if not match or not path.is_char_device():
        raise RoutingError("Select a soundcard control or playback device, not a capture, sequencer or timer device")
    return int(match[1] or match[2]), int(match[3]) if match[3] is not None else None


def module_arguments(argument):
    try:
        return dict(item.split("=", 1) for item in shlex.split(argument) if "=" in item)
    except ValueError:
        return {}


class AudioRoutes:
    def __init__(self, pactl=pactl, device_resolver=device_endpoint, registry=REGISTRY):
        self.pactl = pactl
        self.device_resolver = device_resolver
        self.registry = Path(registry)
        self.errors = {}
        self.waiting = set()
        self.stopping = lambda: False

    def listing(self, kind):
        if self.stopping():
            raise RoutingError("Audio route recovery is stopping")
        if kind == "modules":
            modules = []
            indices = set()
            for line in self.pactl("list", "short", "modules").splitlines():
                fields = line.split("\t", 3)
                if len(fields) < 3 or not re.fullmatch(r"[0-9]+", fields[0]) or not fields[1]:
                    raise RoutingError("Could not read PulseAudio module identities")
                index = int(fields[0])
                if index in indices:
                    raise RoutingError("PulseAudio returned duplicate module identities")
                indices.add(index)
                modules.append({"index": index, "name": fields[1], "argument": fields[2]})
            return modules
        try:
            value = json.loads(self.pactl("--format=json", "list", kind))
        except (ValueError, TypeError) as error:
            raise RoutingError("Could not read PulseAudio " + kind) from error
        if not isinstance(value, list):
            raise RoutingError("Could not read PulseAudio " + kind)
        return value

    def owned_modules(self):
        if not self.registry.exists():
            return []
        try:
            records = json.loads(self.registry.read_text())
            if not isinstance(records, list) or any(
                not isinstance(item, dict) or set(item) != {"index", "name", "argument"}
                or type(item["index"]) is not int or item["name"] != "module-remap-sink"
                or not isinstance(item["argument"], str) for item in records
            ):
                raise ValueError
            return records
        except (OSError, ValueError) as error:
            raise RoutingError("Could not read the app's audio-route ownership record") from error

    def record(self, modules):
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.registry.parent, prefix="audio-routes.")
        try:
            with os.fdopen(descriptor, "w") as target:
                json.dump(modules, target)
            os.replace(temporary, self.registry)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def cleanup(self):
        owned = self.owned_modules()
        if not owned:
            return
        remaining = self.remove_owned(owned)
        self.record(remaining)
        if remaining:
            raise RoutingError("Could not unload an app-created audio route")

    def remove_owned(self, owned):
        remaining = []
        for position, module in enumerate(owned):
            if self.stopping():
                return remaining + owned[position:]
            try:
                current = {item["index"]: item for item in self.listing("modules")}
            except RoutingError:
                return remaining + owned[position:]
            found = current.get(module["index"])
            if found and all(found.get(key) == value for key, value in module.items()):
                if self.stopping():
                    return remaining + owned[position:]
                try:
                    self.pactl("unload-module", str(module["index"]))
                except RoutingError:
                    remaining.append(module)
        return remaining

    def select_output(self, player, cards, sinks):
        index, pcm = self.device_resolver(player["device"])
        matching_cards = [card for card in cards
                          if str(card.get("properties", {}).get("alsa.card", "")) == str(index)]
        if len(matching_cards) != 1:
            error = RoutingError if matching_cards else HardwareUnavailable
            raise error(player["name"] + ": selected soundcard has no unique PulseAudio card")
        card = matching_cards[0]
        serial = card.get("properties", {}).get("device.serial")
        if player["device"].startswith("/dev/snd/by-id/") and serial and any(
            other["index"] != card["index"]
            and other.get("properties", {}).get("device.serial") == serial for other in cards
        ):
            raise RoutingError(player["name"] + ": multiple soundcards share this by-id identity; "
                               "select a by-path port or control/playback device and verify the physical mapping")
        channels = tuple(player["channels"]) if "channels" in player else PAIRS[player.get("channel_pair", "front")]
        card_sinks = [sink for sink in sinks if self.matches_card(sink, card, index)
                      and sink.get("properties", {}).get("device.class") != "filter"]
        if not card_sinks:
            raise HardwareUnavailable(player["name"] + ": selected soundcard has no active PulseAudio output")
        if pcm is not None:
            card_sinks = [sink for sink in card_sinks
                          if str(sink.get("properties", {}).get("alsa.device", "")) == str(pcm)]
            if not card_sinks:
                raise HardwareUnavailable(player["name"] + ": selected playback PCM " + str(pcm)
                                   + " has no active PulseAudio output. Enable its profile in Home Assistant's "
                                   "Audio settings or choose a named Explicit output")
        masters = [sink for sink in card_sinks if set(channels).issubset(self.channels(sink))]
        if len(masters) > 1:
            raise RoutingError(player["name"] + ": more than one active output has the requested channels: "
                               + ", ".join(sink["name"] for sink in masters)
                               + ". Select a playback device for the intended PCM or a named Explicit output")
        if not masters:
            available = list(dict.fromkeys(channel for sink in card_sinks for channel in self.channels(sink)))
            raise RoutingError(player["name"] + ": requested " + ", ".join(channels)
                               + "; available channels: " + (", ".join(available) or "none")
                               + ". Choose two channels on one output or a suitable profile in Home Assistant's Audio settings")
        return masters[0], channels

    @staticmethod
    def player_outputs(players):
        result = []
        outputs = {}
        for player in players:
            player = dict(player)
            if "device" in player:
                player["output"] = "pulse:local_audio_zones_" + player["id"]
                for key in ("device", "channel_pair", "channels"):
                    player.pop(key, None)
            if player["output"] in outputs:
                raise RoutingError(player["name"] + " and " + outputs[player["output"]]
                                   + " select the same audio output")
            outputs[player["output"]] = player["name"]
            result.append(player)
        return result

    def ensure_route(self, player, master, channels, sinks, modules, owned):
        target = "local_audio_zones_" + player["id"]
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", master["name"]):
            raise RoutingError("Unsupported PulseAudio master name")
        argument = (
            "sink_name=" + target + " master=" + master["name"]
            + " channels=2 channel_map=front-left,front-right master_channel_map="
            + ",".join(channels) + " remix=no sink_properties=device.description=" + target
        )
        previous = next((item for item in owned
                         if module_arguments(item["argument"]).get("sink_name") == target), None)
        sink = next((item for item in sinks if item["name"] == target), None)
        foreign = sink is not None and (previous is None or sink.get("owner_module") != previous["index"])
        if sink is not None and not foreign:
            if previous["argument"] == argument and self.channels(sink) == ["front-left", "front-right"]:
                return None
        if previous is not None:
            if self.remove_owned([previous]):
                raise RoutingError(player["name"] + ": could not replace its previous audio route")
            owned.remove(previous)
            self.record(owned)
        if foreign:
            raise RoutingError(player["name"] + ": the app output name is already in use by another module")
        if self.stopping():
            raise RoutingError("Audio route recovery is stopping")
        loaded = self.pactl("load-module", "module-remap-sink", argument)
        if not re.fullmatch(r"[0-9]+", loaded):
            raise RoutingError("PulseAudio returned an invalid module id")
        module = {"index": int(loaded), "name": "module-remap-sink", "argument": argument}
        try:
            self.record(owned + [module])
            if not self.stopping():
                sinks[:] = self.listing("sinks")
                sink = next((item for item in sinks if item["name"] == target), None)
                if sink is None or sink.get("owner_module") != module["index"]:
                    raise RoutingError(player["name"] + ": PulseAudio did not create the requested app output name")
                if self.channels(sink) != ["front-left", "front-right"]:
                    raise RoutingError(player["name"] + ": PulseAudio did not create the requested stereo output")
        except Exception:
            remaining = self.remove_owned([module])
            self.record(owned + remaining)
            raise
        owned.append(module)
        modules.append(module)
        print(player["name"] + ": using pulse:" + target + " on " + master["name"]
              + " (" + ", ".join(channels) + ")", file=sys.stderr)
        return module

    def resolve(self, players, wait=False):
        self.errors = {}
        self.waiting = set()
        outputs = self.player_outputs(players)
        if not any("device" in player for player in players):
            return outputs
        cards = self.listing("cards")
        sinks = self.listing("sinks")
        modules = self.listing("modules")
        routes = []
        selected_channels = {}

        def reserve_channels(master, channels, name):
            for channel in channels:
                previous = selected_channels.get((master, channel))
                if previous is not None:
                    raise RoutingError(name + " and " + previous + " share soundcard channel " + channel)
            for channel in channels:
                selected_channels[(master, channel)] = name

        for player in players:
            if "device" not in player:
                routes.append((player, None, None))
                continue
            try:
                master, channels = self.select_output(player, cards, sinks)
            except RoutingError as error:
                if not wait:
                    raise
                self.errors[player["id"]] = str(error)
                if isinstance(error, HardwareUnavailable) and not any(
                    sink["name"] == "local_audio_zones_" + player["id"] for sink in sinks
                ):
                    self.waiting.add(player["id"])
                continue
            reserve_channels(master["name"], channels, player["name"])
            routes.append((player, master, channels))

        module_by_index = {module["index"]: module for module in modules}
        sink_by_name = {sink["name"]: sink for sink in sinks}
        device_masters = {master["name"] for _, master, _ in routes if master is not None}
        for player, master, _ in routes:
            output = player.get("output", "")
            if master is not None or not output.startswith("pulse:"):
                continue
            sink = sink_by_name.get(output[6:])
            if sink is None:
                continue
            module = module_by_index.get(sink.get("owner_module"), {})
            if module.get("name") == "module-remap-sink":
                arguments = module_arguments(module["argument"])
                master_name = arguments.get("master")
                if master_name not in sink_by_name:
                    master_name = sink.get("properties", {}).get("device.master_device")
                if not isinstance(master_name, str) or master_name not in sink_by_name:
                    raise RoutingError(player["name"] + ": cannot identify this explicit remap's master; "
                                       "select its soundcard and channels")
                if sink_by_name[master_name].get("properties", {}).get("device.class") == "filter":
                    raise RoutingError(player["name"] + ": nested remaps cannot verify room isolation; "
                                       "select its soundcard and channels")
                if master_name in device_masters and arguments.get("remix") != "no":
                    raise RoutingError(player["name"] + ": this explicit remap can mix into other rooms; "
                                       "use a remap with remix=no or select its soundcard and channels")
                source_channels = (arguments["master_channel_map"].split(",")
                                   if "master_channel_map" in arguments else self.channels(sink))
                if all(source_channels):
                    reserve_channels(master_name, source_channels, player["name"])
            elif any(key[0] == sink["name"] for key in selected_channels):
                reserve_channels(sink["name"], self.channels(sink), player["name"])

        current = {module["index"]: module for module in modules}
        recorded = self.owned_modules()
        owned = [module for module in recorded if current.get(module["index"]) == module]
        if owned != recorded:
            self.record(owned)
        created = []
        try:
            for player, master, channels in routes:
                if master is not None:
                    try:
                        module = self.ensure_route(player, master, channels, sinks, modules, owned)
                        if module is not None:
                            created.append(module)
                    except (RoutingError, OSError) as error:
                        if not wait:
                            raise
                        self.errors[player["id"]] = str(error)
        except Exception:
            # Roll back only routes made by this attempt; host remaps remain untouched.
            remaining = self.remove_owned(created)
            self.record([module for module in owned if module not in created]
                        + remaining)
            raise
        return outputs

    def reconcile(self, players):
        wanted = {player["output"][6:] for player in self.player_outputs(players)
                  if player.get("output", "").startswith("pulse:")}
        owned = self.owned_modules()
        obsolete = [module for module in owned if module_arguments(module["argument"]).get("sink_name") not in wanted]
        if obsolete:
            remaining = self.remove_owned(obsolete)
            self.record([module for module in owned if module not in obsolete] + remaining)
        self.resolve(players, wait=True)
        if obsolete and remaining:
            self.errors["cleanup"] = "Could not remove an obsolete app audio route"
        return self.errors

    def publish_status(self, players, errors, path=STATUS):
        """Only a verified missing device permits health to skip its waiting player."""
        identifiers = {player["id"] for player in players}
        ready = [player["id"] for player in players if "device" in player
                 and player["id"] not in errors] if set(errors).issubset(identifiers) else []
        status = {"checked_at": time.monotonic(), "ready": ready, "waiting": sorted(self.waiting),
                  "errors": {key: value for key, value in errors.items() if key not in self.waiting}}
        path = Path(path)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix="routes.")
        try:
            with os.fdopen(descriptor, "w") as target:
                json.dump(status, target)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def matches_card(sink, card, alsa_index):
        properties = sink.get("properties", {})
        if "alsa.card" in properties:
            return str(properties["alsa.card"]) == str(alsa_index)
        return sink.get("card") == card["index"]

    @staticmethod
    def channels(sink):
        channel_map = sink.get("channel_map", "")
        channels = channel_map.split(",") if isinstance(channel_map, str) else channel_map
        if not isinstance(channels, list) or not channels or any(
            not isinstance(channel, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", channel)
            for channel in channels
        ) or len(set(channels)) != len(channels):
            raise RoutingError("Could not read channel names for " + sink.get("name", "audio output"))
        return channels

def main():
    try:
        routes = AudioRoutes()
        if sys.argv[1:] == ["cleanup"]:
            routes.cleanup()
        elif len(sys.argv) == 3 and sys.argv[1] in ("prepare", "resolve", "watch"):
            players = json.loads(Path(sys.argv[2]).read_text())
            if sys.argv[1] == "prepare":
                print(json.dumps(routes.player_outputs(players)))
            elif sys.argv[1] == "resolve":
                print(json.dumps(routes.resolve(players)))
            else:
                stop = threading.Event()
                routes.stopping = stop.is_set
                for signum in (signal.SIGTERM, signal.SIGINT):
                    signal.signal(signum, lambda *_: stop.set())
                previous = None
                while not stop.is_set():
                    try:
                        errors = routes.reconcile(players)
                    except (RoutingError, OSError, ValueError) as error:
                        routes.waiting = set()
                        errors = {"audio": str(error)}
                    if stop.is_set():
                        break
                    routes.publish_status(players, errors)
                    if errors != previous:
                        for message in errors.values():
                            print("Waiting for audio: " + message, file=sys.stderr)
                        if not errors:
                            print("Configured audio routes are ready.", file=sys.stderr)
                        previous = dict(errors)
                    stop.wait(2)
        else:
            raise RoutingError("Usage: audio_routes.py prepare|resolve|watch PLAYERS_FILE | cleanup")
    except (RoutingError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
