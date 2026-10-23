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

## Phase 5 — 07-worker-queue  ✅
- [x] T14. `queue.py` + `worker.py`
- [x] T15. API (`POST /pipeline/run`, `GET /jobs`, `GET /jobs/{id}`) + CLI (`worker start`,
      `queue enqueue|status|list`) + `tests/test_queue.py` (43 tests, all 8 acceptance criteria)

Decisions worth recording:
- **One `process_document` for both modes.** The API's synchronous path and the worker call
  the same function, so inline and queued runs cannot drift. It stamps `tenant_id` on the
  trace *and* the diagnosis — the spec's worker sketch stamped neither, which would have
  leaked every queued job's records into `default`.
- **A finished job's payload is cleared.** The payload carries the raw document text, and
  spec 03 promises the unredacted original never reaches disk; retaining it for
  `QUEUE_JOB_TTL` (24h) broke that promise for every queued job. Retries keep it.
- **`busy_timeout=5000` on every connection.** Without it the second worker to write gets
  `database is locked` instead of waiting, so criterion 6 would be false in practice even
  though the claim itself is atomic.
- **`Job` is a pydantic model** (not the sketch's dataclass) so the API has a real response
  schema, and `status` is an enum, which gives `?status=` its validation.
- **`run_once(pipeline)` is the seam.** `run()` owns the LLM client and the loop; signal
  handlers are best-effort, so the loop is always stoppable via `stop()`.
- `expected_type` on the API is constrained to `DocumentType`: an arbitrary string would
  silently disable the analyzer's ground-truth check.
- Concurrency is tested two ways: sequential claims (3+3 distinct) and 4 threads racing for
  20 jobs, asserting each job is claimed exactly once.
- Spec 07 corrected: it used `datetime`/`UTC` with no import, built the queue at import
  time, and its criteria/call shapes are now annotated with what actually ships.

## Phase 6 — Deploy
- [x] T16. `render.yaml` + `Procfile` + `scripts/smoke.sh`; persistent disk, health check, env wiring
- [ ] T17. live smoke test — **blocked: no Render account/CLI/credentials in this environment.**
      Verified everything that does not need the account: the blueprint parses, the exact build
      command runs (`pip install ".[otel]"` + `npm ci && npm run build`), the exact start command
      serves the API *and* the compiled SPA, and `scripts/smoke.sh` passes the whole
      health → seed → flag → diagnose → confirm → analytics loop against that server, both
      anonymously (as the blueprint deploys it) and with `AUTH_ENABLED=true` + a real `ffk_` key.
      A no-key request is refused with 401 on every data route and allowed on `/health`.

Real defects found and fixed while making the deploy honest:
- **`SPEND_FILE` did not exist.** The spend-ledger path was hardcoded, so a deployment that put its
  database on a persistent disk still reset the global spend cap on every redeploy. Added the
  setting and threaded it through `NIMClient` (mirroring `cache_dir`).
- **`npm ci` failed**: the committed `package-lock.json` disagreed with `package.json`
  (vite 8.3.4 / typescript 7.0.2 / plugin-react 6.1.2 were installed and building fine, while
  `package.json` still asked for vite ^6 / typescript ^5.7). Aligned `package.json` to the versions
  the lock file and the verified build actually use, so the build is reproducible.
- **`OTEL_*` environment variables were silently ignored.** pydantic-settings fills a nested model
  only from `OTEL__EXPORTER` and drops env vars matching no field, so spec 01's entire feature was
  unreachable by configuration: `OTEL_EXPORTER=otlp` left the exporter at `none`. Added flat
  `OTEL_*` bindings (the `NVIDIA_API_KEY` pattern) plus a validator that assembles `settings.otel`;
  verified `console`, `otlp`, and `none` end to end, from both env vars and `.env`.
- **`cp .env.example .env` produced a config that refused to start.** The template set
  `JUDGE_FAMILY_SEPARATION_REQUIRED=true` while giving the pipeline and its fallback the same model
  ID, which the duplicate-ID guard (gated on the same flag) rejects once the flag is on.
- **The fault injector could be a no-op.** `_inject_wrong_label` picked "the first type absent from
  the document text", but most demo documents never name their own type, so it could set the label
  to the correct one and the scorecard then blamed the analyzer for a fault it never received. The
  analyzer's fault-injection score went 71.4% → 85.7% (step and category) with that one fix.
- `.env.example` gained every setting added by specs 04/06/07 (auth, scheduler, alerts, queue),
  plus the PII/OTel/judge/plugins block it never documented.

## Phase 7 — Docs truth pass  ✅
- [x] T18. `docs/limitations.md` rewritten: a "Resolved in this revision" table names each item the old
      file listed as missing (judge, auth, scheduler, plugins, PII, React) with its status, and the
      remaining 17 entries are real, current limits — each with impact, mitigation, and the measurement
      behind it. No table row presents shipped work as missing.
- [x] T19. `docs/README.md` written (index of specs → test files, ADRs 0000–0015, guides, and how to
      verify claims), ADRs 0013/0014/0015 added for the auth, scheduler, and queue decisions, ADR 0000's
      hosting assumption corrected, and `README.md` refreshed (status table, spec-to-test map, deploy
      guide, real CLI surface, honest demo prerequisites).

Verification of "every command in the README runs as documented":
`make install` (now re-runnable), `make check`, `make test`, `make dev`, `make run-web` (serves the API
*and* the compiled SPA), `ping`, `validate-config`, `validate-dataset`, `demo`, `run`, `eval`,
`analytics`, `benchmark-analyzer`, every `auth`/`scheduler-*`/`worker`/`queue` subcommand, and
`scripts/smoke.sh` against a live local server. `pytest -m live` is documented as requiring a real key
and was not run (quota); that precondition is stated in the README.

More real defects found while doing this pass:
- **`make install` failed for anyone who had already run it** — `uv venv` refuses to touch an existing
  `.venv` and exits non-zero. `--allow-existing` makes the documented first step idempotent.
- **`OTEL_SAMPLING_RATE` was parsed but never applied**, and `max_prompt_chars` was read via
  `getattr(settings, "otel_max_prompt_chars", default)` for a setting that did not exist. Sampling is
  now a per-trace head-based decision and the prompt-truncation limit is a real setting; both are
  tested (`tests/test_otel_export.py`, now 15 tests).
- **`frontend/package-lock.json` had a stale root block** (name `frontend`, `license: ISC`, and older
  react/lucide ranges) that disagreed with `package.json`. `npm install` resynced it; `npm ci` and
  `npm run build` now both work from a clean checkout, which the deploy build depends on.
- `forensics ping`'s docstring promised a connectivity test it deliberately does not make.

---

# Review findings (2026-10-20) — security, runtime, logic, frontend

Skills applied: `systematic-debugging` (root cause first), `verification-before-completion`
(every claim re-run), `test-fixing` (suite green throughout).

## Security
- [x] Path traversal in document lookups — `forensics/documents.py` allowlist +
      resolve-and-contain; applied to `api.py`, `eval_simple.py`, `streamlit_app.py`,
      and as a pydantic `pattern` on `PipelineRunRequest.doc_id` / `Document.doc_id`
- [x] Stored XSS in the Streamlit explorer — `html.escape` on diff text, step names, evidence
- [x] API-key prefix collisions — prefix = configured prefix + 8 chars of key entropy;
      validation iterates every candidate row
- [x] `scripts/smoke.sh` — parsed field paths, no `eval()`; failure rate passed via `argv`
- [x] CORS — `CORS_ORIGINS` setting, explicit origins, credentials never combined with `*`
- [x] PII — `parse_custom_patterns` (startup rejects bad regex/non-list/missing keys; the
      persistence path skips instead of crashing), `ffk_`/`sk-` key patterns, eval-case
      payloads redacted in `Storage.create_eval_case`
- [x] `ALERT_WEBHOOK_URL` — scheme + private/loopback/link-local validation at startup

## Build & runtime
- [x] `client.ts` `API_KEY_STORAGE` identifier (was `STORAGE_KEY` + undeclared use)
- [x] Clean checkout: conftest seeds models/key + `SPEND_FILE`/`SCHEDULER_DB_PATH` under
      `tmp_path`; judge tests assert configured ids; `DRY_RUN=true` builds Settings with no key
- [x] `TraceDetailView` parses span payloads through a non-throwing helper
- [x] Uploaded documents persisted to `data/documents/` and resolved by eval/diagnose
      (plus intake-span fallback for pre-existing traces)
- [x] README Node requirement matches `vite@8` (`^20.19 || >=22.12`) and documents `DRY_RUN`

## Backend logic & concurrency
- [x] `/eval/run` ignores `root_cause_step == "none"` when seeding
- [x] Confirmation re-reads the row and only creates cases for confirmed, real failures
- [x] Judge override takes evidence from the step it blamed
- [x] Propagation check uses whole-word definitional markers; `mixed` exempt
- [x] One grounding rule (`text_contains_entity`) across analyzer, eval repair, Streamlit
- [x] `SpendTracker`: context-local per-run counter + locked read-modify-write ledger;
      `analyze_trace` runs via `asyncio.to_thread` so it cannot block the loop
- [x] `run_with_fallback` covers transport/schema errors and never retries quota exhaustion
- [x] Tenant scoping in `eval_simple` and the scheduled-eval baseline
- [x] `_current_span` reset after each step; spans deleted before a re-save

## Frontend
- [x] Override sends `confirmed: true`
- [x] `POST /eval/run` typed as `EvalRunSummary`; empty suite returns `run_id: null`
- [x] Sidebar/filter use the diagnosis verdict as well as run status
- [x] `DAGGraphView` copies before sorting
- [x] Fake metrics/banners removed (duration, tokens, cost, MTTR, pass rate, delta, `[FAIL]`)
- [x] Vite proxy covers `/jobs` and `/scheduler`

## Verification
- [x] `ruff check` / `ruff format --check` / `mypy` clean
- [x] `pytest` — 417 passed (27 new regression tests in `tests/test_review_findings.py`)
- [x] `npm run build` (tsc + vite) exit 0
- [x] `bash -n scripts/smoke.sh` + field-path smoke check
- [x] `DRY_RUN=true forensics demo` — 15 traces, 5/5 seeded failures reproduced

---

# Next-step 2 — real numbers instead of fixture numbers (2026-10-24)  ✅

Goal: the live demo must run against a real model, produce real traces, and the
scorecard must be honest rather than "85.7% on a DRY_RUN baseline".

- [x] `tests/test_live.py` — one `@pytest.mark.live` end-to-end pipeline test (real NIM call,
      grounded entities, non-zero latency, clean diagnosis). Skips without a real key and
      skips (not fails) when the shared quota is exhausted; `make test` still deselects it.
- [x] Live `forensics demo` (DRY_RUN=false): 14/15 classifications match the labels
      (miss: `mixed_003`, an incident report the model calls `report`), 0 spurious root
      causes on the clean baseline.
- [x] Live `forensics benchmark-analyzer` against a real trace with 29 entities:
      step localization 7/7, category accuracy 7/7.
- [x] `dataset/live/traces/` — five traces from that run (invoice, report, contract,
      mixed) committed; `forensics seed` loads them idempotently, and the Render start
      command seeds before serving so a fresh URL is not purely DRY_RUN.

## Model reality check (the expensive part)

`poolside/laguna-xs-2.1` — the model `.env.example` called "the only one that works" —
actually returns **zero entities** and classifies the invoice as `mixed` at 0.10.
Most slugs in `/v1/models` answer `404 Function not found for account`; the account
reaches `minimaxai/minimax-m3.1-flash`, which extracts 29 grounded entities and
classifies the invoice at 0.99 in ~20s. `.env`, `.env.example`, `render.yaml` and
`CLAUDE.md` now name the verified model.

## Analyzer defects the live data exposed (all fixed, all regression-tested)

These are the reason the earlier fixture-based 85.7% was meaningless: on real output
the analyzer blamed extraction or summarization on 10/15 clean documents.

- **Dates**: real models copy "February 2024", "Monday, March 4", "Friday", "July"
  verbatim. `DATE_FORMATS` rejected all of them, so `date_regex` failed the whole
  extraction step and hid the real root cause.
- **Labelled values**: models return "Due Date: March 3, 2024" and
  "Invoice #: INV-2024-001". `_strip_entity_label` removes the label only when the
  tail literally occurs in the document (so it cannot launder an invented value).
- **Context preservation** demanded every grounded entity in the summary; a thorough
  extractor therefore guaranteed its own failure. It is now (a) restricted to salient
  types and (b) fails only when *none* survive. Typographic dashes (`$44–46M` vs
  `$44-46M`) compare equal.
- **Propagation** fired on any later mention of another type ("pay within 30 days of
  invoice" in a contract) and had a weak-signal rule that flagged every mixed summary.
  It now judges the first definitional marker in the opening sentence.
- **Fault injection** preserved only a fixed token list, so the injected
  `propagation_error` also triggered context loss, which outranked it. It now preserves
  the trace's own salient entities (in `key_points`) and imitates a type different from
  the actual classification.
- **Benchmark baseline** could be a `mixed` trace, where propagation is exempt by
  design; it now prefers a clean single-type baseline.
- **Demo** aborted the whole 15-document run on the first 429; it retries the document
  (cached steps are free) and paces the batch. The live summary no longer prints the
  fixture's "seeded-failure accuracy", which measured the fixture, not the analyzer.

Verification: `make check` → ruff + mypy clean, **455 passed, 1 deselected (live)**;
`pytest -m live` → 1 passed against the hosted model; live benchmark 7/7 (100%/100%).
