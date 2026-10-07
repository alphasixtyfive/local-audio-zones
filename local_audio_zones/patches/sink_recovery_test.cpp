// SPDX-License-Identifier: Apache-2.0
// Compile this fixture with the patched upstream src/sink_recovery.cpp.

#include "sink_recovery.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>

using sendspin_cli::SinkRecovery;
using sendspin_cli::SINK_RESCAN_DELAY_MS;
using sendspin_cli::SINK_RESCAN_MAX_DELAY_MS;

namespace {

void require(bool condition, const char* message) {
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", message);
        std::exit(1);
    }
}

void fail_reopen(SinkRecovery& recovery) {
    require(recovery.reopen_due(), "a fresh stream offers one immediate reopen");
    recovery.reopen_done(false);
    require(recovery.pending(), "a failed reopen schedules delayed recovery");
}

int64_t attempt(SinkRecovery& recovery, int64_t now, int64_t delay) {
    require(!recovery.rescan_due(now), "a retry starts with a fresh deadline");
    require(!recovery.rescan_due(now + delay - 1), "a retry cannot fire early");
    require(recovery.rescan_due(now + delay), "a retry fires at its bounded deadline");
    require(!recovery.pending(), "an outstanding attempt is not also pending");
    require(!recovery.reopen_due(), "writes cannot rearm an outstanding attempt");
    require(!recovery.rescan_due(now + delay + 1), "an outstanding attempt cannot fire twice");
    return now + delay;
}

void long_and_repeated_outages() {
    SinkRecovery recovery;
    SinkRecovery unaffected;
    fail_reopen(recovery);
    int64_t now = 1'000'000;
    int64_t delay = SINK_RESCAN_DELAY_MS;
    // More than twenty minutes offline, without sleeping or reconfiguring.
    for (int i = 0; i < 48; ++i) {
        now = attempt(recovery, now, delay);
        recovery.rescan_done(false);
        require(recovery.pending(), "long outages retain a future retry");
        recovery.rescan_done(false);
        require(!unaffected.pending(), "another sink retains its own recovery state");
        delay = delay >= SINK_RESCAN_MAX_DELAY_MS / 2
                    ? SINK_RESCAN_MAX_DELAY_MS : delay * 2;
    }
    require(delay == SINK_RESCAN_MAX_DELAY_MS, "retry backoff reaches its ceiling");
    recovery.discard_frames(48'000);
    now = attempt(recovery, now, delay);
    recovery.rescan_done(true);
    require(!recovery.pending(), "success ends the current outage");
    require(recovery.take_discarded_frames() == 48'000, "success preserves the outage gap");
    require(recovery.take_discarded_frames() == 0, "the gap is retired exactly once");
    require(!recovery.rescan_due(now + SINK_RESCAN_MAX_DELAY_MS), "healthy sinks do not retry");

    // Repeated losses of the same configured stream must remain recoverable.
    for (int i = 0; i < 8; ++i) {
        require(!recovery.reopen_due(), "later outages use backoff rather than immediate reopen loops");
        require(recovery.pending(), "later outages schedule recovery without a new stream");
        now = attempt(recovery, now, SINK_RESCAN_DELAY_MS);
        recovery.rescan_done(true);
        require(!recovery.pending(), "each restored output ends its retry cycle");
    }
}

void flush_and_reset() {
    SinkRecovery recovery;
    fail_reopen(recovery);
    int64_t now = attempt(recovery, 2'000'000, SINK_RESCAN_DELAY_MS);
    recovery.rescan_done(false);
    recovery.discard_frames(960);
    recovery.forget_discarded_frames();
    require(recovery.take_discarded_frames() == 0, "a flush clears discarded frames");
    now = attempt(recovery, now, 2 * SINK_RESCAN_DELAY_MS);

    // Reconfiguration cancels both pending and outstanding old recovery.
    recovery.reset();
    recovery.rescan_done(false);
    require(!recovery.pending(), "late reports after reset cannot rearm recovery");
    require(!recovery.rescan_due(now + SINK_RESCAN_MAX_DELAY_MS), "reset cancels old deadlines");
    fail_reopen(recovery);
    now = attempt(recovery, now, SINK_RESCAN_DELAY_MS);
    recovery.rescan_done(false);
    recovery.reset();
    require(!recovery.pending(), "reset cancels a scheduled retry");
    require(!recovery.rescan_due(now + SINK_RESCAN_MAX_DELAY_MS), "no cancelled retry can fire");

    recovery.discard_frames(std::numeric_limits<uint32_t>::max() - 10);
    recovery.discard_frames(20);
    require(recovery.take_discarded_frames() == std::numeric_limits<uint32_t>::max(),
            "discarded frames saturate rather than overflow");
}

}  // namespace

int main() {
    long_and_repeated_outages();
    flush_and_reset();
    std::puts("Native sink recovery: long outages, repeated losses, isolation, backoff, gaps and reset passed.");
}
