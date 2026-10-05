# Stereo zones on a multichannel soundcard

Local Audio uses named PulseAudio outputs. Configure the soundcard and its
stereo outputs in the host audio service; the app needs no direct USB or ALSA
device access. Each zone runs the same Sendspin player.

Select the card's multichannel output profile, then create a remap for each
stereo pair. Use the actual names from `pactl list short cards` and
`pactl list short sinks`:

```text
set-card-profile CARD output:analog-surround-71
load-module module-remap-sink sink_name=study master=MASTER channels=2 channel_map=front-left,front-right master_channel_map=front-left,front-right remix=no
load-module module-remap-sink sink_name=guest_room master=MASTER channels=2 channel_map=front-left,front-right master_channel_map=rear-left,rear-right remix=no
load-module module-remap-sink sink_name=kids_room master=MASTER channels=2 channel_map=front-left,front-right master_channel_map=side-left,side-right remix=no
load-module module-remap-sink sink_name=bedroom master=MASTER channels=2 channel_map=front-left,front-right master_channel_map=front-center,lfe remix=no
```

Replace `CARD` and `MASTER` with the discovered card and its multichannel
sink. Persist these commands in the host's PulseAudio startup configuration.
Home Assistant OS reads `/mnt/data/supervisor/audio/custom.pa` after its normal
configuration. Back up this file separately from Home Assistant configuration.
Use `.nofail` around optional USB-card commands so an absent card does not
prevent the audio service from starting.

`remix=no` preserves independent pairs. The center/subwoofer pair is usable
as a stereo output only if the hardware exposes both channels at full range;
verify it with the connected amplifier. Headphone outputs often share the
front pair, so a connector count does not establish another independent zone.

Configure the app with a stable ID and explicit sink for each room:

```yaml
zones:
  - id: study
    name: Study
    output: pulse:study
  - id: guest_room
    name: Guest room
    output: pulse:guest_room
```

Names can change; keep IDs when renaming rooms so Music Assistant retains their
configuration. Ports default to 8928 plus the list position. Specify unique
ports when another player uses those ports or when stable ports are needed
across reordering. Use unique sink names and IDs for additional soundcards.

Leave player format and buffer settings unset initially. PulseAudio performs
format conversion and the player selects its normal buffer. Calibrate fixed
hardware output levels separately from Music Assistant's everyday room volume.
Test zero volume, each physical jack, simultaneous playback, restart and device
loss before putting the installation into service.
