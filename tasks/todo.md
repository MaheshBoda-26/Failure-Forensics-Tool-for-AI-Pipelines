# Failure Forensics — Remaining Work Implementation

## Phase 0 — Baseline lock & harness (~1 h)
- [x] Record baseline into `docs/baseline-2026-10.md`
- [x] Add `tests/test_limitations_contract.py` (regression fence for resolved limitations)
- [x] Add `make baseline` target to Makefile

## Phase 1 — Configuration honesty (~3 h) — limitations #10 (prices), #4 (auth-off)
- [x] 1.1 Price awareness: `prices_configured`, startup warning, `/runtime` fields, `validate-config --strict-prices`, Header chip, `.env.example` banner
- [x] 1.2 Auth-off multi-tenant policy: `AUTH_OFF_MULTI_TENANT_POLICY`, startup warn/refuse, `/runtime` fields, docs, tests

## Phase 2 — Data lifecycle & redaction correctness (~5 h) — limitations #5, #7b
- [x] 2.1 Queue payload mode: `QUEUE_PAYLOAD_MODE`, reference mode enqueue/resolve, missing-doc guard, tests, docs
- [x] 2.2 Re-diagnosis under redaction: 409/stored/override contract, `degraded_reason`, UI banner, tests, docs

## Phase 3 — Evidence completeness (~6 h) — limitations #9, #12
- [x] 3.1 Prompt template archive: `prompts.py`, `PromptTemplate` model, storage + migration, API/CLI, tests
- [x] 3.2 Propagation quality: cascade rule, precondition skip, fixtures, docs

## Phase 4 — Queue fairness & worker health (~6 h) — limitation #17
- [x] Per-tenant inflight cap, `DEAD` status + dead letter, queue stats, CLI commands, tests, ADR 0017, docs

## Phase 5 — Verification & operations (~4 h + deploy) — limitations #16, #18
- [x] 5.1 Live-model CI (weekly schedule, spend caps, artifact, job summary, docs)
- [x] 5.2 PostgreSQL operational tests (pool sizing, reconnect, close idempotency, long txn)
- [ ] 5.3 Hosted deploy & smoke (needs owner credentials — user action)

## Phase 6 — Residual UI/UX gaps (~3 h)
- [x] Empty-trace state panel in TraceDetailView
- [x] Ground-truth diff fallback line in TokenDiffView
- [x] Analytics CSV export button + serializeAnalyticsCsv
- [x] Keyboard shortcuts (g o, g t, g a, /, ?) + help dialog in App.tsx
- [x] Frontend tests (empty state, CSV serializer)

## Phase 7 — Documentation reconciliation & release (~2 h)
- [ ] Update limitations.md (done for #4,#5,#7b,#9,#10,#11,#12,#16,#17,#18)
- [ ] New ADRs (done: 0017-queue-fairness.md)
- [ ] README.md: status, new env vars, new CLI commands, /runtime fields, "prices unset" note
- [ ] interview-talking-points.md: refresh test count and add two new answers
- [ ] PHASE-BF-SUMMARY.md: append "Phase G — remaining limitations" section
- [ ] Version bumps if behavior changed (Phase 3 does)
- [ ] Doc-consistency pass
