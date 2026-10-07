# Hardware notes

Support depends on the host driver, active audio profile and reported channel
map. The app does not identify cards by model or assume socket numbering.

## Device identity

Use `/dev/snd/by-id/` when the card has a unique serial number. Identical cards
may share that identity; use separate `/dev/snd/by-path/` links through
**Configuration / Edit in YAML** and keep USB ports fixed. The native picker
does not list by-path aliases. VM USB topology changes can change the guest
path, so check each output after a full reboot.

A control node selects a card. A playback node selects a PCM endpoint using the
host's `alsa.card` and `alsa.device` properties. If several active outputs match,
the app reports their names rather than choosing one. Select the intended
playback device or use a named stereo sink through **Explicit output**.

## Channel layouts

| Hardware | Requirement |
| --- | --- |
| Stereo DAC | One zone on its reported left/right channels. |
| Multichannel card | An active profile exposing independent stereo pairs. |
| Professional interface | Custom pairs using its reported channels, including `aux` positions. |
| Several PCMs, analogue or HDMI outputs | A uniquely selected active PCM endpoint. |
| Mono output | Device-based zones require two distinct channels. |
| Headphone socket | May duplicate another pair. |
| Centre/Subwoofer | Both channels must provide full-range sound for stereo. |

The app uses PulseAudio stereo remaps with `remix=no`. It preserves host
profiles, rates and hardware levels. Channel names come from the active output;
fixed socket numbers would assume an ordering the app cannot know.

## USB recovery

The host must detect unplugged and reconnected cards. Keep PulseAudio's native
`module-udev-detect` enabled; manually loading cards at startup does not provide
hotplug detection. If running Home Assistant in a VM, the hypervisor must also
return the USB device to that VM.

The app restores its own routes when the selected card returns. Players retry
the same output through long and repeated disconnections without falling back
to another zone. Missed playback is not replayed. An **Explicit output** must
be restored by the host that created it.

Test each jack separately and together, then check restart, unplug/reconnect,
zero volume and usable maximum volume. Software checks cannot establish
analogue quality or compatibility with every card.

## Virtualised USB audio

Some cards need host driver settings to avoid distorted playback. An audio
buffer setting in this app does not repair a host driver problem. A tested
Cubilux CA7 setup uses interrupt scheduling, 48 kHz and an eight-channel profile:

```udev
SUBSYSTEM=="sound", KERNEL=="card[0-9]*", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="4f25", ENV{ID_PATH}=="?*", ENV{PULSE_NAME}="$env{ID_PATH}", ENV{PULSE_MODARGS}="tsched=no rate=48000 profile=output:analog-surround-71"
```

On HAOS, this can be saved in `/etc/udev/rules.d/99-usb-audio.rules`.
`PULSE_NAME` distinguishes identical cards by port. These settings are specific
to that hardware; other cards may work with their defaults. Back up host rules,
remove conflicting static card loads, reload the rules and restart audio once.
Check profiles, channel mappings and levels after reconnection.

References: [PulseAudio card overrides](https://github.com/pulseaudio/pulseaudio/blob/v17.0/src/modules/alsa/module-alsa-card.c),
[native device detection](https://github.com/pulseaudio/pulseaudio/blob/v17.0/src/modules/module-udev-detect.c),
[HAOS persistent rules](https://github.com/home-assistant/operating-system/blob/dev/buildroot-external/rootfs-overlay/usr/lib/systemd/system/etc-udev-rules.d.mount).
