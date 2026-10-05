# Stereo outputs on a multichannel soundcard

Select a detected soundcard and channel pair in each room's native app settings.
The app resolves the selected device to its PulseAudio card and checks the
active output's channel map. It reuses a matching stereo remap or creates one
with `remix=no`, keeping each room on its two selected channels.

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
adding another card. A USB port assignment by itself does not guarantee a stable
PulseAudio or ALSA identity.
The app rejects a by-id selection when multiple PulseAudio cards report the same
nonempty serial. Explicit control or playback paths remain available, with the
same requirement to verify their physical mapping after a restart.

The app preserves existing output levels, including levels on reused remaps.
Calibrate fixed hardware levels separately from everyday Music Assistant volume.
Leave buffers unset initially. Test each jack, concurrent playback, zero volume,
restart and device loss before relying on the installation.

App-created remaps are removed on a normal stop; pre-existing host remaps remain.
Named players stay on their selected sink when hardware disappears. Native
recovery is bounded per stream, so restoring a card after that window can require
starting another track. The app does not change host audio settings or run a
hardware-reconciliation service.
