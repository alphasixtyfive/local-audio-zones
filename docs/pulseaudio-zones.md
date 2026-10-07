# Stereo outputs on a multichannel soundcard

Select a detected soundcard and channel pair in each room's native app settings.
The app resolves the selected device to its PulseAudio card and checks the
active output's channel map. Each room gets an app-owned stereo route with
`remix=no`, keeping playback on its two selected channels.

The host owns the soundcard profile, sample rate and hardware levels. Activate
a profile that exposes the required channels before starting the app. A stereo
profile provides FRONT only; a suitable 7.1 profile usually provides all four
pairs. The app rejects missing channels rather than moving a room to another
output. Use its Log tab to identify the selection or profile that needs fixing.

`center_sub` maps full-range stereo directly to front-center and LFE without
filtering or remixing. It is useful only if the soundcard exposes both channels
at full range. Check the physical outputs with your amplifier. Headphones often
share the front pair, so connector count alone does not establish another room.

Prefer stable `/dev/snd/by-id/` selections. Cards with identical serial numbers
can have ambiguous identities; verify mappings after a full reboot and when
adding another card.
The app rejects a by-id selection when multiple PulseAudio cards report the same
nonempty serial. Set separate `/dev/snd/by-path/` links through **Configuration /
Edit in YAML** for cards on fixed physical ports; the native picker does not
offer those aliases. Explicit control/playback paths remain available, but
numeric card order can change. Verify physical mappings after a full reboot.

The app preserves hardware output levels.
Calibrate fixed hardware levels separately from everyday Music Assistant volume.
Leave buffers unset initially. Test each jack, concurrent playback, zero volume,
restart and device loss before relying on the installation.

## Device recovery

PulseAudio on the host must discover unplugged and reconnected devices. Keep
its native `module-udev-detect` enabled. Loading ALSA cards manually at startup
does not provide USB hotplug recovery.

When a selected soundcard disappears, its rooms stay assigned to that device.
The app restores their owned routes when the host exposes the device again;
players retry their named output with capped backoff through long and repeated
outages. They do not fall back to another room or restart the host audio service.
Playback missed during the outage is not replayed.

For cards selected by path, reconnect to the same physical USB port and retain
the VM's USB port assignment. ALSA card numbers may change without changing the
selected path. Moving a card to another port requires checking its selection.

**Explicit output** uses a host-managed named sink. The host must restore that
sink; the app cannot reconstruct an arbitrary external routing configuration.
App-owned routes are removed on a normal stop; unrelated host routes remain.

## Optional HAOS / QEMU driver settings

Some USB cards need different PulseAudio scheduling under virtualisation.
Keep such settings on the host, separate from room configuration. The following
example retains a tested Cubilux CA7 configuration: interrupt scheduling,
48 kHz and an eight-channel output. It is not a requirement for other cards,
and the CA7 also advertises 44.1 kHz.

On HAOS, a persistent rule such as
`/etc/udev/rules.d/99-usb-audio.rules` can configure both device discovery and
driver settings:

```udev
SUBSYSTEM=="sound", KERNEL=="card[0-9]*", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="4f25", ENV{ID_PATH}=="?*", ENV{PULSE_NAME}="$env{ID_PATH}", ENV{PULSE_MODARGS}="tsched=no rate=48000 profile=output:analog-surround-71"
```

`PULSE_NAME` gives identical cards separate names based on their ports.
PulseAudio 17 supports `PULSE_MODARGS` for per-card overrides, so native
discovery reapplies these settings after reconnection. No audio-container
filesystem changes or periodic restarts are needed.

Back up existing host rules and audio configuration before changing them.
Remove conflicting static card loads and any command that disables native
discovery. Reload the rules and restart audio once to apply the migration,
then verify profiles, sample rates and physical room mappings after reconnecting
each card. New sink names have separate saved-volume state; check calibration
before resuming playback.

Sources: [PulseAudio card overrides](https://github.com/pulseaudio/pulseaudio/blob/v17.0/src/modules/alsa/module-alsa-card.c),
[native discovery](https://github.com/pulseaudio/pulseaudio/blob/v17.0/src/modules/module-udev-detect.c),
[HAOS persistent rules](https://github.com/home-assistant/operating-system/blob/dev/buildroot-external/rootfs-overlay/usr/lib/systemd/system/etc-udev-rules.d.mount).
