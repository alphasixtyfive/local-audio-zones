# USB amplifier triggers

The app can control an amplifier's low-voltage trigger through a USB serial
relay. This is optional; leave **Amplifier triggers** empty to disable it.

## Supported protocols

| Model | Interface | Channels |
| --- | --- | --- |
| [DSD TECH SH-UR01A](https://www.deshide.com/product-details_SH-UR01A.html) | AT commands, USB serial | 1 |
| [KMtronic U1CRB](https://kmtronic.com/product/2764/usb-relay-controller-one-channel-box.html) | `FF 01 00/01`, USB serial | 1 |
| LCUS binary relay boards | `A0 channel state checksum`, USB serial | 1–8 |

All use 9600 baud, eight data bits, no parity and one stop bit. Select the
documented protocol of the board, not its USB chipset. A CP2102, CH340 or FTDI
chip can also be used in unrelated devices. LCUS is not Modbus.

This implementation has automated serial-emulator coverage, but still needs
physical relay testing. DSD TECH commands require an `OK` acknowledgement;
KMtronic and LCUS report a successful command write, not physical contact
readback. HID, FTDI bitbang, Modbus and CasaTunes trigger cards are not supported.

## Home Assistant alternative

If the amplifier's relay already appears as a Home Assistant switch, use
[Music Assistant's Home Assistant plugin](https://www.music-assistant.io/ha-plugin/#linking-home-assistant-entities-to-player-controls).
Select the switch under power controls, then select it in the player's
**Player Controls** settings. No USB trigger configuration is needed in this app.

For an amplifier shared by several rooms, use one Home Assistant automation
to own the switch: turn it on when any assigned room starts playing and off
after all assigned rooms have stopped for the standby delay. Treat unavailable
room states as unknown, not stopped. Do not assign the same physical switch
independently to every player's power control; one room could turn it off
while another is still playing. Use either this automation or the app's USB
controller for a relay, so only one system owns it.

This works with any relay integration that exposes an on/off switch in Home
Assistant. Hardware-specific commands stay in that integration.

## Configure

1. On Proxmox, pass the relay's USB device into the Home Assistant VM. Keep it
   separate from the Zigbee coordinator and USB soundcard.
2. In **Settings / System / Hardware / All hardware**, identify its serial port.
3. In the app's **Configuration / Amplifier triggers**, add an amplifier name,
   its **USB relay device**, and matching **Relay model**.
4. Add the **Player IDs** of every room using that amplifier. These are the IDs
   in **Zones**, not the display names in Music Assistant.
5. Set **Standby delay** if needed; it defaults to 60 seconds. Save and restart.

Prefer `/dev/serial/by-id/`. For identical boards without unique serial IDs,
use `/dev/serial/by-path/` in **Edit in YAML** and keep physical ports fixed.
Numeric `/dev/ttyUSB0` selections can change after a reboot. Use one device
path consistently for every channel on the same board. The app never scans
serial ports or guesses a relay protocol.

```yaml
usb_relays:
  - name: Dayton amplifier
    device: /dev/serial/by-id/usb-your-relay-identifier
    protocol: DSD TECH SH-UR01A
    zones:
      - sendspin-study
      - sendspin-guest-room
      - sendspin-kids-room
      - sendspin-bedroom
    off_delay: 60
```

Change the example device and player IDs to your actual selections. Channel
defaults to 1. For an LCUS board, add an entry per assigned channel, all using
the same device and protocol. Unassigned channels are left alone. Configuration
rejects unknown players, duplicate channel assignments and incompatible models.

## Behaviour

One receiving room switches its assigned relay on. Only when every assigned
room has a known idle stream for the full standby delay does it switch off.
Pausing or removing a room from a group stops its demand when its stream stops.
Mute and volume zero do not turn the amplifier off while a stream is receiving.

The service reads each player's local status socket. It does not modify audio
buffers, hooks, routing or volume. Detection runs approximately twice a second;
the amplifier's wake-up time can clip the beginning of playback or announcements.
There is no automatic pre-roll or warm-up delay.

A missing player status holds the current demand and cancels the idle timer.
After startup or a service restart, a channel is left untouched until a room
is receiving or every assigned room has been idle for the standby delay.
A missing relay is retried every five seconds without stopping the audio
players. Reconnection reapplies the current demand. An orderly app shutdown
attempts to switch off configured channels. A server crash or unplugged relay
cannot guarantee that the hardware contacts open; choose suitable hardware
power-loss behaviour for your installation.

The Log tab reports missing player status, relay connection failures and command
changes. The app health check verifies that the trigger service is running;
it does not treat an unplugged optional relay as a reason to restart all rooms.

## Wiring a 12 V trigger

The USB relay switches a separate 12 V DC source. It does not produce 12 V.

| Connection | Destination |
| --- | --- |
| Supply positive | Relay COM |
| Relay NO | Amplifier trigger plug tip |
| Supply negative | Amplifier trigger plug sleeve |

Leave NC unused. Disconnect power before wiring and check polarity before
connecting the amplifier. Follow the amplifier's manual for trigger voltage,
connector and power-mode selection. These instructions cover a low-voltage
trigger connection, not switching the amplifier's mains power.

## Development

Run `python3 scripts/usb_triggers_test.py` in the built image. Tests use fake
serial transports, Unix status sockets and Linux pseudo-terminals; they do not
require a physical relay or operate any connected hardware.

To add a USB serial relay protocol:

1. Add a named frame encoder and `Protocol` entry in `relay_protocols.py` using
   the board's documented commands, channel count and baud rate. Include its
   handshake and acknowledgement when needed.
2. Add its name to the protocol selector in `config.yaml`. Adjust the channel
   selector's upper bound if the protocol supports more channels.
3. Add known on/off frames and a transport test to `usb_triggers_test.py`,
   document the board here and verify it on hardware.

Player status, standby logic and reconnection stay in `usb_triggers.py`.
The current transport uses USB serial with 8N1 framing. HID and FTDI bitbang
boards need a different transport, not another serial protocol entry.

The LCUS wire format and shared-amplifier behaviour were checked against
[Multi-Room Audio's .NET implementation](https://github.com/chrisuthe/Multi-SendSpin-Player-Container).
The controller here is independent and uses current player state instead of
counting start and stop events.
