"""Resolve native sound-device selections into named PulseAudio stereo outputs."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


PAIRS = {
    "front": ("front-left", "front-right"),
    "rear": ("rear-left", "rear-right"),
    "side": ("side-left", "side-right"),
    "center_sub": ("front-center", "lfe"),
}
REGISTRY = Path("/data/audio-routes.json")


class RoutingError(Exception):
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


def device_card(device):
    try:
        path = Path(device).resolve(strict=True)
    except OSError as error:
        raise RoutingError("Selected sound device is unavailable: " + device) from error
    match = re.fullmatch(r"/dev/snd/(?:controlC|pcmC)([0-9]+)(?:D[0-9]+p)?", str(path))
    if not match or not path.is_char_device():
        raise RoutingError("Select a soundcard control or playback device, not a capture, sequencer or timer device")
    return int(match[1])


def module_arguments(argument):
    try:
        return dict(item.split("=", 1) for item in shlex.split(argument) if "=" in item)
    except ValueError:
        return {}


class AudioRoutes:
    def __init__(self, pactl=pactl, device_resolver=device_card, registry=REGISTRY):
        self.pactl = pactl
        self.device_resolver = device_resolver
        self.registry = Path(registry)

    def listing(self, kind):
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
            try:
                current = {item["index"]: item for item in self.listing("modules")}
            except RoutingError:
                return remaining + owned[position:]
            found = current.get(module["index"])
            if found and all(found.get(key) == value for key, value in module.items()):
                try:
                    self.pactl("unload-module", str(module["index"]))
                except RoutingError:
                    remaining.append(module)
        return remaining

    def select_output(self, player, cards, sinks):
        index = self.device_resolver(player["device"])
        matching_cards = [card for card in cards
                          if str(card.get("properties", {}).get("alsa.card", "")) == str(index)]
        if len(matching_cards) != 1:
            raise RoutingError(player["name"] + ": selected soundcard has no unique PulseAudio card")
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
        masters = [sink for sink in card_sinks if set(channels).issubset(self.channels(sink))]
        if len(masters) != 1:
            available = list(dict.fromkeys(channel for sink in card_sinks for channel in self.channels(sink)))
            raise RoutingError(player["name"] + ": requested " + ", ".join(channels)
                               + "; available channels: " + (", ".join(available) or "none")
                               + ". Choose two channels on one output or a suitable profile in Home Assistant's Audio settings")
        return masters[0], channels

    def resolve(self, players):
        if not any("device" in player for player in players):
            return players
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
            master, channels = self.select_output(player, cards, sinks)
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

        owned = self.owned_modules()
        created = []
        result = []
        reported_masters = set()
        selected_outputs = {player["output"]: player["name"] for player in players if "output" in player}
        try:
            for player, master, channels in routes:
                player = dict(player)
                if master is not None:
                    if master["name"] not in reported_masters:
                        description = master.get("description") or master["name"]
                        print("Soundcard " + description + ": available channels "
                              + ", ".join(self.channels(master)), file=sys.stderr)
                        reported_masters.add(master["name"])
                    target = self.existing_remap(master, channels, sinks, modules)
                    output = "pulse:" + (target or "local_audio_zones_" + player["id"])
                    if output in selected_outputs:
                        raise RoutingError(player["name"] + " and " + selected_outputs[output]
                                           + " select the same PulseAudio output")
                    selected_outputs[output] = player["name"]
                    if target is None:
                        target = "local_audio_zones_" + player["id"]
                        if any(sink["name"] == target for sink in sinks):
                            raise RoutingError(player["name"] + ": the generated output name is already in use")
                        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", master["name"]):
                            raise RoutingError("Unsupported PulseAudio master name")
                        argument = (
                            "sink_name=" + target + " master=" + master["name"]
                            + " channels=2 channel_map=front-left,front-right master_channel_map="
                            + ",".join(channels) + " remix=no sink_properties=device.description=" + target
                        )
                        loaded = self.pactl("load-module", "module-remap-sink", argument)
                        if not re.fullmatch(r"[0-9]+", loaded):
                            raise RoutingError("PulseAudio returned an invalid module id")
                        module = {"index": int(loaded), "name": "module-remap-sink", "argument": argument}
                        created.append(module)
                        self.record(owned + created)
                        modules.append(module)
                        sinks.append({"name": target, "owner_module": int(loaded), "channel_map": "front-left,front-right"})
                    player.pop("device")
                    player.pop("channel_pair", None)
                    player.pop("channels", None)
                    player["output"] = "pulse:" + target
                    print(player["name"] + ": using " + player["output"], file=sys.stderr)
                result.append(player)
        except Exception:
            # Roll back only routes made by this attempt; host remaps remain untouched.
            try:
                created = self.remove_owned(created) if created else []
            except RoutingError:
                pass
            self.record(owned + created)
            raise
        return result

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

    @staticmethod
    def existing_remap(master, channels, sinks, modules):
        matches = []
        for module in modules:
            if module.get("name") != "module-remap-sink":
                continue
            arguments = module_arguments(module.get("argument", ""))
            if (arguments.get("master") == master["name"]
                    and arguments.get("channels") == "2"
                    and arguments.get("channel_map") == "front-left,front-right"
                    and arguments.get("master_channel_map") == ",".join(channels)
                    and arguments.get("remix") == "no"):
                matches.extend(sink["name"] for sink in sinks
                               if sink.get("owner_module") == module["index"]
                               and AudioRoutes.channels(sink) == ["front-left", "front-right"])
        if len(matches) > 1:
            raise RoutingError("More than one existing stereo output maps to the selected channel pair")
        return matches[0] if matches else None


def main():
    try:
        routes = AudioRoutes()
        if sys.argv[1:] == ["cleanup"]:
            routes.cleanup()
        elif len(sys.argv) == 3 and sys.argv[1] == "resolve":
            players = json.loads(Path(sys.argv[2]).read_text())
            print(json.dumps(routes.resolve(players)))
        else:
            raise RoutingError("Usage: audio_routes.py resolve PLAYERS_FILE | cleanup")
    except (RoutingError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
