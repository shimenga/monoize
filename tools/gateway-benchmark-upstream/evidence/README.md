# Local qualification evidence

`windows-qualification.json` records a failed local qualification run on 2026-09-30.
The run used a release executable, six runtime workers, and 500000 history rows.
The host had an Intel i5-10200H CPU with four cores and eight logical processors.
The gateway runtime code corresponds to the fixes in Libra1337/monoize PR #52.
The report records the local Git revision and executable, runner, and fixture hashes.

Reference calibration passed all 22000 requests in its 60-second load window.
Maximum dispatch lateness was 21.25 ms. Upstream-duration p99 was 1307.80 ms.

| Gateway load | Successful requests | Scheduled requests | TTFB p99 (ms) | Overhead p99 (ms) |
| --- | ---: | ---: | ---: | ---: |
| 100 RPM | 84 | 100 | 15018.43 | 28561.09 |
| 500 RPM | 171 | 500 | 15015.14 | 28554.64 |
| 1000 RPM | 176 | 1000 | 15004.61 | 28388.33 |
| 2000 RPM | 157 | 2000 | 15023.29 | 28636.05 |

Latency percentiles include successful requests only. Errors fail the qualification separately.
Some higher-rate windows also failed the dispatch-lateness gate.
Treat those windows as diagnostic observations.

The run continued for over 650 seconds. It recorded 5752 slow connection-acquire warnings during 2000 RPM traffic.
Peak gateway CPU exceeded the configured budget. Maximum observed WAL size was 13756712 bytes.
The report does not establish production capacity.

Run `python scripts/gateway_benchmark.py --profile qualification` from the repository root to collect a new report.
Use the host, binary hash, configuration, and failed gates when comparing results.
Database investigation remains deferred; this change adds the measurement tool.
