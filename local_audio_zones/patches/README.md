# Native audio patches

Both patches apply to sendspin-cpp-cli v0.3.0,
commit `bc0551c6f0c29ee1a773c4ea730bb20b71992a8a`.

`0001-pulse-pin-named-output.patch` uses `PA_STREAM_DONT_MOVE` to keep a named
output on its selected device when that device disconnects.

`0002-recover-long-and-repeated-outages.patch` keeps retrying through long
outages with the upstream capped backoff. The upstream player already recovers
from repeated outages; this patch removes its finite retry limit.

`sink_recovery_test.cpp` checks long outages, repeated losses, capped retry
timing, cancellation and discarded audio frames against the patched source.
Both architecture builds compile and run it.
