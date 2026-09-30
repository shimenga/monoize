# Gateway Performance Budget Specification

## 0. Status

- **Purpose:** State the throughput and latency budget the Monoize forwarding gateway
  MUST meet on the production node, and the benchmark that proves it.
- **Scope:** The `monoize` process between downstream request arrival and upstream
  dispatch, and between upstream completion and downstream response completion. Upstream
  generation time is excluded. Database, spool, and runtime configuration are in scope.

## 1. Load model

GPB1. The budget load is `R` requests per minute, `1 <= R <= 2000`, mixed as:
- streaming generation requests (the reference benchmark drives 100% streaming; a
  non-streaming minority does not add gateway-side database work per request),
- at most 200 distinct API keys,
- at least one key contributing 90% of the volume (burst concentration).

GPB1a. The reference benchmark MAY additionally drive dashboard aggregate polling
at 1 request per 10 seconds; when it does not, the dashboard aggregate cache TTL
(DPT-DA2) still bounds the same queries' read-pool footprint in production.

GPB2. The benchmark upstream responds with a fixed SSE body at a constant 200 tokens
per second and 300 ms time-to-first-byte. The benchmark upstream MUST NOT be the
bottleneck; its idle capacity MUST exceed the offered load by at least 10x.

## 2. Latency budget

GPB3. Gateway overhead is `request_duration - upstream_duration` measured at the
benchmark client. For requests served at any load in GPB1, gateway overhead MUST
satisfy: p50 <= 50 ms, p90 <= 150 ms, p99 <= 500 ms, max <= 2000 ms.

GPB4. Time-to-first-byte observed by the client MUST satisfy: p50 <= upstream TTFB +
50 ms, p99 <= upstream TTFB + 500 ms.

GPB5. At the load boundary `R = 2000`, for a 60-second window: requests that fail
with `gateway_saturated` (RRB-FA2) MUST be less than 0.1% of offered requests, and
requests that fail with any other 5xx MUST be zero.

## 3. Resource bounds under budget load

GPB6. At `R = 2000`, process CPU MUST remain below 70% of the configured worker
capacity (worker threads x 100%).

GPB7. At `R = 2000`, sqlx connection-pool acquire waits logged by the slow-acquire
threshold MUST be zero in any 60-second steady-state window.

GPB8. The WAL file MUST NOT exceed `MONOIZE_SQLITE_JOURNAL_SIZE_LIMIT_BYTES` in
steady state, measured 10 minutes after the benchmark starts.

## 4. Benchmark

GPB9. The benchmark MUST run the production binary with a SQLite database seeded
with at least 500000 `request_logs` rows attributed to the concentrated key of GPB1,
so spend-window aggregates exercise realistic history depth.

GPB10. The benchmark MUST ramp `R` through 100, 500, 1000, and 2000, holding each
step for at least 60 seconds, and report the GPB3/GPB4/GPB5 metrics per step.

GPB11. The benchmark result is reproducible: a script in the repository builds the
seed database, starts the process, drives the load, and prints one JSON summary.

GPB12. `scripts/gateway_benchmark.py` MUST own the benchmark process, mock
upstream, and temporary fixture directory. It MUST bind the upstream and gateway
to literal loopback addresses. It MUST NOT use a configured production database,
external upstream, inherited Monoize environment configuration, or an HTTP proxy.
The fixture MUST enable a finite key spending limit and assign at least 500000
historical rows to the exercised key in qualification mode. A `smoke` profile MAY
use smaller fixtures and shorter steps; it MUST NOT claim qualification.

GPB13. Before qualification, calibrate the mock upstream at 22000 requests per
minute for 60 seconds. Every calibration request MUST complete its SSE terminal
and report 200 generated tokens. The p99 mock completion time MUST not exceed
its scheduled 1300 ms duration by more than 50 ms. A failed calibration invalidates
the benchmark. Emit the first SSE frame at 300 ms, then ten tokens every 50 ms
for twenty content frames. Emit terminal usage and `[DONE]` after those frames.
Reuse idle HTTP/1.1 connections within each load step. Close them after the step.
Do not equate client ephemeral-port exhaustion with mock upstream capacity.
Run the native mock upstream from `tools/gateway-benchmark-upstream` in a
separate process with four runtime worker threads. Transfer per-request timing observations
to the load generator without including that transfer in client request duration.
An idle keep-alive connection may expire without counting as a failed request.
The runner MUST count malformed or incomplete benchmark exchanges as request
failures. Closing an unused idle connection MUST NOT count as a failed exchange.
Divide calibration traffic across four load-generator processes with one shared
monotonic start time and uniformly staggered request schedules. Aggregate raw
samples and request counts; do not average worker percentiles.

GPB14. Record scheduled, dispatched, successful, saturated, other-5xx, and other
failed requests separately. Count saturation only for HTTP 503 with JSON error
code `gateway_saturated`. A truncated stream, SSE error, missing terminal usage,
missing `[DONE]`, or incorrect token count MUST be a failure even with HTTP 200.
The scheduled count in each step is `floor(rpm * step_seconds / 60)`.
No scheduled request may disappear from the report. Dispatch lateness above 50 ms
invalidates the offered-load evidence. Empty samples MUST NOT produce zero latency.
Windows load-generator processes MUST request a 1 ms timer period during their
run and release that request on exit. Record this setting in the report. A failed
timer-period request MUST fail the runner rather than silently change the load.
Use a dedicated absolute-deadline scheduler for request dispatch. HTTP parsing and
response validation MUST NOT execute in that scheduler.

GPB15. Match each client request to its mock upstream observation with a unique
request marker. Subtract the observed upstream duration from that request's total
duration for GPB3. Do not substitute a nominal duration for an absent observation.
Report CPU samples from the gateway child process, normalized by its configured
worker count. Sample the WAL at or after 600 seconds of gateway load. Qualification
MUST continue at 2000 RPM until that sample exists. Count slow-acquire warnings
from the gateway log; missing resource evidence invalidates qualification.

GPB15a. Every load step MUST retain the mock timing check: upstream-duration p50
MUST be at least 1290 ms and p99 MUST be at most 1350 ms. A slow mock MUST NOT
be hidden by subtracting its delay from gateway overhead. Calibration TTFB p50
MUST be at least 290 ms, so an early mock frame cannot establish qualification.

GPB16. A qualification run MUST build the release executable with Cargo, record
its SHA-256 and Git revision, and identify the host and operating system in JSON.
It MUST report every failed gate and exit nonzero on any failed gate. A successful
local report proves the measured host only; it does not establish production-node
capacity. An explicitly supplied binary MAY be used for smoke verification.
Preserve reports and logs on failure. Stop only the child process created by the
runner, after its benchmark requests finish or the run fails. The existing
in-process Rust benchmark is a supplementary diagnostic, not GPB9 evidence.
