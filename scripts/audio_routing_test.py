#!/usr/bin/env python3
"""Exercise native device routing with private PulseAudio responses, never host audio."""

import copy
import importlib.util
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import MagicMock, patch


SOURCE = Path(__file__).resolve().parents[1] / "local_audio_zones/rootfs/usr/lib/sendspin-cli/audio_routes.py"
SPEC = importlib.util.spec_from_file_location("audio_routes", SOURCE)
audio_routes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audio_routes)


class PulseFixture:
    """The JSON fields and module arguments exposed by pactl, with isolated mutations."""

    def __init__(self):
        # PulseAudio card indices are deliberately different from ALSA device indices.
        self.cards = [
            {"index": 9, "properties": {"alsa.card": "4"}},
            {"index": 17, "properties": {"alsa.card": "7"}},
        ]
        self.sinks = [
            {"index": 20, "name": "usb_card_a", "owner_module": 1,
             "description": 'USB "Card A"', "properties": {"device.class": "sound", "alsa.card": "4", "alsa.device": "0"},
             "channel_map": "front-left,front-right,rear-left,rear-right,front-center,lfe,side-left,side-right"},
            {"index": 21, "name": "usb_card_b", "owner_module": 2,
             "properties": {"device.class": "sound", "alsa.card": "7", "alsa.device": "0"}, "channel_map": "front-left,front-right"},
            {"index": 22, "name": "unrelated_default", "owner_module": 3,
             "channel_map": "front-left,front-right"},
        ]
        self.modules = []
        self.calls = []
        self.next_module = 100
        self.fail_load = None
        self.fail_unload = set()
        self.fail_module_listing = False
        self.module_text = None

    def __call__(self, *arguments):
        self.calls.append(arguments)
        if arguments == ("list", "short", "modules"):
            if self.fail_module_listing:
                raise audio_routes.RoutingError("Fixture module listing is unavailable")
            if self.module_text is not None:
                return self.module_text
            return "\n".join(f'{module["index"]}\t{module["name"]}\t{module["argument"]}\t'
                             for module in self.modules)
        if arguments[:2] == ("--format=json", "list"):
            if arguments[2] == "modules":
                raise AssertionError("Native PA module JSON has no identity field; use the short list")
            return json.dumps(getattr(self, arguments[2]))
        if arguments[0] == "load-module":
            properties = dict(token.split("=", 1) for token in shlex.split(arguments[2]))
            if properties["sink_name"] == self.fail_load:
                raise audio_routes.RoutingError("Fixture refuses the requested module")
            index = self.next_module
            self.next_module += 1
            self.modules.append({"index": index, "name": arguments[1], "argument": arguments[2]})
            self.sinks.append({"name": properties["sink_name"], "owner_module": index,
                               "channel_map": properties["channel_map"],
                               "description": properties["sink_properties"].split("=", 1)[1]})
            return str(index)
        if arguments[0] == "unload-module":
            index = int(arguments[1])
            if index in self.fail_unload:
                raise audio_routes.RoutingError("Fixture refuses unloading")
            self.modules = [module for module in self.modules if module["index"] != index]
            self.sinks = [sink for sink in self.sinks if sink.get("owner_module") != index]
            return ""
        raise AssertionError("Unexpected pactl operation: " + repr(arguments))

    def remap(self, name="existing_front", channels="front-left,front-right", remix="no", index=50):
        argument = (f"sink_name={name} master=usb_card_a channels=2 "
                    f"channel_map=front-left,front-right master_channel_map={channels} remix={remix}")
        self.modules.append({"index": index, "name": "module-remap-sink", "argument": argument})
        self.sinks.append({"name": name, "owner_module": index, "card": 9,
                           "properties": {"device.class": "filter", "device.master_device": "usb_card_a"},
                           "channel_map": "front-left,front-right", "volume": {"fixture": "80%"}})

    def mutations(self):
        return [call for call in self.calls if call[0] in ("load-module", "unload-module")]


def room(identifier="study", pair="front", device="/dev/snd/controlC4"):
    return {"id": identifier, "name": identifier.title(), "device": device,
            "channel_pair": pair, "client_id": identifier, "port": 8928,
            "buffer_ms": "250", "hook_start": "", "hook_stop": ""}


def custom_room(identifier, channels, device="/dev/snd/controlC4"):
    player = room(identifier, device=device)
    player.pop("channel_pair")
    player["channels"] = channels
    return player


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="audio-routing-")
        self.addCleanup(self.temporary.cleanup)
        self.registry = Path(self.temporary.name) / "owned.json"
        self.pulse = PulseFixture()
        devices = {"/dev/snd/controlC4": (4, None), "/dev/snd/controlC7": (7, None),
                   "/dev/snd/by-id/usb-card-a": (4, None), "/dev/snd/pcmC4D0p": (4, 0),
                   "/dev/snd/pcmC4D3p": (4, 3),
                   "/dev/snd/by-path/pci-0000:00:14.0-usb-0:1:1.0": (4, None),
                   "/dev/snd/by-path/pci-0000:00:14.0-usb-0:2:1.0": (7, None)}
        self.routes = audio_routes.AudioRoutes(pactl=self.pulse,
                                             device_resolver=devices.__getitem__,
                                             registry=self.registry)

    def test_explicit_outputs_do_not_contact_pulse_or_change_players(self):
        players = [{"id": "first", "name": "First", "output": "null"},
                   {"id": "second", "name": "Second", "output": "pulse:existing"}]
        self.assertEqual(self.routes.resolve(players), players)
        self.assertEqual(self.pulse.calls, [])
        self.assertFalse(self.registry.exists())

    def test_native_channel_lists_are_supported(self):
        self.pulse.sinks[0]["channel_map"] = ["front-left", "front-right"]
        self.assertEqual(self.routes.resolve([room()])[0]["output"], "pulse:local_audio_zones_study")

    def test_invalid_channel_maps_fail_before_creating_routes(self):
        for channel_map in (None, {}, [], "", "front-left,front-left",
                            ["front-left", 1], ["front-left", ""]):
            with self.subTest(channel_map=channel_map):
                self.pulse.sinks[0]["channel_map"] = channel_map
                with self.assertRaisesRegex(audio_routes.RoutingError, "channel names"):
                    self.routes.resolve([room()])
                self.assertEqual(self.pulse.mutations(), [])
        self.assertFalse(self.registry.exists())

    def test_short_module_list_preserves_empty_arguments_and_quoted_properties(self):
        argument = 'sink_name=existing_front master=usb_card_a sink_properties=\'device.description="Front café"\''
        self.pulse.module_text = "0\tmodule-device-restore\t\t\n50\tmodule-remap-sink\t" + argument + "\t"
        self.assertEqual(self.routes.listing("modules"),
                         [{"index": 0, "name": "module-device-restore", "argument": ""},
                          {"index": 50, "name": "module-remap-sink", "argument": argument}])

    def test_malformed_short_modules_fail_before_route_creation(self):
        malformed = ["bad\tmodule-remap-sink\targument", "1 module-remap-sink argument",
                     "1\t\targument", "1\tmodule-device-restore",
                     "1\tmodule-remap-sink\tfirst\n1\tmodule-remap-sink\tsecond"]
        for text in malformed:
            with self.subTest(text=text):
                self.pulse.module_text = text
                with self.assertRaises(audio_routes.RoutingError):
                    self.routes.resolve([room()])
                self.assertEqual(self.pulse.mutations(), [])

    def test_malformed_module_listing_keeps_cleanup_ownership(self):
        self.routes.resolve([room()])
        original = self.registry.read_text()
        self.pulse.module_text = "not a short module record"
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.cleanup()
        self.assertEqual(self.registry.read_text(), original)
        self.assertFalse(any(call[0] == "unload-module" for call in self.pulse.calls))

    def test_creates_a_stable_owned_output_without_changing_host_remaps(self):
        self.pulse.remap()
        original = copy.deepcopy(self.pulse.sinks)
        player = room()
        result = self.routes.resolve([player])[0]
        self.assertEqual(result["output"], "pulse:local_audio_zones_study")
        self.assertEqual(result["client_id"], "study")
        self.assertEqual(result["buffer_ms"], "250")
        self.assertNotIn("device", result)
        self.assertNotIn("channel_pair", result)
        self.assertEqual(player, room())
        self.assertEqual(self.pulse.sinks[:len(original)], original)
        self.assertEqual(len(self.pulse.mutations()), 1)
        self.assertEqual(len(self.routes.owned_modules()), 1)

    def test_all_stereo_pairs_map_to_the_selected_card(self):
        pairs = {"front": "front-left,front-right", "rear": "rear-left,rear-right",
                 "side": "side-left,side-right", "center_sub": "front-center,lfe"}
        players = [room(pair, pair) for pair in pairs]
        results = self.routes.resolve(players)
        self.assertEqual([player["output"] for player in results],
                         ["pulse:local_audio_zones_" + pair for pair in pairs])
        for module, pair in zip(self.pulse.modules, pairs):
            argument = module["argument"]
            self.assertIn("master=usb_card_a ", argument)
            self.assertIn("channels=2 channel_map=front-left,front-right ", argument)
            self.assertIn("master_channel_map=" + pairs[pair] + " remix=no", argument)
            self.assertEqual(len(shlex.split(argument)), 7)
            sink = next(sink for sink in self.pulse.sinks if sink["owner_module"] == module["index"])
            self.assertEqual(sink["description"], "local_audio_zones_" + pair)
        self.assertEqual(len(json.loads(self.registry.read_text())), 4)

    def test_custom_aux_channels_route_in_the_requested_order(self):
        self.pulse.sinks[0]["channel_map"] = "aux0,aux1,aux2,aux3,aux4,aux5,aux6,aux7"
        player = custom_room("custom", ["aux5", "aux4"])
        result = self.routes.resolve([player])[0]
        self.assertEqual(result["output"], "pulse:local_audio_zones_custom")
        self.assertNotIn("channels", result)
        self.assertNotIn("device", result)
        self.assertEqual(player["channels"], ["aux5", "aux4"])
        self.assertIn("master_channel_map=aux5,aux4 remix=no", self.pulse.modules[0]["argument"])

    def test_custom_channels_get_their_own_stable_route(self):
        self.pulse.remap(channels="front-right,rear-left")
        result = self.routes.resolve([custom_room("custom", ["front-right", "rear-left"])])[0]
        self.assertEqual(result["output"], "pulse:local_audio_zones_custom")
        self.assertIn("master_channel_map=front-right,rear-left", self.pulse.modules[-1]["argument"])
        self.assertEqual(self.pulse.modules[0]["index"], 50)

    def test_partial_physical_channel_overlap_is_rejected_before_creation(self):
        players = [room(), custom_room("custom", ["rear-right", "front-left"])]
        for ordered in (players, list(reversed(players))):
            with self.subTest(order=[player["id"] for player in ordered]):
                with self.assertRaisesRegex(audio_routes.RoutingError, "front-left"):
                    self.routes.resolve(ordered)
                self.assertEqual(self.pulse.mutations(), [])

    def test_by_path_distinguishes_identical_serial_soundcards(self):
        for card in self.pulse.cards:
            card["properties"]["device.serial"] = "identical-usb-card"
        players = [room(device="/dev/snd/by-path/pci-0000:00:14.0-usb-0:1:1.0"),
                   room("second", device="/dev/snd/by-path/pci-0000:00:14.0-usb-0:2:1.0")]
        self.routes.resolve(players)
        self.assertIn("master=usb_card_a ", self.pulse.modules[0]["argument"])
        self.assertIn("master=usb_card_b ", self.pulse.modules[1]["argument"])

    def test_by_path_and_control_alias_cannot_share_physical_channels(self):
        alias = room("alias", device="/dev/snd/by-path/pci-0000:00:14.0-usb-0:1:1.0")
        for players in ([alias, room()], [room(), alias]):
            with self.subTest(order=[player["id"] for player in players]):
                with self.assertRaisesRegex(audio_routes.RoutingError, "share soundcard channel"):
                    self.routes.resolve(players)
                self.assertEqual(self.pulse.mutations(), [])

    def test_device_alias_resolution_requires_a_sound_character_device(self):
        alias = "/dev/snd/by-path/pci-0000:00:14.0-usb-0:1:1.0"
        for target, character, expected in (("/dev/snd/controlC4", True, (4, None)),
                                            ("/dev/snd/pcmC4D0p", True, (4, 0)),
                                            ("/dev/snd/pcmC14D3p", True, (14, 3)),
                                            ("/dev/snd/controlC4D0p", True, None),
                                            ("/dev/snd/pcmC4", True, None),
                                            ("/dev/snd/controlC4", False, None),
                                            ("/dev/snd/pcmC4D0c", True, None),
                                            ("/dev/null", True, None)):
            resolved = MagicMock()
            resolved.__str__.return_value = target
            resolved.is_char_device.return_value = character
            with self.subTest(target=target, character=character), \
                    patch.object(Path, "resolve", return_value=resolved):
                if expected is None:
                    with self.assertRaises(audio_routes.RoutingError):
                        audio_routes.device_endpoint(alias)
                else:
                    self.assertEqual(audio_routes.device_endpoint(alias), expected)

    def test_custom_channels_report_the_actual_available_map(self):
        self.pulse.sinks[0]["channel_map"] = "aux0,aux1,aux2,aux3"
        with self.assertRaisesRegex(audio_routes.RoutingError, "available channels: aux0, aux1, aux2, aux3"):
            self.routes.resolve([custom_room("custom", ["aux4", "aux5"])])
        self.assertEqual(self.pulse.mutations(), [])

    def test_explicit_remap_overlapping_a_selected_device_is_rejected(self):
        self.pulse.remap("expert_route", channels="front-left,rear-right")
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:expert_route"}
        with self.assertRaisesRegex(audio_routes.RoutingError, "front-left"):
            self.routes.resolve([room(), explicit])
        self.assertEqual(self.pulse.mutations(), [])

    def test_explicit_nonoverlapping_host_remap_is_preserved(self):
        self.pulse.remap("expert_route", channels="rear-left,rear-right")
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:expert_route"}
        result = self.routes.resolve([room(), explicit])
        self.assertEqual(result[1], explicit)
        self.routes.cleanup()
        self.assertEqual([module["index"] for module in self.pulse.modules], [50])

    def test_explicit_implicit_channel_map_cannot_overlap_a_device_room(self):
        self.pulse.remap("implicit_front")
        self.pulse.modules[0]["argument"] = self.pulse.modules[0]["argument"].replace(
            " master_channel_map=front-left,front-right", "")
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:implicit_front"}
        for players in ([room(), explicit], [explicit, room()]):
            with self.subTest(order=[player["id"] for player in players]):
                with self.assertRaisesRegex(audio_routes.RoutingError, "front-left"):
                    self.routes.resolve(players)
                self.assertEqual(self.pulse.mutations(), [])

    def test_explicit_implicit_channel_map_allows_a_nonoverlapping_device_room(self):
        self.pulse.remap("implicit_front")
        self.pulse.modules[0]["argument"] = self.pulse.modules[0]["argument"].replace(
            " master_channel_map=front-left,front-right", "")
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:implicit_front"}
        result = self.routes.resolve([room(pair="rear"), explicit])
        self.assertEqual(result[1], explicit)
        self.routes.cleanup()
        self.assertEqual([module["index"] for module in self.pulse.modules], [50])

    def test_explicit_default_master_cannot_overlap_a_device_room(self):
        self.pulse.remap("default_front")
        self.pulse.modules[0]["argument"] = self.pulse.modules[0]["argument"].replace(" master=usb_card_a", "")
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:default_front"}
        for players in ([room(), explicit], [explicit, room()]):
            with self.subTest(order=[player["id"] for player in players]):
                with self.assertRaisesRegex(audio_routes.RoutingError, "front-left"):
                    self.routes.resolve(players)
                self.assertEqual(self.pulse.mutations(), [])

    def test_explicit_default_master_allows_a_nonoverlapping_device_room(self):
        self.pulse.remap("default_front")
        self.pulse.modules[0]["argument"] = self.pulse.modules[0]["argument"].replace(" master=usb_card_a", "")
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:default_front"}
        result = self.routes.resolve([room(pair="rear"), explicit])
        self.assertEqual(result[1], explicit)
        self.routes.cleanup()
        self.assertEqual([module["index"] for module in self.pulse.modules], [50])

    def test_explicit_unknown_master_is_rejected_before_route_creation(self):
        self.pulse.remap("unknown_front")
        self.pulse.modules[0]["argument"] = self.pulse.modules[0]["argument"].replace(" master=usb_card_a", "")
        self.pulse.sinks[-1]["properties"].pop("device.master_device")
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:unknown_front"}
        with self.assertRaisesRegex(audio_routes.RoutingError, "cannot identify"):
            self.routes.resolve([room(pair="rear"), explicit])
        self.assertEqual(self.pulse.mutations(), [])

    def test_explicit_nested_remap_is_rejected_without_traversing_filters(self):
        self.pulse.remap("inner_front")
        self.pulse.remap("outer_front", index=51)
        self.pulse.modules[-1]["argument"] = self.pulse.modules[-1]["argument"].replace(
            "master=usb_card_a", "master=inner_front")
        self.pulse.sinks[-1]["properties"]["device.master_device"] = "inner_front"
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:outer_front"}
        with self.assertRaisesRegex(audio_routes.RoutingError, "nested remaps"):
            self.routes.resolve([room(pair="rear"), explicit])
        self.assertEqual(self.pulse.mutations(), [])

    def test_explicit_remixing_routes_cannot_share_a_device_selected_master(self):
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:expert_route"}
        for remix in ("yes", None):
            with self.subTest(remix=remix):
                self.pulse.modules.clear()
                self.pulse.sinks = self.pulse.sinks[:3]
                self.pulse.remap("expert_route", channels="rear-left,rear-right", remix="yes")
                if remix is None:
                    self.pulse.modules[0]["argument"] = self.pulse.modules[0]["argument"].replace(" remix=yes", "")
                for players in ([room(), explicit], [explicit, room()]):
                    with self.assertRaisesRegex(audio_routes.RoutingError, "remix=no"):
                        self.routes.resolve(players)
                    self.assertEqual(self.pulse.mutations(), [])

    def test_explicit_remixing_route_on_another_master_is_unchanged(self):
        self.pulse.remap("expert_route", remix="yes")
        self.pulse.modules[0]["argument"] = self.pulse.modules[0]["argument"].replace("master=usb_card_a", "master=usb_card_b")
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:expert_route"}
        result = self.routes.resolve([room(), explicit])
        self.assertEqual(result[1], explicit)
        self.routes.cleanup()
        self.assertEqual([module["index"] for module in self.pulse.modules], [50])

    def test_explicit_only_remixing_route_stays_an_expert_configuration(self):
        self.pulse.remap("expert_route", remix="yes")
        players = [{"id": "expert", "name": "Expert", "output": "pulse:expert_route"}]
        self.assertEqual(self.routes.resolve(players), players)
        self.assertEqual(self.pulse.calls, [])

    def test_explicit_physical_master_cannot_overlap_a_selected_device(self):
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:usb_card_a"}
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room(), explicit])
        self.assertEqual(self.pulse.mutations(), [])

    def test_second_device_uses_its_own_pulse_card_index(self):
        self.routes.resolve([room(device="/dev/snd/controlC7")])
        self.assertIn("master=usb_card_b ", self.pulse.modules[0]["argument"])

    def test_native_pulse_sink_without_card_field_uses_alsa_card_property(self):
        self.assertNotIn("card", self.pulse.sinks[0])
        self.routes.resolve([room()])
        self.assertIn("master=usb_card_a ", self.pulse.modules[0]["argument"])

    def test_pulse_card_field_is_supported_when_alsa_property_is_absent(self):
        self.pulse.sinks[0]["properties"].pop("alsa.card")
        self.pulse.sinks[0]["card"] = 9
        self.routes.resolve([room()])
        self.assertIn("master=usb_card_a ", self.pulse.modules[0]["argument"])

    def test_conflicting_alsa_property_overrides_a_misleading_card_field(self):
        self.pulse.sinks[0]["properties"]["alsa.card"] = "7"
        self.pulse.sinks[0]["card"] = 9
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room()])
        self.assertEqual(self.pulse.mutations(), [])

    def test_unknown_sink_card_identity_never_uses_the_default_output(self):
        self.pulse.sinks[0]["properties"].pop("alsa.card")
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room()])
        self.assertEqual(self.pulse.mutations(), [])

    def test_duplicate_serial_rejects_a_stable_alias_before_mutation(self):
        for card in self.pulse.cards:
            card["properties"]["device.serial"] = "shared-usb-serial"
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room(device="/dev/snd/by-id/usb-card-a")])
        self.assertEqual(self.pulse.mutations(), [])

    def test_duplicate_serial_allows_explicit_current_card_selections(self):
        for card in self.pulse.cards:
            card["properties"]["device.serial"] = "shared-usb-serial"
        result = self.routes.resolve([room(), room("second", device="/dev/snd/controlC7")])
        self.assertNotEqual(result[0]["output"], result[1]["output"])
        self.assertIn("master=usb_card_a ", self.pulse.modules[0]["argument"])
        self.assertIn("master=usb_card_b ", self.pulse.modules[1]["argument"])

    def test_empty_serial_does_not_invent_a_duplicate_identity(self):
        for card in self.pulse.cards:
            card["properties"]["device.serial"] = ""
        self.routes.resolve([room(device="/dev/snd/by-id/usb-card-a")])
        self.assertIn("master=usb_card_a ", self.pulse.modules[0]["argument"])

    def test_stable_device_alias_and_playback_device_resolve_the_same_card(self):
        self.pulse.remap()
        result = self.routes.resolve([room("alias", device="/dev/snd/by-id/usb-card-a"),
                                      room("playback", pair="rear", device="/dev/snd/pcmC4D0p")])
        self.assertEqual(result[0]["output"], "pulse:local_audio_zones_alias")
        self.assertIn("master=usb_card_a ", self.pulse.modules[-1]["argument"])

    def test_duplicate_card_and_pair_aliases_are_rejected_before_creation(self):
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room("first"), room("alias", device="/dev/snd/by-id/usb-card-a")])
        self.assertEqual(self.pulse.mutations(), [])
        self.assertFalse(self.registry.exists())

    def test_device_route_colliding_with_an_explicit_output_is_rejected(self):
        self.pulse.remap()
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room(), {"id": "other", "name": "Other", "output": "pulse:existing_front"}])
        self.assertEqual(self.pulse.mutations(), [])

    def test_missing_card_does_not_fall_back_to_an_unrelated_sink(self):
        self.pulse.cards = []
        with self.assertRaisesRegex(audio_routes.RoutingError, "no unique PulseAudio card"):
            self.routes.resolve([room()])
        self.assertEqual(self.pulse.mutations(), [])

    def test_duplicate_card_identity_is_rejected(self):
        self.pulse.cards.append({"index": 33, "properties": {"alsa.card": "4"}})
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room()])
        self.assertEqual(self.pulse.mutations(), [])

    def test_unsupported_pair_on_stereo_device_has_an_actionable_error(self):
        with self.assertRaisesRegex(audio_routes.RoutingError, "Audio settings"):
            self.routes.resolve([room(pair="rear", device="/dev/snd/controlC7")])
        self.assertEqual(self.pulse.mutations(), [])

    def test_validates_all_devices_before_creating_any_routes(self):
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room(), room("missing", "side", "/dev/snd/controlC7")])
        self.assertEqual(self.pulse.mutations(), [])

    def test_ambiguous_physical_outputs_are_rejected(self):
        alternative = dict(self.pulse.sinks[0], name="another_output")
        self.pulse.sinks.append(alternative)
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room()])
        self.assertEqual(self.pulse.mutations(), [])

    def second_pcm(self):
        sink = copy.deepcopy(self.pulse.sinks[0])
        sink.update(name="digital_output", index=30, channel_map="front-left,front-right")
        sink["properties"]["alsa.device"] = "3"
        self.pulse.sinks.append(sink)

    def test_playback_node_selects_the_exact_pcm_on_a_multi_pcm_card(self):
        self.second_pcm()
        self.routes.resolve([room("analog", device="/dev/snd/pcmC4D0p"),
                             room("digital", device="/dev/snd/pcmC4D3p")])
        self.assertIn("master=usb_card_a ", self.pulse.modules[0]["argument"])
        self.assertIn("master=digital_output ", self.pulse.modules[1]["argument"])

    def test_missing_pcm_does_not_use_another_output_on_the_card(self):
        for metadata in ("0", None):
            with self.subTest(metadata=metadata):
                self.pulse.sinks[0]["properties"]["alsa.device"] = metadata
                with self.assertRaisesRegex(audio_routes.RoutingError, "PCM 3 has no active"):
                    self.routes.resolve([room(device="/dev/snd/pcmC4D3p")])
                self.assertEqual(self.pulse.mutations(), [])

    def test_selected_pcm_does_not_take_channels_from_a_different_pcm(self):
        self.second_pcm()
        with self.assertRaisesRegex(audio_routes.RoutingError, "available channels: front-left, front-right"):
            self.routes.resolve([room(pair="rear", device="/dev/snd/pcmC4D3p")])
        self.assertEqual(self.pulse.mutations(), [])

    def test_control_node_lists_ambiguous_pcm_outputs(self):
        self.second_pcm()
        with self.assertRaisesRegex(audio_routes.RoutingError, "usb_card_a, digital_output"):
            self.routes.resolve([room()])
        self.assertEqual(self.pulse.mutations(), [])

    def test_playback_node_does_not_guess_between_sinks_on_the_same_pcm(self):
        self.second_pcm()
        self.pulse.sinks[-1]["properties"]["alsa.device"] = 0
        with self.assertRaisesRegex(audio_routes.RoutingError, "more than one active output"):
            self.routes.resolve([room(device="/dev/snd/pcmC4D0p")])
        self.assertEqual(self.pulse.mutations(), [])

    def test_control_node_can_select_a_unique_channel_pair_on_a_multi_pcm_card(self):
        self.second_pcm()
        self.routes.resolve([room(pair="rear")])
        self.assertIn("master=usb_card_a ", self.pulse.modules[0]["argument"])

    def test_multiple_host_remaps_do_not_change_the_owned_route_identity(self):
        self.pulse.remap()
        self.pulse.remap("another_front", index=51)
        result = self.routes.resolve([room()])
        self.assertEqual(result[0]["output"], "pulse:local_audio_zones_study")
        self.assertEqual([module["index"] for module in self.pulse.modules[:2]], [50, 51])
        self.assertEqual(len(self.pulse.mutations()), 1)

    def test_remixing_route_is_not_reused(self):
        self.pulse.remap(remix="yes")
        result = self.routes.resolve([room()])[0]
        self.assertEqual(result["output"], "pulse:local_audio_zones_study")
        self.assertEqual(len(self.pulse.modules), 2)

    def test_conflicting_sink_name_is_not_overwritten(self):
        self.pulse.sinks.append({"name": "local_audio_zones_study"})
        with self.assertRaisesRegex(audio_routes.RoutingError, "already in use"):
            self.routes.resolve([room()])
        self.assertEqual(self.pulse.mutations(), [])

    def test_failed_creation_rolls_back_only_new_modules(self):
        self.pulse.remap()
        self.pulse.fail_load = "local_audio_zones_side"
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room("rear", "rear"), room("side", "side")])
        self.assertEqual([module["index"] for module in self.pulse.modules], [50])
        self.assertEqual(json.loads(self.registry.read_text()), [])

    def test_cleanup_preserves_host_remaps_and_unloads_only_owned_routes(self):
        self.pulse.remap()
        self.routes.resolve([room("rear", "rear")])
        self.routes.cleanup()
        self.assertEqual([module["index"] for module in self.pulse.modules], [50])
        self.assertEqual(json.loads(self.registry.read_text()), [])
        self.routes.cleanup()

    def test_container_replacement_preserves_owned_routes_for_cleanup(self):
        self.pulse.remap("host_rear", channels="rear-left,rear-right")
        first = self.routes.resolve([room()])
        replacement = audio_routes.AudioRoutes(pactl=self.pulse, device_resolver=lambda _: (4, None),
                                               registry=self.registry)
        self.assertEqual(replacement.resolve([room()]), first)
        self.assertEqual(len([call for call in self.pulse.calls if call[0] == "load-module"]), 1)
        replacement.cleanup()
        self.assertEqual([module["index"] for module in self.pulse.modules], [50])
        self.assertEqual(json.loads(self.registry.read_text()), [])

    def test_interrupted_cleanup_is_completed_after_container_replacement(self):
        self.routes.resolve([room("rear", "rear"), room("side", "side")])
        self.pulse.fail_unload = {100}
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.cleanup()
        self.assertEqual([record["index"] for record in json.loads(self.registry.read_text())], [100])
        replacement = audio_routes.AudioRoutes(pactl=self.pulse, device_resolver=lambda _: (4, None),
                                               registry=self.registry)
        self.pulse.fail_unload.clear()
        replacement.cleanup()
        self.assertEqual(self.pulse.modules, [])
        self.assertEqual(json.loads(self.registry.read_text()), [])

    def test_cold_start_cleanup_allows_changed_channels_for_the_same_room(self):
        self.routes.resolve([room()])
        replacement = audio_routes.AudioRoutes(pactl=self.pulse, device_resolver=lambda _: (4, None),
                                               registry=self.registry)
        replacement.cleanup()
        result = replacement.resolve([room(pair="rear")])
        self.assertEqual(result[0]["output"], "pulse:local_audio_zones_study")
        self.assertEqual(len(self.pulse.modules), 1)
        self.assertIn("master_channel_map=rear-left,rear-right", self.pulse.modules[0]["argument"])

    def test_cold_start_cleanup_removes_owned_routes_when_all_rooms_are_deleted(self):
        self.pulse.remap("host_rear", channels="rear-left,rear-right")
        self.routes.resolve([room()])
        replacement = audio_routes.AudioRoutes(pactl=self.pulse, device_resolver=lambda _: (4, None),
                                               registry=self.registry)
        replacement.cleanup()
        self.assertEqual(replacement.resolve([]), [])
        self.assertEqual([module["index"] for module in self.pulse.modules], [50])
        self.assertEqual(json.loads(self.registry.read_text()), [])

    def test_recycled_module_id_is_not_unloaded(self):
        self.routes.resolve([room()])
        self.pulse.modules[0]["argument"] = "sink_name=someone_else"
        self.routes.cleanup()
        self.assertEqual(len(self.pulse.modules), 1)
        self.assertFalse(any(call[0] == "unload-module" for call in self.pulse.calls))
        self.assertEqual(json.loads(self.registry.read_text()), [])

    def test_failed_cleanup_retains_ownership_for_retry(self):
        self.routes.resolve([room()])
        self.pulse.fail_unload = {100}
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.cleanup()
        self.assertEqual(len(json.loads(self.registry.read_text())), 1)
        self.pulse.fail_unload.clear()
        self.routes.cleanup()
        self.assertEqual(self.pulse.modules, [])

    def test_corrupt_ownership_record_fails_without_unloading_anything(self):
        self.registry.write_text('[{"index":100}]')
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.cleanup()
        self.assertEqual(self.pulse.calls, [])

    def test_cleanup_retains_ownership_when_module_listing_is_unavailable(self):
        self.routes.resolve([room()])
        original = self.registry.read_text()
        self.pulse.fail_module_listing = True
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.cleanup()
        self.assertEqual(self.registry.read_text(), original)
        self.assertFalse(any(call[0] == "unload-module" for call in self.pulse.calls))

    def test_rollback_checks_ownership_again_after_a_pulse_restart(self):
        def restart_on_second_load(*arguments):
            if arguments[0] == "load-module" and "sink_name=local_audio_zones_side " in arguments[2]:
                self.pulse.modules[0]["argument"] = "sink_name=unrelated_after_restart"
                raise audio_routes.RoutingError("Fixture restarted during creation")
            return self.pulse(*arguments)

        self.routes.pactl = restart_on_second_load
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room("rear", "rear"), room("side", "side")])
        self.assertEqual(self.pulse.modules[0]["argument"], "sink_name=unrelated_after_restart")
        self.assertFalse(any(call[0] == "unload-module" for call in self.pulse.calls))
        self.assertEqual(json.loads(self.registry.read_text()), [])

    def test_rollback_retains_ownership_when_pulse_stops_answering(self):
        def fail_after_first_load(*arguments):
            if arguments[0] == "load-module" and "sink_name=local_audio_zones_side " in arguments[2]:
                self.pulse.fail_module_listing = True
                raise audio_routes.RoutingError("Fixture PulseAudio is unavailable")
            return self.pulse(*arguments)

        self.routes.pactl = fail_after_first_load
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.resolve([room("rear", "rear"), room("side", "side")])
        self.assertEqual(len(json.loads(self.registry.read_text())), 1)
        self.assertFalse(any(call[0] == "unload-module" for call in self.pulse.calls))
        self.pulse.fail_module_listing = False
        self.routes.cleanup()
        self.assertEqual(self.pulse.modules, [])

    def lose_card_a(self):
        self.pulse.cards = [card for card in self.pulse.cards if card["index"] != 9]
        owned_ids = {module["index"] for module in self.pulse.modules
                     if "master=usb_card_a " in module["argument"]}
        self.pulse.modules = [module for module in self.pulse.modules if module["index"] not in owned_ids]
        self.pulse.sinks = [sink for sink in self.pulse.sinks
                            if sink["name"] != "usb_card_a" and sink.get("owner_module") not in owned_ids]

    def test_player_outputs_are_fixed_without_contacting_missing_hardware(self):
        player = custom_room("study", ["rear-right", "rear-left"])
        result = self.routes.player_outputs([player])
        self.assertEqual(result[0]["output"], "pulse:local_audio_zones_study")
        self.assertNotIn("device", result[0])
        self.assertNotIn("channels", result[0])
        self.assertEqual(player["channels"], ["rear-right", "rear-left"])
        self.assertEqual(self.pulse.calls, [])

    def test_fixed_output_collision_fails_before_pulse_mutation(self):
        explicit = {"id": "expert", "name": "Expert", "output": "pulse:local_audio_zones_study"}
        with self.assertRaisesRegex(audio_routes.RoutingError, "same audio output"):
            self.routes.reconcile([room(), explicit])
        self.assertEqual(self.pulse.calls, [])

    def test_missing_card_waits_while_an_independent_room_recovers(self):
        self.lose_card_a()
        players = [room(), room("guest", device="/dev/snd/controlC7")]
        errors = self.routes.reconcile(players)
        self.assertEqual(set(errors), {"study"})
        self.assertEqual(self.routes.waiting, {"study"})
        status = Path(self.temporary.name) / "status.json"
        self.routes.publish_status(players, errors, status)
        self.assertEqual(json.loads(status.read_text()),
                         {"ready": ["guest"], "waiting": ["study"], "errors": {}})
        self.assertEqual(len(self.pulse.modules), 1)
        self.assertIn("sink_name=local_audio_zones_guest ", self.pulse.modules[0]["argument"])

    def test_invalid_routing_is_not_hidden_as_missing_hardware(self):
        players = [room(pair="rear", device="/dev/snd/controlC7")]
        errors = self.routes.reconcile(players)
        self.assertEqual(self.routes.waiting, set())
        status = Path(self.temporary.name) / "status.json"
        self.routes.publish_status(players, errors, status)
        self.assertEqual(json.loads(status.read_text())["errors"], errors)
        self.assertEqual(json.loads(status.read_text())["ready"], [])

    def test_global_pulse_failure_never_publishes_ready_or_waiting_rooms(self):
        status = Path(self.temporary.name) / "status.json"
        self.routes.publish_status([room()], {"audio": "PulseAudio unavailable"}, status)
        self.assertEqual(json.loads(status.read_text()),
                         {"ready": [], "waiting": [], "errors": {"audio": "PulseAudio unavailable"}})

    def test_repeated_outages_recover_only_the_affected_stable_output(self):
        master = copy.deepcopy(self.pulse.sinks[0])
        players = [room(device="/dev/snd/by-path/pci-0000:00:14.0-usb-0:1:1.0"),
                   room("guest", device="/dev/snd/controlC7")]
        index = 4
        self.routes.device_resolver = lambda device: (7 if device.endswith("C7") else index, None)
        self.assertEqual(self.routes.reconcile(players), {})
        guest = next(sink for sink in self.pulse.sinks if sink["name"] == "local_audio_zones_guest")
        guest.update(volume={"fixture": "61%"}, mute=True)
        preserved = copy.deepcopy(guest)
        for index in (14, 24):
            self.lose_card_a()
            self.assertEqual(set(self.routes.reconcile(players)), {"study"})
            self.pulse.cards.append({"index": 9, "properties": {"alsa.card": str(index)}})
            master["properties"]["alsa.card"] = str(index)
            self.pulse.sinks.append(copy.deepcopy(master))
            self.assertEqual(self.routes.reconcile(players), {})
            owned = self.routes.owned_modules()
            self.assertEqual(len(owned), 2)
            self.assertEqual(next(sink for sink in self.pulse.sinks
                                  if sink["name"] == "local_audio_zones_guest"), preserved)
            self.assertEqual(sum("sink_name=local_audio_zones_study " in module["argument"]
                                 for module in self.pulse.modules), 1)
        self.assertFalse(any(call[0].startswith("set-") for call in self.pulse.calls))

    def test_healthy_worker_restart_keeps_owned_modules_volume_and_mute(self):
        players = [room()]
        self.routes.reconcile(players)
        self.pulse.sinks[-1].update(volume={"fixture": "80%"}, mute=True)
        original = copy.deepcopy(self.pulse.sinks[-1])
        mutations = self.pulse.mutations()[:]
        replacement = audio_routes.AudioRoutes(pactl=self.pulse, device_resolver=lambda _: (4, None),
                                               registry=self.registry)
        for _ in range(3):
            self.assertEqual(replacement.reconcile(players), {})
        self.assertEqual(self.pulse.sinks[-1], original)
        self.assertEqual(self.pulse.mutations(), mutations)

    def test_one_failed_route_does_not_roll_back_an_independent_recovery(self):
        self.pulse.fail_load = "local_audio_zones_study"
        players = [room(), room("guest", device="/dev/snd/controlC7")]
        errors = self.routes.reconcile(players)
        self.assertEqual(set(errors), {"study"})
        self.assertEqual(self.routes.waiting, set())
        self.assertEqual(len(self.routes.owned_modules()), 1)
        self.assertIn("sink_name=local_audio_zones_guest ", self.pulse.modules[0]["argument"])

    def test_removed_rooms_clean_only_their_exact_owned_modules(self):
        self.pulse.remap()
        players = [room("rear", "rear"), room("side", "side")]
        self.routes.reconcile(players)
        side = copy.deepcopy(self.pulse.modules[-1])
        self.assertEqual(self.routes.reconcile([players[1]]), {})
        self.assertEqual(self.pulse.modules, [self.pulse.modules[0], side])
        self.assertEqual(self.routes.owned_modules(), [side])
        self.assertEqual(self.routes.reconcile([]), {})
        self.assertEqual([module["index"] for module in self.pulse.modules], [50])

    def test_pulse_listing_outage_retains_ownership_until_recovery(self):
        self.routes.reconcile([room()])
        original = self.registry.read_text()
        self.pulse.fail_module_listing = True
        with self.assertRaises(audio_routes.RoutingError):
            self.routes.reconcile([room()])
        self.assertEqual(self.registry.read_text(), original)
        self.pulse.fail_module_listing = False
        self.assertEqual(self.routes.reconcile([room()]), {})
        self.assertEqual(len(self.pulse.mutations()), 1)

    def test_recycled_foreign_module_with_our_sink_name_is_not_unloaded(self):
        self.routes.reconcile([room()])
        self.pulse.modules[-1]["argument"] += " foreign=yes"
        errors = self.routes.reconcile([room()])
        self.assertIn("already in use", errors["study"])
        self.assertEqual(self.routes.waiting, set())
        self.assertFalse(any(call[0] == "unload-module" for call in self.pulse.calls))

    def test_foreign_collision_does_not_prevent_an_unrelated_room(self):
        self.pulse.sinks.append({"name": "local_audio_zones_study"})
        errors = self.routes.reconcile([room(), room("guest", device="/dev/snd/controlC7")])
        self.assertEqual(set(errors), {"study"})
        self.assertEqual(self.routes.waiting, set())
        self.assertIn("sink_name=local_audio_zones_guest ", self.pulse.modules[-1]["argument"])
        self.assertFalse(any(call[0] == "unload-module" for call in self.pulse.calls))

    def test_shutdown_during_creation_records_that_route_and_starts_no_more(self):
        stopped = False
        def stop_during_load(*arguments):
            nonlocal stopped
            result = self.pulse(*arguments)
            if arguments[0] == "load-module":
                stopped = True
            return result
        self.routes.pactl = stop_during_load
        self.routes.stopping = lambda: stopped
        self.routes.reconcile([room("rear", "rear"), room("side", "side")])
        self.assertEqual(len(self.pulse.modules), 1)
        self.assertEqual(self.routes.owned_modules(), self.pulse.modules)
        self.assertIn("sink_name=local_audio_zones_rear ", self.pulse.modules[0]["argument"])
        self.assertEqual(len(self.pulse.mutations()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
