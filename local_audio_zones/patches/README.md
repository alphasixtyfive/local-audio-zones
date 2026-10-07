# Native audio patches

`0001-pulse-pin-named-output.patch` applies to sendspin-cpp-cli v0.2.0,
commit `e980128a9c37229cbc28c764e9f2fd65d1c5edb6`.

An explicitly selected `pulse:<sink>` must stay on that output. PulseAudio can
otherwise move an active stream to its default output when the selected sink
disappears. The patch sets the standard `PA_STREAM_DONT_MOVE` flag for named
outputs. The unnamed `pulse` output continues to follow normal server routing.

When a named sink disappears, PulseAudio terminates that stream. The player's
recovery logic retries the same sink. Remove this patch when the pinned
upstream release includes the fix.

Sources: [upstream PulseAudio backend](https://github.com/Sendspin/sendspin-cpp-cli/blob/e980128a9c37229cbc28c764e9f2fd65d1c5edb6/src/pulse_sink.cpp),
[PulseAudio stream flags](https://github.com/pulseaudio/pulseaudio/blob/v17.0/src/pulse/def.h).

`0002-recover-long-and-repeated-outages.patch` applies to the same CLI commit.
The original recovery state machine exhausts its retries after about a minute
and cannot recover a second outage in the same stream. The patch retains the
existing capped backoff and resets its delay after recovery. It preserves the
player connection and transport state.

`sink_recovery_test.cpp` checks long and repeated outages, retry timing and
cancellation. Both architecture builds compile and run it against the patched
source. The patch also updates the upstream unit tests.
