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

If identical cards share a by-id identity, select an unambiguous control or
playback device instead and verify its physical output after each restart.

| Pair | Channels |
| --- | --- |
| `front` | Front left/right |
| `rear` | Rear left/right |
| `side` | Side left/right |
| `center_sub` | Front center/LFE |

The app reuses matching stereo outputs or creates standard PulseAudio remaps.
It preserves hardware profiles, rates and output levels, and removes only
remaps it created when stopping. CENTER/SUB requires hardware that outputs both
channels at full range. A headphone socket may share FRONT rather than provide
another independent pair.

Keep IDs when renaming rooms. Optional port, log level, server discovery, buffer
and start/stop commands can be set per room. Logging, server, buffer and commands
inherit the app settings when omitted. **Explicit output** accepts an existing
native player output instead of selecting a sound device.

Music Assistant controls playback, volume, mute and grouping. Keep its minimum
volume at zero for silence at zero. Start quietly and calibrate maximum loudness
at the amplifier or host output. An empty room list waits without creating a
player.

Home Assistant also shows a global Audio panel because the app uses its managed
PulseAudio connection. That panel cannot be hidden through app configuration.
Leave its input and output at Default; room selections control routing.
