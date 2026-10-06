# Local Audio Zones

<img src="local_audio_zones/icon.svg" width="96" height="96" alt="Local Audio Zones">

Turn soundcard outputs into separate zones in Music Assistant. Choose a card
and a stereo pair for each zone in Home Assistant's app settings. Music
Assistant handles playback, volume and groups.

## Install

[Add to Home Assistant](https://my.home-assistant.io/redirect/supervisor_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Falphasixtyfive%2Flocal-audio-zones)

1. Add `https://github.com/alphasixtyfive/local-audio-zones` under
   **Settings / Apps / App store / Repositories**.
2. Install **Local Audio Zones**.
3. Add your zones in **Configuration**, save, then restart the app.

Music Assistant and the app need working mDNS on the same local network.
This first release is experimental while testing across more hardware continues.

## Configuration

Select a detected soundcard and an output pair for each zone. Custom channel
pairs and existing stereo outputs are also supported. Keep each zone's player
ID when renaming it. For identical cards without unique serial numbers, use
their separate `/dev/snd/by-path/` links in the YAML editor and keep USB ports fixed.

Optional USB serial relays can switch an amplifier's 12 V trigger. Select the
relay device and model, then assign the zones that use that amplifier. See the
[amplifier guide](docs/amplifier-triggers.md) for supported boards and wiring.

The app checks channel availability and conflicts before starting. It leaves
hardware profiles, sample rates and output levels alone.

See the [app guide](local_audio_zones/DOCS.md),
[hardware requirements](docs/audio-hardware-support.md) and
[routing guide](docs/pulseaudio-zones.md) for details and current limits.

## Development

```sh
docker build -t local-audio-zones local_audio_zones
scripts/smoke_test.sh local-audio-zones
```

CI checks configuration, supervision, routing and AppArmor on amd64 and aarch64.
For local testing on Home Assistant OS, copy `local_audio_zones/` to
`/addons/local_audio_zones/`, remove `image` from the copy's `config.yaml`,
then reload the app store. This builds your local source instead of pulling
the published image.

## Credits

Based on [Music Assistant Local Audio](https://github.com/music-assistant/local-audio-addon)
and its [native-player update](https://github.com/music-assistant/local-audio-addon/pull/34).
Playback uses [Sendspin](https://github.com/Sendspin/sendspin-cpp-cli), with a
[small routing patch](local_audio_zones/patches/README.md).

This is an independent community app. Upstream licenses and notices are
retained in [LICENSE](LICENSE) and [NOTICE](local_audio_zones/NOTICE).
