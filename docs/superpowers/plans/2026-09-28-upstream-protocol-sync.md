# Upstream Protocol Synchronization Implementation Plan

> **For agentic workers:** Use executing-plans for implementation and verification-before-completion for integration.

**Goal:** Synchronize upstream protocol conversion at `136c8023` while preserving local service behavior.
**Architecture:** Import the upstream typed URP codecs. Adapt local handlers and transforms to the fixed upstream type interfaces. Preserve local routing, billing, deployment, retries, and regression fixtures.
**Tech Stack:** Rust, Tokio, serde, Bun, Astro.
**Spec:** `spec/upstream-protocol-sync.spec.md` and its referenced protocol specifications.

## Global Constraints

- Ordinary writes remain inside the project root.
- Preserve existing CNY work. At the 2026-09-30 baseline it is present in the
  committed tree; the historical stash reference is not a prerequisite for this checkout.
- Production deployment is excluded.
- Do not remove local transform identifiers or legacy routes.

## Review Focus

- Same-family custom tools preserve their schemas and exact input bytes.
- Split SSE JSON, explicit event names, terminal usage, and errors preserve local behavior.
- Typed field deletion prevents replay metadata from restoring removed values.
- Trusted context cannot be supplied by the client or leak upstream.
- Tool-result images retain MIME and mask semantics after compression.

### Task 1: Canonical codecs and handler integration

**Files:** `src/urp/**`, `src/handlers/**`, `src/error.rs`, `tests/urp_upstream_sync.rs`.
**Interfaces:** Consume the upstream typed URP API at the pinned revision. Produce adapted local request preparation, stream accumulation, and dispatch.

- [x] Verify JSON-boundary regressions for Gemini sampling and untrusted context with `cargo test --test urp_upstream_sync`.
- [x] Import typed URP, retaining local SSE parser constraints and HTTP transport.
- [x] Adapt handler typed constructors, accumulation, tool transport preparation, and runtime context.
- [x] Run codec and API regression suites. Preserve security and billing behavior in existing tests.

### Task 2: Transforms and docs

**Files:** `src/transforms/**`, relevant transform registry, `spec/auto-cache-transforms.spec.md`, `spec/urp-transform-system.spec.md`, matching docs in four locales.
**Interfaces:** Consume upstream typed URP fields; retain local transform IDs and semantics.

- [x] Synchronize Anthropic automatic caching and progressing tool-result breakpoints.
- [x] Synchronize image compression for tool-result images and complete function arguments.
- [x] Adapt existing transforms to typed fields without removing local regression coverage.
- [x] Update corresponding specs and four locale docs; run focused transform tests and docs build.

### Task 3: Integration review and push

- [ ] Run `cargo test`, frontend tests/lint/build when frontend changes, and docs build.
- [ ] Review the entire change for local regressions and spec alignment.
- [ ] Commit verified changes and push to `origin/Monoize-Claude`.
- [x] Preserve existing CNY files and record the imported source revision and remaining work.

## Verification update: 2026-09-30

- Fetched upstream `136c8023b6622bcffd941423b39ab2e6a78fc247` and compared its
  protocol and transform files against the local baseline `f405eb2e`.
- The native Anthropic automatic-cache and tool-result-cache implementations match
  the pinned source. Local codec differences include the documented UPS security,
  reasoning, terminal-event, and media corrections.
- The focused `urp_upstream_sync` integration suite passed both tests.
- The library run passed all 330 `urp` tests and all 9 `upstream_sync_tests`.
- The image compression suite passed 15 tests in each feature configuration;
  the manual image benchmark remained ignored. Fixed missing JPEG XL feature
  guards and applied the existing four-worker default from RRB-R1.
- The four-locale docs build passed after the image documentation update.
- The default-feature library suite passed 1132 tests; its manual image benchmark
  was ignored. All 457 API integration tests passed, including protocol settlement,
  streaming, authentication, and analytics cache scope.
- Frontend tests (330), browser checks (13), lint, and production build passed.
- The remaining repository-wide integration targets and delivery are still open.
  Database work is deferred at the user's request.
