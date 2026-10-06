# Changelog

## 0.1.3

- Leave relay channels untouched until player status is known after a restart.
- Attempt shutdown on every assigned channel even if one command fails.
- Use named relay frame encoders and document adding another serial protocol.

## 0.1.2

- Keep relay protocol definitions separate from amplifier control.
- Accept serial device names and links without chipset-specific assumptions.
- Remove arbitrary relay-count and standby-delay limits.

## 0.1.1

- Optional USB serial amplifier triggers in native app settings.
- DSD TECH SH-UR01A, KMtronic one-channel and LCUS binary protocols.
- Shared amplifier assignments, delayed standby and USB reconnection.
- Validate relay devices, channels and player assignments before startup.

## 0.1.0

- Independent Music Assistant players for up to 32 rooms.
- Native Home Assistant configuration with detected sound devices, standard or custom
  stereo pairs and optional per-room player settings.
- Reuse existing stereo outputs or create standard PulseAudio remaps without
  changing hardware profiles, rates or levels.
- Separate saved state, process recovery and health checks for each room.
- Recover app-owned routes after an interrupted shutdown.
- Native Sendspin playback, with named outputs kept on their selected sink.
