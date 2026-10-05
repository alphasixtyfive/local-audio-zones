# Local Audio Zones

Create independent Music Assistant players for your audio outputs.

1. Start the app and select **Open Web UI**.
2. Add a room, enter its **Name**, and choose an **Output**. Repeat for each room.
3. Select **Check configuration**, **Save configuration**, then **Restart app**.
   The players appear in Music Assistant with the names you chose.

Outputs must already exist in Home Assistant's audio service. A multichannel
soundcard needs a stereo remap for each room. The app does not change hardware
profiles, wiring or output levels.

Keep player IDs when renaming rooms. **Advanced settings** offers per-room
ports, logging, server discovery, buffer and playback commands. Defaults inherit
from the app's **Configuration** tab. Saving stores settings; a restart applies
them. Checks confirm output availability and player responses, not audible
sound quality.

Volume, mute and grouping belong to Music Assistant. Start quietly and keep its
minimum volume at zero if zero should mean silence.

If no rooms are configured, the app uses one player and Home Assistant's **Audio**
selector. Named rooms use the outputs selected in the editor instead.
