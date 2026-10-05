#!/usr/bin/env python3
"""Ingress room editor; Supervisor remains the owner of app options."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import socket
import time
from pathlib import Path

import aiohttp
from aiohttp import web


INGRESS_IP = "172.30.32.2"
RUN_DIR = Path("/run/sendspin-cli")
FRONTEND = Path("/usr/share/sendspin-cli/config/index.html")
VALIDATOR = Path(__file__).with_name("players.jq")
BODY_LIMIT = 131072


class SettingsError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def revision(options):
    canonical = json.dumps(options, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def command(*args, timeout=3, input_data=None, env=None):
    process = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE if input_data is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(input_data), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.communicate()
        raise
    return process.returncode, stdout, stderr


class Supervisor:
    def __init__(self, session):
        self.session = session

    async def request(self, method, path, payload=None):
        try:
            async with self.session.request(method, "http://supervisor" + path, json=payload) as response:
                data = await response.json()
                if response.status != 200 or data.get("result") != "ok":
                    raise SettingsError(502, "supervisor_error", "Supervisor could not complete this request.")
                return data.get("data")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, AttributeError):
            raise SettingsError(502, "supervisor_unavailable", "Supervisor is unavailable. Try again shortly.") from None

    async def options(self):
        data = await self.request("GET", "/addons/self/info")
        if not isinstance(data, dict) or not isinstance(data.get("options"), dict):
            raise SettingsError(502, "supervisor_error", "Supervisor returned invalid app settings.")
        return data["options"]

    async def save(self, options):
        await self.request("POST", "/addons/self/options", {"options": options})

    async def restart(self):
        await self.request("POST", "/addons/self/restart")

    async def users(self):
        try:
            async with self.session.ws_connect("http://supervisor/core/websocket") as socket:
                if (await socket.receive_json(timeout=5)).get("type") != "auth_required":
                    raise ValueError
                await socket.send_json({"type": "auth", "access_token": os.environ["SUPERVISOR_TOKEN"]})
                if (await socket.receive_json(timeout=5)).get("type") != "auth_ok":
                    raise ValueError
                await socket.send_json({"id": 1, "type": "config/auth/list"})
                response = await socket.receive_json(timeout=5)
                if response.get("id") != 1 or response.get("success") is not True:
                    raise ValueError
                users = response.get("result")
                if not isinstance(users, list):
                    raise ValueError
                return users
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, AttributeError, TypeError):
            raise SettingsError(503, "authorization_unavailable", "Home Assistant could not verify administrator access.") from None


class Administrator:
    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.allowed = set()
        self.expires = 0
        self.lock = asyncio.Lock()
        self.csrf_secret = secrets.token_bytes(32)

    def csrf_token(self, user_id):
        return hmac.new(self.csrf_secret, user_id.encode(), hashlib.sha256).hexdigest()

    async def require(self, request):
        user_id = request.headers.get("X-Remote-User-Id", "")
        if not user_id:
            raise SettingsError(403, "administrator_required", "A Home Assistant administrator must open this editor.")
        async with self.lock:
            if time.monotonic() >= self.expires:
                try:
                    users = await asyncio.wait_for(self.supervisor.users(), timeout=5)
                except asyncio.TimeoutError:
                    raise SettingsError(503, "authorization_unavailable", "Home Assistant could not verify administrator access.") from None
                if not isinstance(users, list):
                    raise SettingsError(503, "authorization_unavailable", "Home Assistant returned invalid authorization data.")
                self.allowed = {
                    user["id"] for user in users if isinstance(user, dict)
                    and isinstance(user.get("id"), str) and user.get("is_active") is True
                    and (user.get("is_owner") is True or (
                        isinstance(user.get("group_ids"), list) and "system-admin" in user["group_ids"]))
                }
                self.expires = time.monotonic() + 15
        if user_id not in self.allowed:
            raise SettingsError(403, "administrator_required", "A Home Assistant administrator must open this editor.")
        if request.method == "POST":
            token = request.headers.get("X-CSRF-Token", "")
            if not re.fullmatch(r"[a-f0-9]{64}", token) or not hmac.compare_digest(token, self.csrf_token(user_id)):
                raise SettingsError(403, "invalid_request_token", "Reload this editor before changing settings.")
        return user_id


class Editor:
    def __init__(self, supervisor, run_dir=RUN_DIR, validator=VALIDATOR):
        self.supervisor = supervisor
        self.run_dir = run_dir
        self.validator = validator
        self.save_lock = asyncio.Lock()
        self.health_slots = asyncio.Semaphore(8)

    async def validate(self, zones, options):
        values = {}
        defaults = {"name": "Local Audio", "output": "default", "log_level": "info"}
        for key in ("name", "output", "log_level", "server", "buffer_ms", "hook_start", "hook_stop"):
            value = options.get(key)
            if value is None or value == "":
                value = defaults.get(key, "")
            if key == "buffer_ms" and value != "":
                if isinstance(value, bool) or not isinstance(value, int) or not 10 <= value <= 2000:
                    raise SettingsError(400, "invalid_settings", "buffer_ms must be a whole number between 10 and 2000.")
                value = str(value)
            if not isinstance(value, str):
                raise SettingsError(400, "invalid_settings", key + " must be a string.")
            if re.search(r"[\x00-\x1f\x7f-\x9f]", value):
                raise SettingsError(400, "invalid_settings", key + " must not contain control characters.")
            values[key] = value
        values["audio_format"] = os.environ.get("SENDSPIN_AUDIO_FORMAT", "")
        values["client_id"] = os.environ.get("SENDSPIN_ID", "")
        arguments = ["jq", "--slurp"]
        for key, value in values.items():
            arguments.extend(("--arg", key, value))
        arguments.extend(("-f", str(self.validator)))
        try:
            status, stdout, stderr = await command(
                *arguments, input_data=json.dumps(zones, allow_nan=False).encode(), timeout=3,
            )
        except (OSError, asyncio.TimeoutError):
            raise SettingsError(503, "validator_unavailable", "The settings validator is unavailable.") from None
        except (ValueError, TypeError):
            raise SettingsError(400, "invalid_settings", "Settings must contain valid JSON values.") from None
        if status:
            message = stderr.decode(errors="replace").strip().splitlines()
            detail = re.sub(r"^jq: error \(at [^)]*\):\s*", "", message[-1]) if message else "Invalid room settings."
            raise SettingsError(400, "invalid_settings", detail[:300])
        return json.loads(stdout)

    async def outputs(self):
        environment = {**os.environ, "PULSE_SERVER": "unix:/run/audio/pulse.sock"}
        try:
            status, stdout, _ = await command("pactl", "--format=json", "list", "sinks", env=environment)
            if status != 0:
                raise ValueError
            sinks = json.loads(stdout)
            if not isinstance(sinks, list):
                raise ValueError
            return [
                {"value": "pulse:" + sink["name"], "label": sink.get("description") or sink["name"],
                 "name": sink["name"], "available": True,
                 "sample_specification": sink.get("sample_specification", ""),
                 "channel_map": sink.get("channel_map", "")}
                for sink in sinks if isinstance(sink, dict) and isinstance(sink.get("name"), str)
            ]
        except (OSError, asyncio.TimeoutError, ValueError, TypeError):
            raise SettingsError(503, "audio_unavailable", "PulseAudio is unavailable. Try again shortly.") from None

    def running_players(self):
        try:
            players = json.loads((self.run_dir / "players.json").read_text())
            if not isinstance(players, list):
                return []
            return [player for player in players if isinstance(player, dict)
                    and isinstance(player.get("id"), str)
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", player["id"])]
        except (OSError, ValueError):
            return []

    async def healthy(self, player):
        directory = self.run_dir if player["id"] == "default" else self.run_dir / "zones" / player["id"]
        socket = directory / "control.sock"
        if not socket.is_socket():
            return False
        async with self.health_slots:
            try:
                status, _, _ = await command("sendspin-cli", "status", "--control-socket", str(socket), timeout=2)
                return status == 0
            except (OSError, asyncio.TimeoutError):
                return False

    async def settings(self, request):
        options = await self.supervisor.options()
        outputs = await self.outputs()
        players = self.running_players()
        configured = await self.validate(options.get("zones") or [], options)
        health = await asyncio.gather(*(self.healthy(player) for player in players))
        return web.json_response({
            "options": options, "outputs": outputs, "revision": revision(options),
            "csrf_token": request.app[ADMIN_KEY].csrf_token(request.headers["X-Remote-User-Id"]),
            "restart_required": configured != players,
            "players": [{key: player.get(key, "") for key in ("id", "name", "output")}
                        | {"healthy": healthy} for player, healthy in zip(players, health)],
        })

    async def draft(self, request):
        if request.content_type != "application/json":
            raise SettingsError(400, "invalid_request", "Send settings as JSON.")
        try:
            payload = await request.json()
        except (ValueError, UnicodeError):
            raise SettingsError(400, "invalid_request", "The request must contain valid JSON.") from None
        if (not isinstance(payload, dict) or set(payload) != {"zones", "revision"}
                or not isinstance(payload["zones"], list)
                or not isinstance(payload["revision"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", payload["revision"])):
            raise SettingsError(400, "invalid_request", "Provide rooms and the current settings revision.")
        options = await self.supervisor.options()
        if revision(options) != payload["revision"]:
            raise SettingsError(409, "settings_changed", "Settings changed. Reload before saving.")
        players = await self.validate(payload["zones"], options)
        return payload, options, players

    async def check(self, request):
        _, _, players = await self.draft(request)
        outputs = {output["value"] for output in await self.outputs()}
        active = {player["id"]: player for player in self.running_players()}
        async def check_player(player):
            running = active.get(player["id"]) == player
            output = player["output"]
            if output in ("default", "pulse", "pulse:"):
                available = bool(outputs)
            elif output.startswith("pulse:"):
                available = output in outputs
            else:
                available = None
            return {
                "id": player["id"], "name": player["name"], "output": player["output"],
                "available": available,
                "running": running,
                "healthy": await self.healthy(active[player["id"]]) if running else False,
            }
        checks = await asyncio.gather(*(check_player(player) for player in players))
        return web.json_response({"valid": True, "checks": checks,
                                  "restart_required": players != self.running_players()})

    async def save(self, request):
        async with self.save_lock:
            payload, options, _ = await self.draft(request)
            # The Supervisor has no compare-and-swap options API; recheck after validation.
            current = await self.supervisor.options()
            if revision(current) != revision(options):
                raise SettingsError(409, "settings_changed", "Settings changed. Reload before saving.")
            updated = {**current, "zones": payload["zones"]}
            await self.supervisor.save(updated)
            saved = await self.supervisor.options()
            return web.json_response({
                "saved": True, "revision": revision(saved), "restart_required": True,
                "message": "Saved. Restart Local Audio Zones to apply these settings.",
            })

    async def restart(self, request):
        if request.content_type != "application/json":
            raise SettingsError(400, "invalid_request", "Send settings as JSON.")
        try:
            payload = await request.json()
        except (ValueError, UnicodeError):
            raise SettingsError(400, "invalid_request", "The request must contain valid JSON.") from None
        if not isinstance(payload, dict) or set(payload) != {"revision"}:
            raise SettingsError(400, "invalid_request", "Provide the current settings revision.")
        async with self.save_lock:
            options = await self.supervisor.options()
            if revision(options) != payload["revision"]:
                raise SettingsError(409, "settings_changed", "Settings changed. Reload before restarting.")
            response = web.json_response({"restarting": True, "message": "Restart requested."}, status=202)
            response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
            # Flush the acknowledgement before Supervisor stops this process.
            await response.prepare(request)
            await response.write_eof()
            try:
                await self.supervisor.restart()
            except SettingsError as error:
                logging.getLogger(__name__).warning("App restart request failed: %s", error.code)
        return response


EDITOR_KEY = web.AppKey("editor", Editor)
ADMIN_KEY = web.AppKey("administrator", Administrator)


@web.middleware
async def ingress_only(request, handler):
    connection = request.transport.get_extra_info("socket") if request.transport else None
    unix_peer = connection is not None and connection.family == socket.AF_UNIX
    if request.path == "/health" and (request.remote in ("127.0.0.1", "::1") or unix_peer):
        return await handler(request)
    if request.remote != INGRESS_IP:
        return web.json_response({"error": {"code": "forbidden", "message": "Use Home Assistant to open this app."}}, status=403)

    async def authorized_handler():
        await request.app[ADMIN_KEY].require(request)
        return await handler(request)

    try:
        response = await asyncio.wait_for(authorized_handler(), timeout=30)
    except SettingsError as error:
        response = web.json_response({"error": {"code": error.code, "message": error.message}}, status=error.status)
    except web.HTTPRequestEntityTooLarge:
        response = web.json_response({"error": {"code": "request_too_large", "message": "The settings request is too large."}}, status=413)
    except asyncio.TimeoutError:
        response = web.json_response({"error": {"code": "timeout", "message": "The request timed out. Try again."}}, status=504)
    except web.HTTPException as error:
        response = web.json_response({"error": {"code": "http_error", "message": error.reason}}, status=error.status)
    if not response.prepared:
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    return response


def create_app(supervisor=None):
    app = web.Application(middlewares=[ingress_only], client_max_size=BODY_LIMIT)

    async def resources(application):
        if supervisor is None:
            token = os.environ["SUPERVISOR_TOKEN"]
            async with aiohttp.ClientSession(
                headers={"Authorization": "Bearer " + token}, timeout=aiohttp.ClientTimeout(total=5),
            ) as session:
                application[EDITOR_KEY] = Editor(Supervisor(session))
                application[ADMIN_KEY] = Administrator(application[EDITOR_KEY].supervisor)
                yield
        else:
            application[EDITOR_KEY] = Editor(supervisor)
            application[ADMIN_KEY] = Administrator(supervisor)
            yield

    async def index(request):
        return web.FileResponse(FRONTEND)

    async def health(request):
        return web.json_response({"healthy": True}, headers={"Cache-Control": "no-store"})

    app.cleanup_ctx.append(resources)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)

    def endpoint(action):
        async def handler(request):
            return await getattr(request.app[EDITOR_KEY], action)(request)
        return handler

    app.router.add_get("/api/settings", endpoint("settings"))
    app.router.add_post("/api/check", endpoint("check"))
    app.router.add_post("/api/settings", endpoint("save"))
    app.router.add_post("/api/restart", endpoint("restart"))
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=int(os.environ.get("SENDSPIN_CONFIG_PORT", "8099")),
                path=str(RUN_DIR / "editor.sock"), shutdown_timeout=0.5, access_log=None)
