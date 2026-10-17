# forensics-fix-and-finish — execution tracker

HEAD baseline: `e6c0961` (clean tree, `?? plan/` untracked)

## Reality check found before starting
The plan's Phase 0 claim ("T1 alone makes `make check` green") is **false** at HEAD:
- `checks.py:18` imports `CheckResult` from `analyzer_simple` at module top -> cycle (as documented).
- `analyzer_simple.py:64` calls `register_builtin_checks()` **before** the check functions it imports
  are defined -> would raise ImportError even after the cycle is broken.
- `checks.py:run_registered_checks` calls `plugin.func(span, trace, document_text)` (3 args) while the
  registered built-ins take 1–2 args -> all built-ins would raise TypeError and report as failed.
- `run_deterministic_checks` ignores `expected_type`, so `label_grounding` can never observe ground truth.
These are all one half-finished refactor (arrived in 53380b0, touched by e6c0961).

## Phase 0 — Unblock  ✅
- [x] T1. Break cycle (checks.py owns registry; analyzer imports it lazily; register lazily + once)
- [x] T2. `make check` green, 0 failed, no uncollectable modules
- [x] T3. `tests/test_imports.py` (both import orders, fresh subprocess)

Extra work the plan did not anticipate (all required for T2's "green from clean checkout"):
- [x] Fixed 21 pre-existing mypy errors at HEAD (pii.py `Pattern[str]`, otel_export.py exporter
      typing + `cast`, llm.py dry-run `parsed` union + duplicate unreachable return, api.py dict/-> FileResponse).
- [x] Installed optional extras (`.[dev,otel,auth,scheduler]`) so auth/scheduler/queue phases can run
      and mypy can resolve the OTLP exporter.

## Phase 1 — 05-check-plugins  ✅
- [x] T4. `CHECK_PLUGINS` setting + `load_plugins_from_config` loader w/ clear errors
      (wired into `validate_startup()` and a FastAPI lifespan hook)
- [x] T5. `tests/test_check_plugins.py` (20 tests, all 7 acceptance criteria) + `tests/plugin_samples.py`

## Phase 2 — Phase 1 test debt  ✅
- [x] T6. `tests/test_pii_redaction.py` (23 tests; asserts on the bytes written to JSON + SQLite,
      with a negative control). Found and fixed two real gaps the spec's own criteria exposed:
      the `api_key` pattern required `sk_or_` so real `sk-or-v1-…`/`nvapi-…` keys were never
      redacted; custom patterns ignored their documented `replacement`. Spec 03 corrected to match.
- [x] T7. `tests/test_judge_llm.py` (14 tests, `httpx.MockTransport` only). Found and fixed a
      **blocking** judge bug: `structured()` validated replies against `JudgeAnalysis`, which
      requires `model_id`/`provider` that the prompt never asks for — so every judge reply failed
      validation and the judge could never run. Added `JudgeVerdict` (the call facts stay out of
      the response schema). Also implemented per-step spend attribution so the `judge` tag the plan
      requires exists, and filled `JudgeAnalysis.cost_usd`.

## Phase 3 — 04-auth-multitenant  ✅
- [x] T8. idempotent `tenant_id` migration + `tenants`/`api_keys` tables
      (`src/forensics/migrations.py`, called from both `Storage._init_schema` and `tracing.init_db`)
- [x] T9. `auth.py` (`AuthManager`: bcrypt hashing, `ffk_` prefix lookup, revocation, tenant lifecycle)
- [x] T10. wired into api/storage/cli (tenant-scoped reads, stamped writes, `forensics auth …` group)
- [x] T11. `tests/test_auth.py` (49 tests, all 8 acceptance criteria)

Decisions worth recording:
- `tenant_id` defaults to `'default'` on every model, so no existing constructor call changed and
  pre-multi-tenancy rows land in one reachable tenant instead of becoming orphans.
- Dev mode (`AUTH_ENABLED=false`) applies **no** tenant filter, so it "works as before" (criterion 1);
  writes still stamp `default`. Auth mode filters in SQL, so `limit`/`offset` stay meaningful, and a
  cross-tenant read answers 404 from the index before any payload is loaded.
- Endpoints take `authorization: Annotated[str | None, Header()]` and call `get_current_tenant(...)`
  rather than using `Depends`: this repo's tests call endpoint functions directly, and a `Depends`
  default would leak a `Depends` object into those calls.
- `bcrypt` promoted from the `auth` extra to a required dependency — `forensics.auth` is imported by
  `forensics.api`/`forensics.cli`, so a `.[dev]`-only install could not import the package. The `auth`
  extra is gone; `make install` now pulls `[dev,otel]` and a `opentelemetry.exporter.*` mypy override
  keeps typecheck green when the OTel extra is absent.
- Verified for real: fresh venv + `uv pip install -e ".[dev]"` → ruff clean, mypy strict clean,
  256 tests pass. `make check` green from a clean checkout, with no optional extra installed.

## Phase 4 — 06-scheduled-eval  ✅
- [x] T12. `scheduler.py` + hooks (config, API lifespan, `scheduler-start`/`scheduler-status` CLI, `/scheduler/status`)
- [x] T13. `tests/test_scheduler.py` (29 tests, all 8 acceptance criteria)

Decisions worth recording:
- **SQLAlchemy promoted to a required dependency.** `SQLAlchemyJobStore` (the
  SQLite job store criterion 7 requires) raises `ImportError` without it, and the
  spec's own dependency list omitted it. Adding the dep is the honest fix;
  reimplementing APScheduler's job store in raw sqlite3 would be a riskier way to
  satisfy the same criterion.
- **The job is a module-level function.** APScheduler pickles jobs into the
  store, so a bound method would serialise the whole scheduler (settings,
  transport, live connections). `_scheduled_eval_job` resolves the global at run
  time.
- **`asyncio.run` in the job wrapper.** APScheduler runs sync callables in a
  worker thread; passing the `async def` directly yields an un-awaited coroutine,
  i.e. an eval that silently never runs.
- **`ALERT_ON_FIXED` diffs case by case.** The sketch alerted on
  `passed_count > 0`, which fires after every healthy run.
- **Float comparison rounded before the threshold test**: `1.0 - 0.9` is
  `0.09999999999999998`, so an exactly-10% drop missed a 10% threshold. Found by
  the boundary test, not by inspection.
- `regression_pct` is a real percentage (the accuracies beside it are fractions),
  and tests exercise the webhook through `httpx.MockTransport`, so the real
  request-building code runs without touching the network.
- Spec 06 corrected: command names (`scheduler-start`, not `scheduler_start`),
  duplicate criterion number, `SCHEDULER_DB_PATH`, dependency list, and an
  "Implementation notes" section recording the four deliberate divergences.

## Phase 5 — 07-worker-queue
- [ ] T14. `queue.py` + `worker.py`
- [ ] T15. API/CLI + `tests/test_queue.py`

## Phase 6 — Deploy
- [ ] T16. render.yaml / Dockerfile + persistent disk
- [ ] T17. live smoke test (needs Render access)

## Phase 7 — Docs truth pass
- [ ] T18. `docs/limitations.md`
- [ ] T19. README / docs README / ADRs
