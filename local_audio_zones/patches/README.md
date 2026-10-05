# Named PulseAudio outputs

`0001-pulse-pin-named-output.patch` applies to sendspin-cpp-cli v0.2.0,
commit `e980128a9c37229cbc28c764e9f2fd65d1c5edb6`.

An explicitly selected `pulse:<sink>` must stay on that output. PulseAudio can
otherwise move an active stream to its default output when the selected sink
disappears. The patch sets the standard `PA_STREAM_DONT_MOVE` flag for named
outputs. The unnamed `pulse` output continues to follow normal server routing.

When a named sink disappears, PulseAudio terminates that stream. The player's
existing recovery logic retries the same sink; no add-on routing monitor or
fallback is added. Remove this patch when the pinned upstream release includes
the fix.

Sources: [upstream PulseAudio backend](https://github.com/Sendspin/sendspin-cpp-cli/blob/e980128a9c37229cbc28c764e9f2fd65d1c5edb6/src/pulse_sink.cpp),
[PulseAudio stream flags](https://github.com/pulseaudio/pulseaudio/blob/v17.0/src/pulse/def.h).
