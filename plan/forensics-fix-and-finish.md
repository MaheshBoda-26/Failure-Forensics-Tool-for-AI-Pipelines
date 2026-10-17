# Failure Forensics Tool — Fix & Finish

## Goal
Make `make check` green again (a circular import between `analyzer_simple.py` and `checks.py`
breaks the CLI/API today), then finish the remaining spec'd work: `05-check-plugins`,
Phase 1 test debt, `04-auth-multitenant`, `06-scheduled-eval`, `07-worker-queue`, and the
one unimplemented `PLAN.md` item — the Render deploy.

## Context (verified at HEAD `e6c0961`, clean tree)
- `analyzer_simple.py:28` imports `checks`; `checks.py:18` imports `CheckResult` back. Cycle
  arrived in `53380b0` and is still present.
- Broken: `import forensics.{api,cli,checks}`. Working: `pipeline`, `storage`, `tracing`.
- Tests: 86 pass, 1 fail (`test_llm.py::test_analyzer_does_not_execute_trace_payload`, same root cause),
  2 modules uncollectable (`test_analyzer_phase3.py`, `test_storage_eval_api.py`).
- Missing code: `auth.py`, `scheduler.py`, `queue.py`, `worker.py` — none exist.
  Missing config: `CHECK_PLUGINS`, `SCHEDULER_*`, `QUEUE_*`, `API_KEYS`.
  Missing model field: `tenant_id` on all 7 tables. Present already: `judge_analysis`, PII + OTel settings.
- Deploy artifacts: no `render.yaml`, `Procfile`, or `Dockerfile`.

## Phase 0 — Unblock (do this first, nothing else is testable)
- [ ] **T1. Break the import cycle.** In `checks.py`, add `from __future__ import annotations` and move
      `from forensics.analyzer_simple import CheckResult` under `if TYPE_CHECKING:`; keep the
      `register_builtin_checks` body import lazy. → Verify: `python -c "import forensics.api, forensics.cli, forensics.checks"` prints OK.
- [ ] **T2. Re-run the gate.** → Verify: `make check` green, pytest reports 0 failed and both previously
      uncollectable modules now collect.
- [ ] **T3. Guard against regression.** Add `tests/test_imports.py` asserting `forensics.api` and
      `forensics.checks` import in either order (fresh subprocess each). → Verify: test fails if the
      cycle is reintroduced.

## Phase 1 — Finish spec `05-check-plugins` (it is written but non-functional)
- [ ] **T4. Add the `CHECK_PLUGINS` setting** (comma-separated `module:attr` paths) plus a loader that
      registers each plugin and raises a clear startup error on a bad spec. → Verify: acceptance #2 and #6
      of the spec (`CHECK_PLUGINS=` loads; invalid spec errors with the offending string named).
- [ ] **T5. `tests/test_check_plugins.py`** covering all 7 acceptance criteria, including "built-ins behave
      exactly as before" (same names/logic) and registration-order priority. → Verify: `pytest tests/test_check_plugins.py` green.

## Phase 2 — Phase 1 test debt (these specs claim "done" but have no tests)
- [ ] **T6. `tests/test_pii_redaction.py`** for all 10 acceptance criteria in `03-pii-redaction.md`,
      especially #9 (redaction happens before JSON + SQLite write; the original never reaches disk) and
      #8 (custom pattern `INT-\d{8}` → `[REDACTED]`). → Verify: green; assert on written file/db bytes, not the in-memory span.
- [ ] **T7. `tests/test_judge_llm.py`** for `02-judge-llm.md` criteria 1–5 using `httpx.MockTransport`
      (never live): `JUDGE_ENABLED=false` → zero calls/zero cost; failure → judge runs and can override;
      `JUDGE_FORCE=true` → runs on every trace; verdict lands in `Diagnosis.judge_analysis`; spend ledger
      shows the `judge` tag separately. → Verify: green, no network.

## Phase 3 — Spec `04-auth-multitenant` (~6h, the biggest block)
- [ ] **T8. Migration script first.** Idempotent SQLite migration adding `tenant_id` to `traces, spans,
      flags, diagnoses, eval_cases, eval_runs, eval_results` plus an `api_keys` table. → Verify: run on a
      copy of `data/forensics.db`; row counts unchanged; running it twice is a no-op.
- [ ] **T9. `src/forensics/auth.py`.** Key mint/hash/verify (`bcrypt` is already in optional deps) and a
      FastAPI dependency resolving the caller's tenant. → Verify: unit test for verify + rejection paths.
- [ ] **T10. Wire it.** Apply the dependency to every mutating endpoint, filter list endpoints by tenant
      (`api.py`), scope `storage.py` queries, and add a CLI command to mint a key. → Verify: request with
      no key → 401; key A cannot read or mutate tenant B's trace.
- [ ] **T11. `tests/test_auth.py`.** → Verify: green; no endpoint left unauthenticated (enumerate routes and assert each is guarded).

## Phase 4 — Spec `06-scheduled-eval` (~3h)
- [ ] **T12. `src/forensics/scheduler.py`** (APScheduler, already an optional dep) with webhook trigger and
      regression alerting on failure-rate change, plus the `api.py` / `cli.py` hooks. → Verify: acceptance
      criteria of the spec; webhook exercised against a local test server, no external network.
- [ ] **T13. `tests/test_scheduler.py`.** → Verify: green; proves it works unauthenticated (so it is not blocked by Phase 3).

## Phase 5 — Spec `07-worker-queue` (~5h)
- [ ] **T14. `queue.py` + `worker.py`** with atomic SQLite claims, implementing against
      `tenant_id='default'` first so it is not blocked on Phase 3. → Verify: two workers never claim the
      same job; a killed worker's job is reclaimable.
- [ ] **T15. API + CLI surface and `tests/test_queue.py`.** → Verify: green, including the plan's
      100-concurrent-jobs load test with no duplicate execution.

## Phase 6 — Deploy (`PLAN.md`'s only unfinished item)
- [ ] **T16. Add deploy config** — `render.yaml` (or `Procfile`) with the `uvicorn` start command, install
      the `otel`/`auth`/`scheduler` extras, and attach a persistent disk at `data/` so traces and
      `forensics.db` survive restarts. → Verify: `render.yaml` lint/parse passes locally; build command runs clean from a fresh checkout.
- [ ] **T17. Deploy and smoke test live.** → Verify: on the live URL run `ping → run → flag → diagnose →
      confirm` and confirm `/analytics` returns non-zero `failure_rate` (the demo's headline number).

## Phase 7 — Truth pass on the docs (they currently misdescribe the code)
- [ ] **T18. Rewrite `docs/limitations.md`** — it still claims "No Judge LLM", "No PII Redaction",
      "No Custom Check Plugins", "Streamlit Only"; all four are now implemented and React replaced Streamlit.
      → Verify: no ✅ row in its summary table still describes shipped work as missing.
- [ ] **T19. Refresh `README.md`** status ("Day 1 Complete" is stale), fill in `docs/README.md`
      (currently "Not yet written"), and add ADRs for the auth / scheduler / queue decisions.
      → Verify: every command in the README runs as documented.

## Done When
- [ ] `make check` is green from a clean checkout, with no uncollectable test modules.
- [ ] Specs 01–07 each have their named test file present and passing.
- [ ] A request with no API key is rejected and tenants cannot see each other's data.
- [ ] Eval can run on a schedule and a regression raises a webhook alert.
- [ ] A live Render URL completes the full flag → diagnose → confirm loop.
- [ ] No doc statement contradicts the code.

## Notes
- **Ordering is deliberate.** Phase 0 blocks everything: don't start specs on a tree where `make check` is red.
  Phase 3 (auth) is sequenced before the queue because the queue's real tenant isolation needs `tenant_id`,
  but T14 is written against `tenant_id='default'` so it can start early if auth slips.
- **Subagent-friendly.** Phases 2, 4, 5 and 7 are independent of each other and parallelizable after Phase 0.
- **Tests before features, per the project's own post-mortem.** `docs/REVIEW-full-codebase.md` names
  "coverage and scope both insufficient" as the reason 20+ defects shipped. Phases 1–2 exist to stop
  repeating that; each later phase ships its test file in the same step as its code.
- **Every phase's cost is an estimate, not a commitment.** Re-scope after Phase 2 once the real number is known.
