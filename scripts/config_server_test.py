#!/usr/bin/env python3
"""Run with Python, aiohttp and jq; no Home Assistant or audio hardware required."""

import copy
import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("config_server", ROOT / "local_audio_zones/rootfs/usr/lib/sendspin-cli/config_server.py")
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)

ZONES = [{"id": "study", "name": "Study", "output": "pulse:study"}]
OPTIONS = {"name": "Local Audio", "log_level": "warning", "buffer_ms": 100,
           "hook_start": "logger started", "server": "mdns:Home", "zones": ZONES}
USERS = [
    {"id": "owner", "is_owner": True, "is_active": True, "group_ids": []},
    {"id": "admin", "is_owner": False, "is_active": True, "group_ids": ["system-admin"]},
    {"id": "user", "is_owner": False, "is_active": True, "group_ids": ["system-users"]},
    {"id": "disabled", "is_owner": True, "is_active": False, "group_ids": []},
]


class FakeSupervisor:
    def __init__(self):
        self.data = copy.deepcopy(OPTIONS)
        self.saved = []
        self.restarts = 0
        self.users = AsyncMock(return_value=USERS)

    async def options(self):
        return copy.deepcopy(self.data)

    async def save(self, options):
        self.data = copy.deepcopy(options)
        self.saved.append(options)

    async def restart(self):
        self.restarts += 1


class Request:
    def __init__(self, payload=None, method="POST", user="admin", csrf=None):
        self.payload = payload
        self.method = method
        self.headers = {"X-Remote-User-Id": user}
        if csrf is not None:
            self.headers["X-CSRF-Token"] = csrf
        self.content_type = "application/json"
        self.remote = config.INGRESS_IP
        self.transport = None
        self.path = "/api/settings"
        self.app = {}

    async def json(self):
        return self.payload


class AuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.supervisor = FakeSupervisor()
        self.authorization = config.Administrator(self.supervisor)

    async def test_owner_and_admin_authorized(self):
        for user in ("owner", "admin"):
            await self.authorization.require(Request(method="GET", user=user))

    async def test_nonadmin_unknown_and_inactive_rejected(self):
        for user in ("user", "unknown", "disabled", ""):
            with self.assertRaises(config.SettingsError) as error:
                await self.authorization.require(Request(method="GET", user=user))
            self.assertEqual(error.exception.status, 403)

    async def test_forged_admin_header_does_not_authorize(self):
        request = Request(method="GET", user="user")
        request.headers["X-Hass-Is-Admin"] = "1"
        with self.assertRaises(config.SettingsError):
            await self.authorization.require(request)

    async def test_lookup_failure_does_not_use_expired_cache(self):
        await self.authorization.require(Request(method="GET"))
        self.authorization.expires = 0
        self.supervisor.users.side_effect = config.SettingsError(503, "authorization_unavailable", "Unavailable")
        with self.assertRaises(config.SettingsError) as error:
            await self.authorization.require(Request(method="GET"))
        self.assertEqual(error.exception.status, 503)

    async def test_write_nonce_is_required_and_bound_to_user(self):
        for token in (None, "é", self.authorization.csrf_token("owner")):
            with self.assertRaises(config.SettingsError):
                await self.authorization.require(Request(csrf=token))
        await self.authorization.require(Request(csrf=self.authorization.csrf_token("admin")))

    async def test_untrusted_peer_rejected_before_lookup(self):
        request = Request(method="GET")
        request.remote = "127.0.0.1"
        request.headers["X-Forwarded-For"] = config.INGRESS_IP
        request.app[config.ADMIN_KEY] = self.authorization
        handler = AsyncMock(return_value=web.json_response({}))
        response = await config.ingress_only(request, handler)
        self.assertEqual(response.status, 403)
        self.supervisor.users.assert_not_called()
        handler.assert_not_called()


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.supervisor = FakeSupervisor()
        self.directory = tempfile.TemporaryDirectory(dir="/tmp")
        self.editor = config.Editor(self.supervisor, run_dir=Path(self.directory.name))

    async def asyncTearDown(self):
        self.directory.cleanup()

    def request(self, zones):
        return Request({"zones": zones, "revision": config.revision(self.supervisor.data)})

    async def test_save_changes_only_zones_without_restart(self):
        zones = [{**ZONES[0], "name": "New Study", "hook_start": ""}]
        response = await self.editor.save(self.request(zones))
        body = json.loads(response.body)
        self.assertTrue(body["saved"])
        self.assertTrue(body["restart_required"])
        self.assertEqual(self.supervisor.data, {**OPTIONS, "zones": zones})
        self.assertEqual(self.supervisor.restarts, 0)

    async def test_stale_revision_rejected_without_write(self):
        request = self.request(ZONES)
        self.supervisor.data["log_level"] = "debug"
        with self.assertRaises(config.SettingsError) as error:
            await self.editor.save(request)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.supervisor.saved, [])

    async def test_change_during_validation_rejected(self):
        original_validate = self.editor.validate

        async def concurrent_change(zones, options):
            result = await original_validate(zones, options)
            self.supervisor.data["name"] = "Changed outside editor"
            return result

        self.editor.validate = concurrent_change
        with self.assertRaises(config.SettingsError) as error:
            await self.editor.save(self.request(ZONES))
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.supervisor.saved, [])

    async def test_invalid_draft_never_writes(self):
        for zones in ([{**ZONES[0], "id": "../other"}], [{**ZONES[0], "port": 2}],
                      [{**ZONES[0], "extra": "option"}], [{**ZONES[0], "name": "Study\nserver=evil"}]):
            with self.assertRaises(config.SettingsError) as error:
                await self.editor.save(self.request(zones))
            self.assertEqual(error.exception.status, 400)
        self.assertEqual(self.supervisor.saved, [])

    async def test_global_controls_and_buffer_refused(self):
        for options in ({**OPTIONS, "name": "bad\nname"}, {**OPTIONS, "buffer_ms": True},
                        {**OPTIONS, "buffer_ms": 3000}):
            with self.assertRaises(config.SettingsError):
                await self.editor.validate(ZONES, options)

    async def test_empty_zones_stay_single_player(self):
        response = await self.editor.save(self.request([]))
        self.assertEqual(self.supervisor.data["zones"], [])
        players = await self.editor.validate([], self.supervisor.data)
        self.assertEqual(players[0]["id"], "default")
        self.assertEqual(players[0]["log_level"], "warn")
        self.assertEqual(response.status, 200)

    async def test_check_reports_missing_output_without_playing(self):
        self.editor.outputs = AsyncMock(return_value=[])
        self.editor.healthy = AsyncMock(return_value=True)
        response = await self.editor.check(self.request(ZONES))
        check = json.loads(response.body)["checks"][0]
        self.assertFalse(check["available"])
        self.assertFalse(check["running"])
        self.assertFalse(check["healthy"])
        self.editor.healthy.assert_not_called()
        self.assertEqual(self.supervisor.saved, [])

    async def test_running_health_matches_applied_settings(self):
        players = await self.editor.validate(ZONES, OPTIONS)
        (Path(self.directory.name) / "players.json").write_text(json.dumps(players))
        self.editor.outputs = AsyncMock(return_value=[{"value": "pulse:study"}])
        self.editor.healthy = AsyncMock(return_value=True)
        response = await self.editor.check(self.request(ZONES))
        check = json.loads(response.body)["checks"][0]
        self.assertTrue(check["available"] and check["running"] and check["healthy"])
        response = await self.editor.check(self.request([{**ZONES[0], "name": "Draft name"}]))
        self.assertFalse(json.loads(response.body)["checks"][0]["running"])

    async def test_get_preserves_single_mode_and_reports_pending_restart(self):
        self.supervisor.data["zones"] = []
        self.editor.outputs = AsyncMock(return_value=[])
        request = Request(method="GET")
        request.app[config.ADMIN_KEY] = config.Administrator(self.supervisor)
        response = await self.editor.settings(request)
        body = json.loads(response.body)
        self.assertEqual(body["options"]["zones"], [])
        self.assertTrue(body["restart_required"])
        self.assertEqual(len(body["csrf_token"]), 64)
        self.assertEqual(self.supervisor.saved, [])

    async def test_restart_requires_current_revision(self):
        with self.assertRaises(config.SettingsError):
            await self.editor.restart(Request({"revision": "stale"}))
        self.assertEqual(self.supervisor.restarts, 0)
        with patch.object(web.Response, "prepare", AsyncMock()), patch.object(web.Response, "write_eof", AsyncMock()):
            await self.editor.restart(Request({"revision": config.revision(self.supervisor.data)}))
        self.assertEqual(self.supervisor.restarts, 1)

    async def test_audio_failure_is_reported(self):
        with patch.object(config, "command", AsyncMock(return_value=(1, b"", b"connection failed"))):
            with self.assertRaises(config.SettingsError) as error:
                await self.editor.outputs()
        self.assertEqual(error.exception.code, "audio_unavailable")

    async def test_restart_acknowledgement_arrives_before_supervisor_returns(self):
        entered = asyncio.Event()
        finish = asyncio.Event()

        async def slow_restart():
            entered.set()
            await finish.wait()

        self.supervisor.restart = slow_restart
        app = config.create_app(self.supervisor)
        with patch.object(config, "INGRESS_IP", "127.0.0.1"):
            async with TestClient(TestServer(app)) as client:
                administrator = app[config.ADMIN_KEY]
                token = administrator.csrf_token("admin")
                response = await client.post("/api/restart", json={"revision": config.revision(self.supervisor.data)},
                                             headers={"X-Remote-User-Id": "admin", "X-CSRF-Token": token})
                self.assertEqual(response.status, 202)
                self.assertTrue((await response.json())["restarting"])
                await asyncio.wait_for(entered.wait(), 1)
                self.assertFalse(finish.is_set())
                finish.set()

    async def test_loopback_health_does_not_need_core_or_allow_settings(self):
        self.supervisor.users.side_effect = config.SettingsError(503, "authorization_unavailable", "Unavailable")
        async with TestClient(TestServer(config.create_app(self.supervisor))) as client:
            self.assertEqual((await client.get("/health")).status, 200)
            response = await client.get("/api/settings", headers={"X-Remote-User-Id": "admin"})
            self.assertEqual(response.status, 403)
        self.supervisor.users.assert_not_called()

    async def test_unix_health_does_not_need_core_or_allow_settings(self):
        self.supervisor.users.side_effect = config.SettingsError(503, "authorization_unavailable", "Unavailable")
        runner = web.AppRunner(config.create_app(self.supervisor), shutdown_timeout=0.5)
        await runner.setup()
        try:
            path = str(Path(self.directory.name) / "editor.sock")
            await web.UnixSite(runner, path).start()
            async with config.aiohttp.ClientSession(connector=config.aiohttp.UnixConnector(path=path)) as client:
                async with client.get("http://localhost/health") as response:
                    self.assertEqual(response.status, 200)
                async with client.get("http://localhost/api/settings", headers={"X-Remote-User-Id": "admin"}) as response:
                    self.assertEqual(response.status, 403)
        finally:
            await runner.cleanup()
        self.supervisor.users.assert_not_called()


if __name__ == "__main__":
    unittest.main()
