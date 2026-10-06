# Changelog

## 0.1.0

First release.

- Separate Music Assistant players for up to 32 zones on one or more soundcards.
- Native Home Assistant settings for zone names, devices and standard or custom channel pairs.
- Selection of individual playback endpoints on soundcards with several outputs.
- Validated routing that preserves host audio settings and keeps each zone on its selected output.
- Independent player recovery, saved state and health checks.
- Optional USB serial amplifier triggers with shared-zone control and delayed standby.

Based on [Music Assistant Local Audio](https://github.com/music-assistant/local-audio-addon)
and its [native-player update](https://github.com/music-assistant/local-audio-addon/pull/34).
Playback uses [Sendspin](https://github.com/Sendspin/sendspin-cpp-cli).
See [NOTICE](https://github.com/alphasixtyfive/local-audio-zones/blob/main/local_audio_zones/NOTICE)
for upstream licenses and attribution.
