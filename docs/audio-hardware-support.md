# Audio hardware support

Local Audio Zones maps stereo players to channels reported by HAOS PulseAudio.
It does not identify cards by model or assume that a printed socket label
corresponds to a particular channel. Support depends on the host driver, active
profile and exposed channel map, not the advertised number of sockets.

## Select the device and channels

The native Home Assistant picker detects sound devices. Prefer `/dev/snd/by-id/`
for a unique device identity. For identical cards with the same serial, use
**Configuration / Edit in YAML** to enter separate `/dev/snd/by-path/` links
in each room's `device`. Keep their physical USB ports unchanged. The native
picker offers by-id or numeric device paths, not by-path aliases; those aliases
are nevertheless accepted by native configuration. These are standard [systemd ALSA device rules](https://github.com/systemd/systemd/blob/main/rules.d/60-persistent-alsa.rules).
Changing a VM's USB topology can change the guest path; verify each room after
a full reboot. Numeric control/playback nodes are supported but their card
numbers can change.

A playback node selects its containing card, not a particular PCM on that card.
The device picker includes capture, sequencer and timer nodes, which are rejected.
Soundcard selection requires one unambiguous active physical PulseAudio sink.
Cards exposing several active PCM outputs need an explicitly named stereo sink
through **Explicit output**, such as `pulse:my_stereo_sink`.

Choose Front left/right, Rear left/right, Side left/right or Centre/Subwoofer.
For another layout, leave Output pair unset and enter two distinct **Custom
channels**, such as `aux0,aux1`. Their order defines the room's left and right.
The Log tab lists the selected sink's actual available channels. Unsupported or
overlapping assignments are rejected before players start.

Home Assistant's app form supports a detected device picker and fixed enum
choices. It cannot filter the pair choices after selecting a card. Validation
uses the actual channel map; the form does not promise that every listed pair
exists on every card. This keeps configuration in the native app settings.

## Hardware limits

| Hardware or layout | Requirement |
| --- | --- |
| Ordinary stereo USB DAC | One room on its reported left/right channels. |
| 6/8-channel analog card | Independent pairs only when its active profile exposes those channels. |
| 16-channel or other professional interface | Custom pairs can use reported `aux` channels; requires a unique active sink exposing them. |
| Mono device | Device-based room mapping requires two distinct channels; mono mapping is outside this mode. |
| Multiple PCMs or HDMI and analog outputs on one card | Do not guess which output to use; configure a named stereo sink explicitly. |
| Headphone socket | May duplicate another output rather than add independent channels. |
| Centre/Subwoofer socket | Software remapping does not remove a hardware crossover or turn a filtered subwoofer output into full-range stereo. |

PulseAudio defines mono, standard surround and auxiliary positions in its
[channel-map API](https://github.com/pulseaudio/pulseaudio/blob/master/src/pulse/channelmap.h).
Its ALSA profiles can contain [multiple output mappings](https://github.com/pulseaudio/pulseaudio/blob/master/src/modules/alsa/alsa-mixer.h).
These capabilities explain why neither socket counts nor a card's ALSA index
are sufficient to choose an output automatically.

The app reuses matching stereo remaps or creates standard
[`module-remap-sink`](https://github.com/pulseaudio/pulseaudio/blob/master/src/modules/module-remap-sink.c)
outputs with `remix=no`. HAOS owns profiles, sample rates, scheduling and physical
volume. The app preserves them. Configure the host first if its default profile
hides channels or its driver needs a particular rate. Restart the app after
changing the host routing.

## Recovery and verification

Process health does not prove that an audio device is available. A responsive
player with a missing output must not trigger a global watchdog restart that
interrupts rooms on other cards.

An unplugged device cannot be replaced by a different room's default output.
Native recovery is bounded for the current stream; a new stream or app restart
may be necessary after repeated interruptions. Remaps must exist again when
the card returns. Automatic topology reconciliation is not provided.

Test each physical output separately, then together. Check a full VM reboot,
zero volume, useful maximum volume and amplifier wiring. Digital routing tests
verify channel isolation; they do not establish analog quality, bass response
or hardware compatibility for every card.

Upstream requests include [multiple DAC outputs](https://github.com/music-assistant/local-audio-addon/issues/24),
[USB reconnect recovery](https://github.com/music-assistant/local-audio-addon/issues/28)
and [multichannel format restrictions](https://github.com/music-assistant/local-audio-addon/issues/32).
The latter concerns a multichannel player stream; this app instead gives each
stereo room its own player. Keep these different requirements separate when
reporting hardware results upstream.

## Device identity

Keep device identity, playback endpoint and channel selection separate. ALSA
`hw:CARD=...,DEV=...` selects a PCM endpoint; it does not select its left/right
channels. PulseAudio's named positions describe channels on that endpoint,
including `aux` positions for layouts without surround labels. Replacing these
names with fixed socket numbers would assume an ordering the app cannot know.
