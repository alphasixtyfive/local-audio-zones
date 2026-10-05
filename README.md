# Local Audio Zones

A Home Assistant app that gives each audio output its own Music Assistant player.
Configure room names and outputs in the web editor, then control playback, volume
and groups in Music Assistant. Each room has separate saved state and an
independently supervised native Sendspin player.

## Install

1. Copy `local_audio_zones/` to `/addons/local_audio_zones/` on Home Assistant OS.
2. In **Settings → Apps → App store**, check for updates, install **Local Audio
   Zones**, and start it.
3. Select **Open Web UI**. Add rooms, choose their outputs, select **Check
   configuration**, then **Save configuration** and **Restart app**.

Music Assistant and the app need working mDNS on the same local network. The
editor requires a Home Assistant administrator account. Saving validates and
stores settings; restarting applies them and briefly interrupts playback.

## Room settings

Use one named PulseAudio output per room, for example `pulse:ca7_study`.
Outputs must already exist in the host audio service. For a surround soundcard,
create a stereo remap for each channel pair; see
[PulseAudio zones](docs/pulseaudio-zones.md). The app leaves card profiles,
channel routing and hardware levels to the host.

The editor generates a stable player ID. Keep it when renaming a room so Music
Assistant retains its settings. **Advanced settings** can override the following:

| Setting | Behavior |
| --- | --- |
| Player ID | 1–64 letters, digits, underscores or hyphens; begins with a letter or digit. `default` is reserved. |
| Port | Unique port from 1024–65535; defaults to 8928 plus room position. |
| Log level | `debug`, `info`, `warning` or `error`. |
| Server | Empty for player discovery, `mdns:` for any server, or `mdns:<name>` for a named server. |
| Audio buffer | Whole number from 10–2000 ms; leave inherited unless needed. |
| Playback commands | Native start/stop shell commands, run inside the app without blocking playback. |

Up to 32 rooms are supported. Log level, server, buffer and commands inherit the
app settings unless overridden. App settings remain in Home Assistant's
**Configuration** tab. An explicit empty server or command clears its inherited
value. Invalid IDs, duplicate ports, unknown fields and control characters are
rejected before players start.

**Check configuration** reports output availability and current player responses;
it cannot verify wiring or sound quality. Start at a low volume when migrating
from another player. Keep Music Assistant's minimum volume at zero for silence
at zero, and calibrate maximum loudness at the amplifier or host output.

With no rooms configured, the app provides a single player using Home
Assistant's built-in **Audio** selector. That selector does not route named rooms.

## Development

The self-contained app lives in `local_audio_zones/`. Build it with:

```sh
docker build -t local-audio-zones local_audio_zones
scripts/smoke_test.sh local-audio-zones --mode standalone
scripts/smoke_test.sh local-audio-zones --mode addon
```

The repository also includes configuration, supervision, PulseAudio routing,
AppArmor and editor tests. CI checks both amd64 and aarch64.

## Credits

Packaging is based on [Music Assistant Local Audio](https://github.com/music-assistant/local-audio-addon)
and its [native-player update in PR #34](https://github.com/music-assistant/local-audio-addon/pull/34).
The player is [sendspin-cli v0.2.0](https://github.com/Sendspin/sendspin-cpp-cli),
pinned to `e980128a9c37229cbc28c764e9f2fd65d1c5edb6`. One
[documented patch](local_audio_zones/patches/README.md) uses PulseAudio's standard
`PA_STREAM_DONT_MOVE` flag to keep named room outputs from moving to another
sink after a device loss. Existing upstream notices and the Apache-2.0
[license](LICENSE) are retained.
