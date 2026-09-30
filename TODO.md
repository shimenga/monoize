# TODO and delivery progress

Status checked: 2026-09-30. Target branch: `Libra1337/monoize:Monoize-Claude`.
Baseline: `f405eb2e257f872bcc65a562de3d957f19ff8e62`.

Database work is deferred at the user's request. Preserve the local database
patches. Do not deploy or activate CNY as part of the current delivery.
A completed checkbox means the stated deliverable is verified; it does not imply
that its PR is merged or deployed.

## Published progress

| Deliverable | Review | Current status |
| --- | --- | --- |
| Cache, routing invalidation, JPEG XL builds, locale labels, browser tests | [PR #52](https://github.com/Libra1337/monoize/pull/52) | Open; not merged |
| Executable capacity verifier, native mock, isolated fixtures, reports, four-locale instructions | [PR #53](https://github.com/Libra1337/monoize/pull/53) | Open; not merged; capacity qualification failed |

- [x] Synchronize the original checkout with the target branch and preserve local files.
- [x] Fix dashboard cache eviction deadlock, shared state, and concurrent capacity bounds.
- [x] Invalidate the routing snapshot after a successful Provider reorder.
- [x] Restore image builds with and without JPEG XL; honor its configured worker count.
- [x] Fill seven missing Traditional Chinese/Japanese UI labels.
- [x] Fix Windows browser fixture paths and align default-currency assertions with ORGL-20.
- [x] Compare protocol code with upstream `136c8023b6622bcffd941423b39ab2e6a78fc247`.
- [x] Verify codec, transform, handler, authentication, and settlement regressions.
- [x] Build an independent-process capacity runner with strict SSE and request accounting.
- [x] Record an actual failed qualification run instead of treating smoke as capacity proof.
- [x] Update affected operator documentation in all four locales and build the docs site.
- [x] Publish PRs #52 and #53 against `Monoize-Claude` and verify their file trees.
- [x] Publish this progress checklist in PR #52.

## Next: review and release acceptance

- [ ] Review PR #52 and resolve reviewer findings before merge.
- [ ] Review PR #53 and resolve verifier/evidence findings before merge.
- [ ] Obtain maintainer merge of each approved PR; verify the resulting target revision.
- [ ] Finish the remaining repository-wide verification targets from the historical plans.
  Do not label focused library/API results as a complete `cargo test` run.
- [ ] Verify documented UI flows and refresh English/Simplified Chinese screenshots
  where current flow changes require them. Existing browser checks do not replace screenshots.
- [ ] Run the isolated Linux routing-namespace verification required by deployment V1.
- [ ] Complete production release acceptance only after explicit deployment authorization.
  Use the blue-green swap, retain old streams through natural drain, and preserve evidence.

## Deferred: database and native CNY accounting

- [ ] Complete PostgreSQL smoke-test isolation and verify the full migration chain on PostgreSQL.
- [ ] Replace the unsafe cross-database cutover protocol with one authoritative writer.
  Prove that final synchronization cannot overwrite balances, resurrect deleted records,
  or lose terminal usage during overlapping streams.
- [ ] Finish schema, row-count, monetary reconciliation, restart, and rollback acceptance.
- [ ] Review and publish the local deployment-environment and migration patches separately.
  Their local Python/shell checks passed, but they are absent from PRs #52 and #53.
- [ ] Investigate the measured connection-pool waits and qualify the full gateway load.
- [ ] Run capacity qualification on the intended production-equivalent host.
- [ ] Complete CNY epoch activation, wallet/ledger normalization, pricing, long-request
  settlement, durable replay, compatibility APIs, frontend integration, and documentation.
  Existing exact-money/schema/state tests do not prove complete CNY integration.

## Decisions and historical acceptance still open

- [ ] Define cancellation and partial-usage charging before changing client-disconnect
  draining; current FP6i retains upstream draining for terminal usage.
- [ ] Decide per-customer rate-limit policy and tune admission from measured capacity.
- [ ] Gate B: satisfy Marketplace latency/write limits and rehearse a redacted production copy.
- [ ] Gate C: compare production pricing snapshots.
- [ ] Gate D: complete the status-event load and fault profiles.
- [ ] Gate E: obtain public-name approval and record topology preflight.
- [ ] Close the 17 historical plans against current specs and acceptance evidence.
  Keep later EPay requirements authoritative over retired adapter-kind plans.
  Do not execute obsolete PM2, old-host, or stop/restart deployment instructions.

## Verified results

| Check | Evidence |
| --- | --- |
| Rust library | 1132 passed; one manual image benchmark ignored |
| API integration | 457 passed |
| Additional contract targets | 55 passed across 8 targets |
| Store/account business targets | 259 passed across 20 targets |
| Image compression without JPEG XL | 15 passed; one manual benchmark ignored |
| Upstream synchronization | 330 codec and 9 transform tests included in library results; 2 focused integration tests passed |
| Frontend | 330 unit tests; 6 composer and 7 organization-limit browser cases passed |
| Build checks | Frontend lint/build and four-locale docs build passed |
| Capacity verifier | 19 self-tests passed, including the native upstream |
| Release smoke | 118/118 gateway requests succeeded across four load steps |
| Full local capacity | Failed; reference calibration passed 22000/22000 requests |

The local capacity run used 500000 historical rows and recorded over 650 seconds
of resource samples. It observed request timeouts, CPU/latency overruns, and 5752
slow-acquire warnings during 2000 RPM traffic. Some load steps also exceeded the
sender-lateness gate. These observations do not establish production capacity.

Read the [qualification report and limitations](https://github.com/shimenga/monoize/blob/077063a0c7bd5397dafc1b0de6672339f9f771b4/tools/gateway-benchmark-upstream/evidence/README.md).
PR #53 records the measured executable hash; that executable includes the fixes in
PR #52. The tool PR does not change application database or routing implementation.

Source plans: `docs/superpowers/plans/`, `HANDOFF-PG-CUTOVER.md`, and the matching
files directly under `spec/`. Treat handoff production observations as historical
until verified on the host. No production deployment was performed in this work.
