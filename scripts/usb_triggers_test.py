#!/usr/bin/env python3
"""Test relay control without accessing household USB devices or audio."""

import copy
import importlib.util
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "local_audio_zones/rootfs/usr/lib/sendspin-cli/usb_triggers.py"
SPEC = importlib.util.spec_from_file_location("usb_triggers", SOURCE)
relay = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = relay
SPEC.loader.exec_module(relay)

PLAYERS = [{"id": "study"}, {"id": "bedroom"}]
ENTRY = {"name": "Amplifier", "device": "/dev/serial/by-id/usb-test-relay",
         "protocol": "DSD TECH SH-UR01A", "zones": ["study", "bedroom"]}


class ConfigurationTests(unittest.TestCase):
    def config(self, entries):
        return relay.configuration({"usb_relays": entries}, PLAYERS)

    def test_disabled_by_default_and_shared_amplifier_defaults(self):
        self.assertEqual(relay.configuration({}, PLAYERS), [])
        trigger, = self.config([ENTRY])
        self.assertEqual(trigger.zones, ("study", "bedroom"))
        self.assertEqual((trigger.channel, trigger.off_delay), (1, 60))

    def test_two_channels_can_share_one_board_and_room(self):
        entries = [dict(ENTRY, protocol="LCUS", channel=1),
                   dict(ENTRY, protocol="LCUS", channel=8, zones=["study"])]
        self.assertEqual([item.channel for item in self.config(entries)], [1, 8])

    def test_explicit_usb_device_paths(self):
        for device in ("/dev/ttyUSB3", "/dev/ttyACM12", "/dev/serial/by-path/pci-usb-relay"):
            with self.subTest(device=device):
                self.assertEqual(self.config([dict(ENTRY, device=device)])[0].device, device)

    def test_rejects_invalid_fields_before_opening_hardware(self):
        bad = [
            {"name": ""}, {"name": "Bad\nName"}, {"name": "Bad\x7fName"},
            {"device": "/dev/ttyS0"}, {"device": "/dev/serial/by-id/../ttyUSB0"},
            {"device": "/dev/serial/by-id/.."}, {"device": "/dev/ttyUSB0 "},
            {"device": "/tmp/relay"}, {"protocol": "automatic"},
            {"channel": True}, {"channel": 0}, {"channel": 2}, {"channel": "1"},
            {"off_delay": True}, {"off_delay": -1}, {"off_delay": 3601},
            {"off_delay": 0.5}, {"zones": []}, {"zones": "study"},
            {"zones": ["missing"]}, {"zones": ["study", "study"]},
            {"zones": [True]}, {"startup_command": "shell command"},
        ]
        for fields in bad:
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    self.config([dict(ENTRY, **fields)])

    def test_rejects_bad_collection_shape_and_limit(self):
        for entries in (None, {}, "relay", [None], [ENTRY] * 17):
            with self.subTest(entries=entries):
                with self.assertRaises(ValueError):
                    self.config(entries)

    def test_rejects_duplicate_channel_and_conflicting_protocol(self):
        for second in (ENTRY, dict(ENTRY, protocol="KMtronic")):
            with self.subTest(second=second):
                with self.assertRaises(ValueError):
                    self.config([ENTRY, second])

    def test_aliases_of_same_physical_port_cannot_be_separate_boards(self):
        second = dict(ENTRY, device="/dev/ttyUSB0", channel=2, protocol="LCUS")
        with patch.object(relay.os.path, "realpath", return_value="/dev/ttyUSB0"):
            with self.assertRaisesRegex(ValueError, "same device path"):
                self.config([dict(ENTRY, protocol="LCUS"), second])

    def test_does_not_modify_options(self):
        options = {"usb_relays": [copy.deepcopy(ENTRY)]}
        before = copy.deepcopy(options)
        relay.configuration(options, PLAYERS)
        self.assertEqual(options, before)


class ProtocolTests(unittest.TestCase):
    def test_known_wire_frames(self):
        cases = [
            ("DSD TECH SH-UR01A", 1, True, b"AT+CH1=1\r\n"),
            ("DSD TECH SH-UR01A", 1, False, b"AT+CH1=0\r\n"),
            ("KMtronic", 1, True, b"\xff\x01\x01"),
            ("KMtronic", 1, False, b"\xff\x01\x00"),
            ("LCUS", 1, True, b"\xa0\x01\x01\xa2"),
            ("LCUS", 1, False, b"\xa0\x01\x00\xa1"),
            ("LCUS", 8, True, b"\xa0\x08\x01\xa9"),
            ("LCUS", 8, False, b"\xa0\x08\x00\xa8"),
        ]
        for protocol, channel, enabled, expected in cases:
            with self.subTest(protocol=protocol, channel=channel, enabled=enabled):
                self.assertEqual(relay.command(protocol, channel, enabled), expected)

    def test_refuses_commands_outside_supported_channels(self):
        for protocol, channel in (("unknown", 1), ("LCUS", 9), ("LCUS", 0),
                                  ("KMtronic", 2), ("LCUS", True)):
            with self.subTest(protocol=protocol, channel=channel):
                with self.assertRaises(ValueError):
                    relay.command(protocol, channel, True)


class DemandTests(unittest.TestCase):
    def test_startup_unknown_does_not_energize_amplifier(self):
        demand = relay.Demand()
        self.assertFalse(demand.update([None, False], 0, 60))
        self.assertIsNone(demand.idle_since)

    def test_shared_amplifier_stays_on_until_last_room_stops(self):
        demand = relay.Demand()
        self.assertTrue(demand.update([True, False], 0, 10))
        self.assertTrue(demand.update([False, True], 20, 10))
        self.assertTrue(demand.update([False, False], 21, 10))
        self.assertTrue(demand.update([False, False], 30.99, 10))
        self.assertFalse(demand.update([False, False], 31, 10))

    def test_unknown_resets_shutdown_deadline_instead_of_counting_as_idle(self):
        demand = relay.Demand()
        demand.update([True], 0, 5)
        demand.update([False], 1, 5)
        self.assertTrue(demand.update([None], 5, 5))
        self.assertTrue(demand.update([False], 100, 5))
        self.assertTrue(demand.update([False], 104.99, 5))
        self.assertFalse(demand.update([False], 105, 5))

    def test_rejoin_cancels_pending_off_and_duplicate_samples_do_not_drift(self):
        demand = relay.Demand()
        for now in range(20):
            self.assertTrue(demand.update([True], now, 5))
        demand.update([False], 20, 5)
        self.assertTrue(demand.update([True], 24, 5))
        self.assertIsNone(demand.idle_since)
        self.assertTrue(demand.update([False], 25, 5))
        self.assertFalse(demand.update([False], 30, 5))

    def test_active_room_wins_over_unknown_room_and_zero_delay_is_immediate(self):
        demand = relay.Demand()
        self.assertTrue(demand.update([None, True], 0, 0))
        self.assertFalse(demand.update([False, False], 1, 0))


class SerialPort:
    """Record the serial contract and inject real transport failure outcomes."""

    def __init__(self, replies=(), write_failure=None):
        self.replies = list(replies)
        self.writes = []
        self.write_failure = write_failure
        self.opened = False
        self.closed = False
        self.reset_count = 0

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def reset_input_buffer(self):
        self.reset_count += 1

    def write(self, data):
        self.writes.append(data)
        if self.write_failure:
            return self.write_failure(data)
        return len(data)

    def read_until(self, terminator, limit):
        return self.replies.pop(0) if self.replies else b"ERROR\r\n"


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.info = types.SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=5, st_ino=11)
        self.stat_patch = patch.object(relay.os, "stat", return_value=self.info)
        self.real_patch = patch.object(relay.os.path, "realpath", return_value="/dev/ttyUSB7")
        self.stat_mock = self.stat_patch.start()
        self.real_patch.start()
        self.addCleanup(self.stat_patch.stop)
        self.addCleanup(self.real_patch.stop)

    def board(self, *ports, protocol="DSD TECH SH-UR01A"):
        factory = Mock(side_effect=ports)
        board = relay.Board(ENTRY["device"], protocol, factory)
        return board, factory

    def test_serial_options_handshake_and_no_repeated_commands(self):
        port = SerialPort([b"AT\r\n", b"OK\r\n", b"OK\r\n"])
        board, factory = self.board(port)
        board.apply({1: True}, 0)
        board.apply({1: True}, 1)
        factory.assert_called_once_with(port=None, baudrate=9600, timeout=0.1,
                                        write_timeout=0.2, exclusive=True)
        self.assertEqual((port.dtr, port.rts, port.port), (False, False, ENTRY["device"]))
        self.assertEqual(port.writes, [b"AT\r\n", b"AT+CH1=1\r\n"])
        self.assertEqual(board.applied, {1: True})
        self.assertEqual(port.reset_count, 1)

    def test_command_ack_failure_does_not_claim_success_and_retries_current_demand(self):
        bad, good = SerialPort([b"OK\r\n", b"ERROR\r\n"]), SerialPort([b"OK\r\n"] * 2)
        board, factory = self.board(bad, good)
        board.apply({1: True}, 10)
        self.assertTrue(bad.closed)
        self.assertEqual(board.applied, {})
        board.apply({1: True}, 14.99)
        self.assertEqual(factory.call_count, 1)
        board.apply({1: False}, 15)
        self.assertEqual(good.writes, [b"AT\r\n", b"AT+CH1=0\r\n"])
        self.assertEqual(board.applied, {1: False})
        self.assertIsNone(board.error)

    def test_handshake_failure_sends_no_relay_command(self):
        port = SerialPort([b"ERROR\r\n"])
        board, _ = self.board(port)
        board.apply({1: True}, 0)
        self.assertEqual(port.writes, [b"AT\r\n"])
        self.assertTrue(port.closed)
        self.assertEqual(board.applied, {})

    def test_missing_ack_has_bounded_deadline(self):
        board, _ = self.board(SerialPort())
        board.port = Mock()
        board.port.read_until.return_value = b""
        with patch.object(relay.time, "monotonic", side_effect=[0, 0.1, 0.3, 0.7]):
            with self.assertRaisesRegex(OSError, "acknowledge"):
                board.acknowledge()
        self.assertEqual(board.port.read_until.call_count, 2)

    def test_short_write_and_transport_error_close_port(self):
        def disconnected(_):
            raise OSError("USB disconnected")
        for failure in (lambda data: len(data) - 1, disconnected):
            with self.subTest(failure=failure):
                port = SerialPort(write_failure=failure)
                board, _ = self.board(port, protocol="KMtronic")
                board.apply({1: True}, 3)
                self.assertTrue(port.closed)
                self.assertEqual(board.applied, {})
                self.assertEqual(board.retry_at, 8)

    def test_replaced_usb_device_reopens_and_reapplies_even_unchanged_demand(self):
        first, second = SerialPort(), SerialPort()
        board, factory = self.board(first, second, protocol="LCUS")
        board.apply({1: True, 8: False}, 0)
        self.info.st_ino += 1
        board.apply({1: True, 8: False}, 1)
        self.assertTrue(first.closed)
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(second.writes, [b"\xa0\x01\x01\xa2", b"\xa0\x08\x00\xa8"])

    def test_missing_or_non_character_device_is_not_opened(self):
        for outcome in (FileNotFoundError("missing"), types.SimpleNamespace(st_mode=stat.S_IFREG)):
            with self.subTest(outcome=outcome):
                self.stat_mock.side_effect = outcome if isinstance(outcome, Exception) else None
                self.stat_mock.return_value = outcome
                board, factory = self.board(SerialPort())
                board.apply({1: True}, 0)
                factory.assert_not_called()
                self.assertEqual(board.applied, {})

    def test_shutdown_only_controls_assigned_channels_and_closes(self):
        port = SerialPort()
        board, _ = self.board(port, protocol="LCUS")
        board.apply({2: True, 7: True}, 0)
        board.shutdown([2, 7])
        self.assertEqual(port.writes[-2:], [b"\xa0\x02\x00\xa2", b"\xa0\x07\x00\xa7"])
        self.assertTrue(port.closed)
        self.assertIsNone(board.port)

    def test_shutdown_does_not_open_an_unavailable_device(self):
        board, factory = self.board(SerialPort())
        board.shutdown([1])
        factory.assert_not_called()

    def test_stop_interrupts_pending_activation_but_shutdown_still_sends_off(self):
        stop = threading.Event()

        def stop_after_first_write(data):
            stop.set()
            return len(data)

        port = SerialPort(write_failure=stop_after_first_write)
        board, _ = self.board(port, protocol="LCUS")
        board.stop = stop
        board.apply({1: True, 2: True}, 0)
        self.assertEqual(port.writes, [b"\xa0\x01\x01\xa2"])
        board.shutdown([1, 2])
        self.assertEqual(port.writes[-2:], [b"\xa0\x01\x00\xa1", b"\xa0\x02\x00\xa2"])
        self.assertTrue(port.closed)

    def test_shutdown_failure_still_closes(self):
        port = SerialPort()
        board, _ = self.board(port, protocol="KMtronic")
        board.apply({1: True}, 0)
        port.write_failure = lambda _: 0
        board.shutdown([1])
        self.assertTrue(port.closed)
        self.assertIsNone(board.port)


class RunTests(unittest.TestCase):
    def test_shared_amplifier_and_unassigned_channels_through_service_loop(self):
        trigger, = relay.configuration({"usb_relays": [dict(ENTRY, off_delay=0)]}, PLAYERS)
        frames = [{"study": True, "bedroom": False},
                  {"study": False, "bedroom": True},
                  {"study": None, "bedroom": False},
                  {"study": False, "bedroom": False}]

        class Stop:
            index = 0

            def is_set(self):
                return self.index == len(frames)

            def wait(self, _):
                self.index += 1

        stop = Stop()
        with patch.object(relay, "Board") as board, patch.object(
            relay, "stream_status", side_effect=lambda zone: frames[stop.index][zone]
        ):
            board.return_value.device = ENTRY["device"]
            relay.run([trigger], stop)
            requested = [call.args[0] for call in board.return_value.apply.call_args_list]
            self.assertEqual(requested, [{1: True}, {1: True}, {1: True}, {1: False}])
            board.return_value.shutdown.assert_called_once_with({1: False})

    def test_already_stopped_is_safe(self):
        stop = threading.Event()
        stop.set()
        trigger, = relay.configuration({"usb_relays": [ENTRY]}, PLAYERS)
        with patch.object(relay, "Board") as board:
            relay.run([trigger], stop)
            board.return_value.apply.assert_not_called()
            board.return_value.shutdown.assert_called_once()

    def test_unexpected_status_failure_still_shuts_down(self):
        trigger, = relay.configuration({"usb_relays": [ENTRY]}, PLAYERS)
        with patch.object(relay, "Board") as board, patch.object(
            relay, "stream_status", side_effect=RuntimeError("fixture status failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture status failure"):
                relay.run([trigger], threading.Event())
            board.return_value.shutdown.assert_called_once()


@unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux Unix sockets")
class SocketTests(unittest.TestCase):
    def response(self, payload):
        with tempfile.TemporaryDirectory(prefix="relay-") as root:
            folder = Path(root) / "zones/study"
            folder.mkdir(parents=True)
            errors = []
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(folder / "control.sock"))
                listener.listen(1)
                listener.settimeout(2)

                def serve():
                    try:
                        connection, _ = listener.accept()
                        with connection:
                            connection.settimeout(2)
                            request = b""
                            while not request.endswith(b"\n"):
                                chunk = connection.recv(256)
                                if not chunk:
                                    raise EOFError("status request ended without a newline")
                                request += chunk
                            self.assertEqual(request, b"status\n")
                            connection.sendall(payload)
                    except Exception as error:
                        errors.append(error)

                thread = threading.Thread(target=serve, daemon=True)
                thread.start()
                result = relay.stream_status("study", Path(root))
                thread.join(3)
                self.assertFalse(thread.is_alive())
                if errors:
                    raise errors[0]
                return result

    def test_endpoint_stream_overrides_group_transport(self):
        self.assertFalse(self.response(b"ok\nstate: playing\nstream: idle\n"))
        self.assertTrue(self.response(b"ok\nstate: unknown\nstream: receiving\n"))

    def test_bad_or_ambiguous_replies_are_unknown(self):
        for payload in (b"error failed: busy\n", b"ok\nstate: playing\n",
                        b"ok\nstream: corrupt\n", b"ok\nstream: idle\nstream: receiving\n",
                        b"ok\nstream: idle\n\xff", b"x" * 65537):
            with self.subTest(payload=payload[:100]):
                self.assertIsNone(self.response(payload))

    def test_absent_socket_is_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(relay.stream_status("study", Path(root)))


@unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux pseudo terminals")
class SerialIntegrationTests(unittest.TestCase):
    def test_dsd_serial_handshake_on_off_and_shutdown(self):
        try:
            import serial
        except ImportError:
            self.skipTest("pyserial is not installed")
        import pty
        import select

        master, slave = pty.openpty()
        device = os.ttyname(slave)
        expected = [b"AT\r\n", b"AT+CH1=1\r\n", b"AT+CH1=0\r\n", b"AT+CH1=0\r\n"]
        received, errors = [], []

        def emulate():
            buffer = b""
            try:
                for wanted in expected:
                    while b"\n" not in buffer:
                        ready, _, _ = select.select([master], [], [], 3)
                        if not ready:
                            raise TimeoutError("relay emulator did not receive command")
                        buffer += os.read(master, 256)
                    line, buffer = buffer.split(b"\n", 1)
                    frame = line + b"\n"
                    received.append(frame)
                    if frame != wanted:
                        raise AssertionError(f"expected {wanted!r}, received {frame!r}")
                    os.write(master, b"OK\r\n")
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=emulate, daemon=True)
        thread.start()
        board = relay.Board(device, "DSD TECH SH-UR01A", serial.Serial)
        try:
            board.apply({1: True}, 0)
            self.assertEqual(board.applied, {1: True})
            board.apply({1: False}, 1)
            self.assertEqual(board.applied, {1: False})
            board.shutdown([1])
            thread.join(4)
            self.assertFalse(thread.is_alive())
            if errors:
                raise errors[0]
            self.assertEqual(received, expected)
        finally:
            board.close()
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
