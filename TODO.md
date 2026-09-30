# Remaining work

Baseline: `origin/Monoize-Claude` at `f405eb2e257f872bcc65a562de3d957f19ff8e62`.
The working files matched this revision when Git tracking was restored on 2026-09-30.

Unchecked historical plan steps do not prove that a feature is missing. Close each
item only after checking its implementation, specification, and verification evidence.

Current user direction: defer database work. Preserve the existing database-related
changes without deploying them. Prioritize protocol, transform, frontend, and
documentation work. CNY storage, database cutover, and database validation remain deferred.

- [x] Synchronize the checkout with `origin/Monoize-Claude` and preserve local changes.
- [ ] Repair PostgreSQL test isolation and verify the complete migration chain.
- [x] Implement and test deployment environment overrides required by BG5.
- [ ] Replace the unsafe cross-database cutover protocol. Preserve in-flight streams,
  establish one authoritative writer, and prove that final synchronization cannot
  overwrite new balances, resurrect deleted records, or lose terminal usage.
- [ ] Make migration schema, row, monetary, and health checks fail on missing evidence.
- [ ] Verify migration restart, failure recovery, and rollback against isolated databases.
- [ ] Complete the gateway benchmark and record GPB1 through GPB11 evidence.
- [ ] Verify upstream protocol synchronization against its three plan tasks.
- [x] Restore image compression builds with and without the optional JPEG XL feature.
- [x] Verify shared dashboard aggregate caching, bounded concurrent insertion, and
  eviction without lock re-entry. This is an in-memory cache change.
- [x] Verify Provider reorder invalidates the routing snapshot before returning.
- [x] Fill missing Traditional Chinese and Japanese labels for model redirects
  and the Japanese cache-write price label; verify non-plural locale-key coverage.
- [ ] Complete the seven CNY accounting plan tasks, including PostgreSQL activation,
  wallet reconciliation, long-request settlement, replay, compatibility APIs, and UI.
- [x] Inventory the remaining historical plans against current code and test results.
  Acceptance gaps remain open in the audit below.
- [ ] Run backend, frontend, browser, and documentation checks required by the changes.
- [ ] Update affected documentation in all four locales and recapture changed documented
  flows in English and Simplified Chinese.
- [ ] Complete delivery and production verification when deployment is explicitly
  requested and the relevant acceptance gates pass.

## Sources

- `HANDOFF-PG-CUTOVER.md`, remaining steps and open items.
- All 17 implementation plans under `docs/superpowers/plans/`.
- `spec/database-configuration.spec.md` and `spec/deployment-docker.spec.md`.
- `spec/gateway-performance-budget.spec.md`.
- `spec/upstream-protocol-sync.spec.md`.
- `spec/accounting-currency.spec.md`.

## Decisions still required

- Client disconnect cancellation changes the existing terminal-usage drain contract
  in FP6i. Define charging and partial-usage behavior before changing that contract.
- Per-customer rate limits require a business policy. Select the forwarding admission
  limit from measured capacity, rather than an unverified constant.

## Historical plan audit

Keep the current subsystem specifications authoritative. Do not execute historical
deployment commands for PM2, `monoize.service`, old hosts, or container restarts.
Use the current blue-green deployment contract when deployment is requested.

| Plan | Current evidence and remaining acceptance |
| --- | --- |
| 2026-08-26 Marketplace rehearsal | Source and evidence exist. The paired qualification explicitly fails latency and lacks write/production-copy qualification. Database work is deferred. |
| 2026-08-26 Provider/pricing rehearsal | Source and historical primitive checks exist. Gate C lacks a production pricing comparison. Database work is deferred. |
| 2026-08-26 status-event rehearsal | Primitive tests exist. Gate D still lacks full load and fault profiles; Gate E lacks name/topology evidence. |
| 2026-08-27 Store billing | Store pages, services, API tests, and frontend tests exist. Payment flow requirements must follow the current Store spec and later payment plans. Full Store acceptance remains open. |
| 2026-08-27 payment milestone | Adapter, refund, redemption, and reconciliation tests exist. The later EPay plan supersedes the original adapter-kind selection. Do not restore retired kinds to satisfy an old checkbox. |
| 2026-08-29 Dashboard/routing/status | Current dashboard and routing implementations exist; frontend and API checks pass. Production acceptance remains open. |
| 2026-08-29 usage/Marketplace/docs | Frontend checks and the four-locale documentation build pass. Live screenshot/production acceptance is not inferred from those checks. |
| 2026-08-30 admin usage/runtime/SSE | Current codec and API tests pass, including the local SSE regressions. Historical deployment steps are obsolete. |
| 2026-08-30 currency/ranking | Shared-currency frontend tests pass. Native CNY accounting remains a separate, deferred transition. |
| 2026-08-30 inline token delta/current rank | Frontend presentation and backend ranking tests pass. Production verification remains open. |
| 2026-08-30 chart selection motion | Selection-dataset frontend tests pass. Commit `4850bdf4` contains the implementation. Current production state is unverified. |
| 2026-08-30 ranking mutual privacy | Library privacy, frontend, and API tests pass. Production acceptance remains open. |
| 2026-09-02 wallet ledger/Coin mark | Wallet frontend, library reconciliation, and API tests pass. Remaining Store integration targets and production acceptance remain open. |
| 2026-09-08 Enterprise/API-key/EPay | Current frontend and API tests pass. Database migration work is deferred. |
| 2026-09-24 continuous deployment | Drain and environment-override checks pass locally. Linux routing-namespace and production-swap evidence remain open. |
| 2026-09-28 CNY accounting | Exact-money, schema, and state tests passed before deferral. Wallet/settlement integration and activation remain incomplete and deferred. |
| 2026-09-28 protocol synchronization | Pinned source comparison, codec tests, transform regressions, API integration, and docs build pass. Remaining repository-wide targets and delivery remain open. |

## Verification evidence

- 2026-09-30: `python tests/blue_green_env.py`: six tests passed. Covered literal
  values, duplicate keys, protected keys, malformed input, absent overrides, and
  credential-safe errors.
- 2026-09-30: `bash tests/blue_green_drain.sh`: passed all drain and race cases.
- 2026-09-30: `bash -n scripts/blue-green-swap.sh`: passed.
- 2026-09-30: `cd frontend; bun test tests`: 330 passed, zero failed.
- 2026-09-30: frontend `bun run lint` and `bun run build`: passed.
- 2026-09-30: docs `bun install --frozen-lockfile` and `bun run build`: passed;
  generated 706 pages, including all four configuration locales.
- 2026-09-30: `bash tests/pg_cutover_checks.sh`: passed exact-count and health
  failure cases. Shell syntax checks passed for the cutover script.
- 2026-09-30: default-feature focused Rust run passed 6 migration-value, 12 exact
  accounting-money, 6 accounting-schema, 8 accounting-state, and 2 upstream-sync tests.
  PostgreSQL verification was explicitly skipped; no live migration was verified.
- 2026-09-30: composer browser checks passed all 6 cases; organization limit editor
  browser checks passed all 7 cases. Fixed Windows fixture paths and aligned the
  editor test with ORGL-20's existing default-CNY contract.
- The first full Rust library run exposed dashboard cache eviction deadlock and
  two routing test failures. The deadlocked local test process was terminated.
  Fixed shared cache state, serialized eviction, reorder invalidation, and direct-write
  fixture invalidation. The final default-feature library run passed 1132 tests,
  with zero failures and one ignored manual image benchmark.
- PostgreSQL verification and database migration work are deferred by the user.
- Production verification and remaining historical-plan acceptance are incomplete.
- 2026-09-30: 330 protocol codec tests, 9 upstream-transform regressions, and 8
  dashboard aggregate cache tests passed. Each image feature configuration passed
  15 image tests; the manual image benchmark was ignored.
- 2026-09-30: all 457 API integration tests passed. The final frontend build after
  locale changes passed. No production deployment has been performed.
- Database-related files and deployment-override helpers remain local, uncommitted
  work while that scope is deferred. Their local check results above do not indicate
  that those changes are published or deployed.
- The non-database changes are prepared for review against `Monoize-Claude`.
  Additional contract checks passed 55 tests across 8 targets, and Store/account
  business checks passed 259 tests across 20 targets. Production deployment and
  the remaining database and capacity gates require separate evidence.
