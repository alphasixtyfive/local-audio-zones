# Local Audio Zones

Turn local soundcard outputs into rooms in Music Assistant. Select a soundcard
and stereo output for each room in Home Assistant, then control playback,
volume and groups in Music Assistant.

## Install

1. Copy `local_audio_zones/` to `/addons/local_audio_zones/` on Home Assistant OS.
2. In **Settings / Apps / App store**, check for updates and install **Local
   Audio Zones**.
3. In the app's **Configuration** tab, add rooms with stable IDs and names.
   Select each detected sound device and stereo pair, then save and restart.

See the [app guide](local_audio_zones/README.md) for the native controls and
[output routing](docs/pulseaudio-zones.md) and
[hardware support](docs/audio-hardware-support.md) for hardware requirements.
Music Assistant and the app need working mDNS on the same local network.

See [publishing](docs/publishing.md) for the community repository and image plan.

## Room settings

Choose a `/dev/snd/by-id/` soundcard when available and Front left/right, Rear left/right,
Side left/right or Centre/Subwoofer. For other layouts, leave Output pair unset
and enter two distinct names in Custom channels, such as `aux0,aux1`. The Log
tab reports the card's actual available channels. Existing stereo remaps are reused; otherwise the app
creates a standard PulseAudio remap with `remix=no`. It does not change card
profiles, sample rates or hardware volume. An explicit output such as
`pulse:my_stereo_sink` is also supported instead of a sound-device selection.

| Setting | Behavior |
| --- | --- |
| Player ID | 1–64 letters, digits, underscores or hyphens; begins with a letter or digit. Keep it when renaming a room. |
| Port | Unique port from 1024–65535; defaults to 8928 plus room position. |
| Log level | `debug`, `info`, `warning` or `error`. |
| Server | Empty for player discovery, `mdns:` for any server, or `mdns:<name>` for a named server. |
| Audio buffer | Whole number from 10–2000 ms; leave inherited unless needed. |
| Playback commands | Native start/stop shell commands, run inside the app without blocking playback. |

Up to 32 rooms are supported. Log level, server, buffer and commands inherit the
app settings unless overridden. An explicit empty server or command clears its
inherited value. Invalid IDs, duplicate ports, unsupported or overlapping channels and ambiguous
card mappings are rejected before players start. No rooms means the app waits
for configuration without advertising an extra player.

Saving native settings stores them; restarting applies them. The Log tab reports
selected outputs and startup errors. Health checks verify player control sockets;
they cannot verify wiring or sound quality. Keep Music Assistant's minimum volume
at zero for silence at zero and calibrate maximum loudness at the amplifier or
host output.

## Development

The self-contained app lives in `local_audio_zones/`. Build it with:

```sh
docker build -t local-audio-zones local_audio_zones
scripts/smoke_test.sh local-audio-zones
```

CI checks configuration, supervision, PulseAudio routing and AppArmor on amd64
and aarch64.

## Credits

Packaging is based on [Music Assistant Local Audio](https://github.com/music-assistant/local-audio-addon)
and its [native-player update in PR #34](https://github.com/music-assistant/local-audio-addon/pull/34).
The player is [sendspin-cli v0.2.0](https://github.com/Sendspin/sendspin-cpp-cli),
pinned to `e980128a9c37229cbc28c764e9f2fd65d1c5edb6`. One
[documented patch](local_audio_zones/patches/README.md) uses PulseAudio's standard
`PA_STREAM_DONT_MOVE` flag to keep named room outputs from moving to another
sink after device loss. Upstream notices and the Apache-2.0 [license](LICENSE)
are retained.

The logo adapts [HASSConnect](https://github.com/alphasixtyfive/HASSConnect)'s
house artwork. See [NOTICE](local_audio_zones/NOTICE) for origins and licenses.
This is an independently maintained community app.
