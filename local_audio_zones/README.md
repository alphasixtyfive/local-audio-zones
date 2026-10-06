# Local Audio Zones

Create independent Music Assistant players for your soundcard outputs.

1. Open **Settings / Apps / Local Audio Zones / Configuration**.
2. Add a room with a stable **Player ID** and **Room name**.
3. Select its detected **Soundcard device** and **Output pair**.
4. Repeat for each room, **Save**, then **Restart** from the Info tab.

Prefer a `/dev/snd/by-id/` soundcard selection. The native picker also lists
control, playback, capture, sequencer and timer devices; only soundcard control
or playback devices can be used. Playback nodes select their containing card.
Each selected pair must exist in the card's
active audio profile. The Log tab explains invalid selections and names the
resolved PulseAudio output.

For identical cards sharing a by-id identity, use **Configuration / Edit in
YAML** to set each room's `device` to its separate `/dev/snd/by-path/` link.
These links are accepted but are not offered by the native picker. Keep the
cards on fixed physical ports and verify their outputs after a full reboot.
Numeric control/playback selections can change when card order changes.

| Pair | Channels |
| --- | --- |
| Front left/right | `front-left,front-right` |
| Rear left/right | `rear-left,rear-right` |
| Side left/right | `side-left,side-right` |
| Centre/Subwoofer | `front-center,lfe` |

For other channel layouts, leave **Output pair** unset and enter two distinct
names in **Custom channels**, for example `aux0,aux1`. The Log tab lists the
selected card's available channels. Both standard and custom selections are
validated against that list; the native form's standard choices do not change
with the selected card. Rooms cannot share physical channels.

The app reuses matching stereo outputs or creates standard PulseAudio remaps.
It preserves hardware profiles, rates and output levels, and removes only
remaps it created when stopping. CENTER/SUB requires hardware that outputs both
channels at full range. A headphone socket may share FRONT rather than provide
another independent pair.

Keep IDs when renaming rooms. Optional port, log level, server discovery, buffer
and start/stop commands can be set per room. Logging, server, buffer and commands
inherit the app settings when omitted. **Explicit output** accepts an existing
native player output instead of selecting a sound device.

Music Assistant creates standard parent players for the room outputs and
controls playback, volume, mute and grouping. Keep its minimum
volume at zero for silence at zero. Start quietly and calibrate maximum loudness
at the amplifier or host output. An empty room list waits without creating a
player.

Home Assistant also shows a global Audio panel because the app uses its managed
PulseAudio connection. That panel cannot be hidden through app configuration.
Leave its input and output at Default; room selections control routing.

## Amplifier triggers

Optionally add USB serial relays under **Amplifier triggers**. Select the relay
device and model, then list the Player IDs of the rooms using that amplifier.
Any receiving room keeps it on; all rooms must be idle for the standby delay
before it switches off. No relay entries means the feature is disabled.

Supported protocols are DSD TECH SH-UR01A, KMtronic one-channel and LCUS binary.
Use a stable serial path and select only the relay device, never the Zigbee port.
The USB chipset does not determine its protocol. Save and restart after changes.

Missing playback status holds the current demand. Relay failures are logged and
retried without stopping audio. The app attempts to switch off configured
channels on orderly shutdown. Physical relay testing is still required;
automatic wake-up can miss initial audio while the amplifier starts.

See the [amplifier guide](https://github.com/alphasixtyfive/local-audio-zones/blob/main/docs/amplifier-triggers.md)
for wiring, supported hardware and configuration examples.
