# Local Audio Zones

## Set up

1. Add an entry under **Configuration / Zones**.
2. Set a **Zone name** and **Player ID**, such as `sendspin-study`.
3. Select a **Soundcard device** and **Output pair**.
4. Save and restart the app.
5. Open Music Assistant and test each zone at a low volume.

Keep a zone's Player ID when renaming it. An empty zone list creates no players.
Music Assistant controls playback, volume, mute and groups. Keep its minimum
volume at zero if you want silence at zero.

## Devices and outputs

Prefer a unique `/dev/snd/by-id/` device. For identical cards without unique
serial numbers, use **Edit in YAML** to set separate `/dev/snd/by-path/` links
and keep their physical USB ports fixed. Numeric card paths can change after
a reboot.

A control device selects a card; a playback device selects its PCM endpoint.
Capture, sequencer and timer devices cannot be used. The host's active audio
profile must expose the required output and channels.

| Output pair | Channels |
| --- | --- |
| Front left/right | `front-left,front-right` |
| Rear left/right | `rear-left,rear-right` |
| Side left/right | `side-left,side-right` |
| Centre/Subwoofer | `front-center,lfe` |

For another layout, leave **Output pair** unset and enter two distinct
**Custom channels**, such as `aux0,aux1`. The Log tab lists available channels.
The native form shows fixed choices; the app checks them against the actual
card and rejects missing or overlapping channels.

Headphone sockets may share another output. Centre/Subwoofer is suitable for
stereo only when both physical channels provide full-range sound.

**Explicit output** is an advanced alternative for an existing player output,
such as `pulse:my_stereo_sink`. Leave device and channel fields unset. The host
owns that output; the app cannot reconstruct arbitrary external routing or
verify channel isolation through nested filters.

## Optional settings

Log level, server and audio buffer can be set for the app or overridden per
zone. Leave the buffer unset initially. Ports must be unique and default to
8928 plus the zone's position.

Leave **Music Assistant server** empty for automatic player discovery. Use
`mdns:<name>` to connect to a selected discovered server, or a direct address
such as `192.168.1.10:8927` or `ws://192.168.1.10:8927/sendspin`. An explicit
server setting makes the player initiate the connection and stops it advertising
itself over mDNS. An empty zone value clears an inherited server setting.
Server URLs must not contain credentials.

The global **Audio** panel belongs to Home Assistant's managed audio connection.
Leave its input and output at **Default**; zone settings select playback outputs.

## Troubleshooting

The Log tab reports invalid settings, selected outputs and available channels.
Health checks cover players, routing and discovery; they cannot verify cables,
amplifier power or audible sound.

When a card reconnects, the app restores its zone routes and players retry their
selected output. Other zones keep running. Reconnect to the same USB port and
ensure the host detects the card again; the app cannot recover a device that
is missing from the host or VM.

The app preserves host profiles, sample rates and physical output levels. If
sound is distorted, check those settings and the amplifier connection first.
See the [hardware notes](https://github.com/alphasixtyfive/local-audio-zones/blob/main/docs/audio-hardware-support.md).

Based on Music Assistant Local Audio. See
[NOTICE](https://github.com/alphasixtyfive/local-audio-zones/blob/main/local_audio_zones/NOTICE)
for attribution and dependency licenses.
