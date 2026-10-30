# Failure Forensics — Remaining Work Implementation Plan

**Status of the project:** the 2-day build plan (`PLAN.md`) and Phases B–F
(`tasks/phase-bf-implementation.md`) are **complete and shipped**. The only
outstanding work is the set of gaps catalogued in `docs/limitations.md`, plus
the operational verification the docs already call out as unproven.

This plan turns those gaps into sequenced, testable work. It deliberately does
**not** re-open finished features.

---

## 0. Baseline (verified against the tree)

| Area | State |
|---|---|
| Pipeline | 4 steps, versioned Pydantic contracts, chain runner (`src/forensics/pipeline.py`) |
| Tracing | spans with prompt/response/tokens/latency, atomic JSON + SQLite (`tracing.py`) |
| Analyzer | 7 deterministic checks + root-cause attribution (`analyzer_simple.py`) |
| Judge | optional cross-family judge (`llm.py`, `analyzer_simple.py`) |
| Eval loop | flag → case → run → alert (`eval_simple.py`, `scheduler.py`) |
| Storage | SQLite default + PostgreSQL via `DATABASE_URL` (`storage_sqlite.py`, `storage_postgres.py`) |
| API | FastAPI, auth/scopes/rate-limit, queue, analytics, benchmark (`api.py`) |
| UI | React + Vite + React Flow, 5 routes, auth key popover (`frontend/`) |
| Dataset | 15 committed docs + `dataset_large/` 150 docs / 43 seeded failures |
| Tests | ~566 passing, 13 skipped, 12 live-Postgres via `make test-postgres` |
| CI | lint/test, postgres, frontend build, **manual** live job (`.github/workflows/ci.yml`) |

Commands that must stay green throughout: `make check`, `cd frontend && npm test && npm run build`,
`make test-postgres` (with a server), `DRY_RUN=true python -m forensics.cli demo`.

---

## Global constraints — read before every phase

### Non-negotiable (never do)

1. **Never invent model prices.** `*_PRICE_IN`/`*_PRICE_OUT` stay operator-supplied. Surface that they are unset; do not fill them.
2. **Never persist unredacted traces** on the normal write path. PII redaction at `finalize_trace` is the shipped guarantee (`tracing.py:501-519`).
3. **Never weaken the deterministic analyzer into a judge dependency.** Checks must keep working with `JUDGE_ENABLED=false` and `DRY_RUN=true`.
4. **Never change prompt text as part of infrastructure work.** Prompt hashes are part of a trace's identity and fixtures key on them. Any prompt change is its own phase with fixture regeneration.
5. **Never make the offline demo require a network or a key.** `DRY_RUN=true` is the first-class demo path.
6. **Never turn `AUTH_ENABLED` on by default**, and never remove the `AUTH_ENABLED=false` single-tenant path — tests and the public demo depend on it.
7. **Never add a broker (Redis/RabbitMQ), an autoscaler, or Kubernetes** to solve a note that says "no orchestrator". The queue's contract is "atomic claims on the existing database".
8. **Never break the storage contract.** Any new storage method goes on `StorageBase`, in both SQLite and PostgreSQL, plus `tests/test_schema_parity.py` parity.
9. **Never broad-rewrite a module to fix one gap.** Surgical diffs, one behaviour per commit.
10. **Never let a new setting silently do nothing.** Every new env var must be bound in `Settings`, documented in `.env.example`, and read by at least one code path with a test.

### Working agreement

- TDD per task: failing test → minimal implementation → green → commit.
- One limitation per commit series. Commit prefix: `fix(limitation-N): …` or `feat(phase-N): …`.
- Every new config flag defaults to **current behaviour**.
- Every phase ends with `make check` green and an entry in `docs/limitations.md` updated in the same PR.
- Docs must not claim anything the tests do not prove (`docs/limitations.md` is the contract).

---

## Phase 0 — Baseline lock & harness (≈1 h)

**Goal:** freeze the current numbers so every later phase can prove it changed nothing.

### Do

- [ ] Record baseline into `docs/baseline-2026-10.md`:
      `make check` tail, `pytest --collect-only -q | tail -1`, `npm test` count,
      `DRY_RUN=true python -m forensics.cli demo` summary,
      `DRY_RUN=true python -m forensics.cli benchmark-analyzer` scorecard.
- [ ] Add `tests/test_limitations_contract.py` with one assertion per **resolved**
      limitation that is cheap to express:
      - `GET /runtime` exposes `pii_redaction_enabled`, `rate_limit_enabled`, `queue_enabled`;
      - `Queue` clears payload on terminal state (`queue.py` `_CLEARED_PAYLOAD`);
      - `finalize_trace` redacts when `PII_REDACTION_ENABLED=true`;
      - `check_plugins` loader raises on a bad spec;
      - `AUTH_KEY_TTL_DAYS` is read by `validate_api_key`.
      This is a regression fence, not new behaviour.
- [ ] Add `make baseline` target that runs the three commands above and writes the file.

### Don't

- Don't add coverage gates or refactor tests.
- Don't pin commit SHAs in docs (they drifts — see the note in `docs/limitations.md`).

### Exit criteria
`make check` green; baseline file committed; contract test fails if a resolved guarantee regresses.

---

## Phase 1 — Configuration honesty (≈3 h) — limitations #10 (prices), #4 (auth-off exposure)

**Goal:** a misconfigured deployment announces itself instead of quietly reporting `$0.0000` or
exposing every tenant.

### 1.1 Price awareness (#10)

**Do**
- [ ] Add to `Settings` (`src/forensics/config.py`): `prices_configured: bool` computed property
      — true when every *active* model (`pipeline`, `pipeline_fallback`, `judge`, `cheap`) has a
      non-zero `price_in_per_1m` or `price_out_per_1m`.
- [ ] `validate_startup()` (`config.py:741`) emits a `RuntimeWarning` when
      `not dry_run and not prices_configured`: "cost and spend caps will read $0.00".
- [ ] Extend `GET /runtime` (`api.py:412`) with `prices_configured` and `spend_caps_usd`
      `{per_run, global}` so the UI and smoke test can assert it.
- [ ] Extend `forensics validate-config` (`cli.py:688`) with `--strict-prices`; exit code 2 when
      unset. Default stays exit 0 + warning, so the existing demo command is unchanged.
- [ ] Frontend: `frontend/src/components/Header.tsx` shows a "prices unset" chip when
      `runtime.prices_configured === false`; add `prices_configured` to `frontend/src/api/types.ts`.
- [ ] `.env.example`: move the price block's `VERIFY BEFORE USE` banner above the values and add
      one worked example (commented out) showing per-1M formatting.

**Don't**
- Don't ship a price table or fetch prices from the web at runtime.
- Don't make zero prices a hard failure — `DRY_RUN` and the offline demo must still start.
- Don't change `spend_cap_*` defaults.

**Verify**
- `pytest tests/test_config.py -k price -v` (new cases: all-zero warns, one set does not warn).
- `DRY_RUN=true python -m forensics.cli validate-config` → 0; `--strict-prices` → 1/2.
- `curl /runtime` contains `prices_configured: false`.

### 1.2 Auth-off multi-tenant exposure (#4)

**Do**
- [ ] Add `AUTH_OFF_MULTI_TENANT_POLICY: Literal["warn","refuse"] = "warn"` to `Settings`.
- [ ] In `validate_startup()`: when `not auth_enabled`, count tenants via `AuthManager`; if > 1,
      warn (or raise for `refuse`). Never runs in tests unless a test creates a second tenant.
- [ ] `GET /runtime` exposes `auth_enabled` and `tenant_isolation` (`"enforced"|"disabled"`).
- [ ] `docs/limitations.md` §4 gets the policy knob and the exact command to audit tenants
      (`forensics auth list-tenants`).
- [ ] Test: `tests/test_auth.py::test_auth_off_multi_tenant_warns` and `..._refuses`.

**Don't**
- Don't apply tenant filters when auth is off — that would change the documented "works as before"
  contract and break the demo's cross-run analytics.
- Don't delete data or migrate tenants in this phase.

**Verify**
`pytest tests/test_auth.py -k multi_tenant -v`; API starts clean on a single-tenant DB.

### Exit criteria
Both warnings are observable via `/runtime` and `validate-config`; `.env.example` and
`docs/limitations.md` describe them; no default behaviour changed.

---

## Phase 2 — Data lifecycle & redaction correctness (≈5 h) — limitations #5, #7b

**Goal:** raw text stops living in the queue when an operator asks, and re-diagnosis stops
producing false hallucinations under redaction.

### 2.1 Queue payload mode (#5)

**Current:** `POST /pipeline/run` inlines `doc_text` into `job_queue.payload` (`api.py:807-818`);
payload is cleared at terminal state (`queue.py` `_CLEARED_PAYLOAD`).

**Do**
- [ ] Add `QUEUE_PAYLOAD_MODE: Literal["inline","reference"] = "inline"` to `Settings`
      (`config.py`, queue section, default preserves today's behaviour).
- [ ] In `reference` mode, `POST /pipeline/run` stores `{"doc_id", "expected_type"}` only —
      no `doc_text`. Uploaded text is already persisted by `store_document`
      (`documents.py:136`), so the worker can resolve it.
- [ ] In `Worker._process_job` (`worker.py:179`), resolve `payload["doc_id"]` via
      `read_document(doc_id, tenant_id)` when `doc_text` is absent; a missing document fails the
      job with a clear, non-retryable message (new `JobStatus.DEAD` or existing `failed` + note —
      choose `failed` and document it).
- [ ] Guard: `reference` + non-persisted document (e.g. `dataset` doc with no file) must fail at
      enqueue time, not at claim time, so the caller learns synchronously.
- [ ] `tests/test_queue.py`:
      - enqueue in reference mode → row contains no document text (`SELECT payload` assert);
      - worker processes it end-to-end;
      - missing doc in reference mode → terminal failure with actionable `error`.
- [ ] `docs/limitations.md` §5 rewritten: inline (default) vs reference, and the residual
      WAL/freelist caveat (not secure erasure).

**Don't**
- Don't redact the queue payload — it is model input, and redaction would change output.
- Don't attempt `VACUUM`/secure-delete of WAL pages; document the limitation instead.
- Don't change the default to `reference` (uploads on ephemeral disks would regress).

**Verify**
`pytest tests/test_queue.py -k payload -v`; `sqlite3 data/forensics.db "select payload from job_queue"` empty of text in reference mode.

### 2.2 Re-diagnosis under redaction (#7b)

**Current:** `POST /traces/{id}/diagnose` (`api.py:535`) reloads the persisted, redacted trace and
re-runs checks, so a correctly extracted email is flagged as a hallucination.

**Do**
- [ ] Change the endpoint contract:
      - If `PII_REDACTION_ENABLED=true` **and** a stored diagnosis exists → return the stored
        diagnosis (200) and set `X-Diagnosis-Source: stored`.
      - If redaction is on, no stored diagnosis, and no explicit override → `409` with a message
        naming the cause and the two ways forward.
      - Add `?allow_redacted=true` (write scope) to force a fresh analysis and set
        `X-Diagnosis-Source: redacted-recompute`; response gains a `degraded_reason` field on
        `Diagnosis` (optional) so the UI can show a banner.
- [ ] `frontend/src/components/TraceDetailView.tsx`: when `degraded_reason` is present, render an
      inline notice ("diagnosis computed on redacted spans").
- [ ] `tests/test_review_findings.py` (or a new `tests/test_rediagnose.py`): stored-path returns
      identical diagnosis and header; forced path returns degraded marker; no-redaction path
      unchanged.
- [ ] `docs/limitations.md` §7b: mark **mitigated**, describe the 409/override contract.

**Don't**
- Don't persist an unredacted second copy "just for analysis". That reopens the leak.
- Don't silently recompute and return a wrong diagnosis — the current bug — as "working".
- Don't change `finalize_trace` redaction ordering.

**Verify**
`pytest tests/test_rediagnose.py -v`; manual: run with `PII_REDACTION_ENABLED=true`, diagnose twice,
second call returns `stored` and the same `root_cause_step`.

### Exit criteria
Reference mode is opt-in and leak-free at rest; re-diagnosis never fabricates a hallucination under
redaction; both documented.

---

## Phase 3 — Evidence completeness (≈6 h) — limitations #9, #12

**Goal:** a trace can answer "what template produced this?" and propagation is less likely to
mis-attribute a cascade.

### 3.1 Prompt template archive (#9)

**Current:** spans store the rendered `prompt` and a `prompt_version` hash; the template itself is
only in code (`llm.py` inline strings).

**Do**
- [ ] Extract the five prompt builders from `llm.py` into `src/forensics/prompts.py` **without
      changing a character of prompt text** (move, then assert byte-equality in a test by hashing
      before/after strings).
- [ ] Add `PromptTemplate` model (`models.py`): `name`, `version` (content hash), `template_text`,
      `created_at`; `StorageBase` methods `save_prompt_template`, `get_prompt_template`,
      `list_prompt_templates`; implement in both stores + migration (`migrations.py`).
- [ ] `finalize_trace` records `span.prompt_template = "extraction-v1"` (name, not text) — or a
      `prompt_template_id` — and upserts the template row once per version (idempotent).
- [ ] API: `GET /prompt-templates`, `GET /prompt-templates/{version}` (read scope).
- [ ] CLI: `forensics prompts list`, `forensics prompts show <version>`.
- [ ] `tests/test_prompt_templates.py`: template row exists after a DRY_RUN trace; hash matches
      rendered text; unknown version 404.

**Don't**
- Don't store the template text on every span (duplication); one row per version.
- Don't alter prompt wording, system message, or version strings — fixtures and `prompt_version`
  identity depend on them.
- Don't archive secrets or API keys; templates are static text only.

**Verify**
`pytest tests/test_prompt_templates.py -v`; `forensics prompts list` after a demo shows 4–5 rows.

### 3.2 Propagation quality (#12)

**Current:** `check_propagation` (`analyzer_simple.py:579`) looks for definitional vocabulary that
disappears downstream; `find_root_cause` (`:839`) can blame a downstream step for an upstream loss.

**Do**
- [ ] Add a cascade rule in `find_root_cause`: when two consecutive steps fail `propagation` and the
      earlier one also fails a local check, attribute the **earliest** failing step as root cause and
      list the later ones as `evidence` "downstream of <step>".
- [ ] Add a precondition to `check_propagation`: skip (report pass) when the upstream step's output
      carries no salient terminology, mirroring how `Fault.scoreable` handles `context_loss`
      (`faults.py`). Report `checks["propagation"] = True` with an evidence note rather than a false
      failure.
- [ ] Add fixtures in `tests/test_analyzer_phase3.py`:
      - upstream extraction drops a term → root cause is extraction;
      - downstream summary drops a term the extraction kept → root cause is summarization;
      - no salient term upstream → no propagation failure.
- [ ] `docs/limitations.md` §12: rewrite as "heuristic + cascade suppression", keep the explicit
      "not a dataflow graph" statement.

**Don't**
- Don't build a dependency graph, LLM-based propagation, or a new check family.
- Don't change check names or the `deterministic_checks` keys (frontend and eval aggregate on them).
- Don't let the new rule change the 150-fault benchmark accuracy downward; if it does, the rule is
  wrong — investigate before adjusting the expectation.

**Verify**
`pytest tests/test_analyzer_phase3.py tests/test_benchmark_scorecard.py -v`;
`DRY_RUN=true python -m forensics.cli benchmark-analyzer` still reports 100% on eligible faults.

### Exit criteria
Template text is retrievable per trace; propagation no longer blames a downstream step for an
upstream loss; benchmark unchanged or better.

---

## Phase 4 — Queue fairness & worker health (≈6 h) — limitation #17

**Goal:** one tenant cannot starve the queue, and a poison job stops retrying forever.

### Do

- [ ] Add settings (`config.py`, queue section):
      `QUEUE_MAX_INFLIGHT_PER_TENANT: int = 0` (0 = unlimited, current behaviour),
      `QUEUE_MAX_ATTEMPTS: int = 3`, `QUEUE_DEAD_LETTER_ENABLED: bool = true`.
- [ ] `Queue.claim` (`queue.py:259`):
      - SQLite: exclude tenants whose `status IN ('pending','running')` count ≥ cap
        (correlated subquery), still `ORDER BY priority DESC, created_at`, still one atomic
        `UPDATE ... RETURNING`.
      - PostgreSQL: same predicate inside the `FOR UPDATE SKIP LOCKED` CTE.
- [ ] `Queue.fail` / retry path (`queue.py:437`): when `attempts >= max_attempts`, set
      `status='dead'` and keep the error; `JobStatus` gains `DEAD`.
- [ ] `Queue.stats()` + `GET /queue/stats` (read scope): counts by status, oldest pending age,
      per-tenant in-flight.
- [ ] CLI: `forensics queue stats`, `forensics queue requeue --dead` (bulk, explicit).
- [ ] `tests/test_queue.py` / `tests/test_queue_fairness.py`:
      - tenant A at cap → tenant B's job is claimed first;
      - `max_attempts` reached → status `dead`, no further claims;
      - stats counts match inserts;
      - existing single-tenant claim tests unchanged.
- [ ] `docs/adr/0017-queue-fairness.md`: cap semantics, dead-letter, why no broker.
- [ ] `docs/limitations.md` §17: update from "no fairness" to "coarse per-tenant inflight cap;
      still no autoscaling/health supervision".

### Don't

- Don't add Redis, Celery, or a separate broker.
- Don't implement weighted fair queueing / DRF — an in-flight cap is enough for a forensics tool.
- Don't make dead-lettering the default for jobs that can be fixed by a retry; `max_attempts`
      only.
- Don't change the atomic-claim guarantee or `busy_timeout`.

### Verify
`pytest tests/test_queue.py tests/test_queue_fairness.py -v`;
`make test-postgres` for the PG claim variant; `forensics queue stats` on a seeded queue.

### Exit criteria
Fairness is opt-in via a cap, starvation is test-proven prevented, poison jobs terminate, and both
backends pass.

---

## Phase 5 — Verification & operations (≈4 h + deploy) — limitations #16, #18 (operational), deploy

**Goal:** the claims the docs make about live models, PostgreSQL under stress, and the hosted demo
are backed by evidence, not hope.

### 5.1 Live-model CI (#16)

**Do**
- [ ] `.github/workflows/ci.yml`: add `schedule: [{cron: "0 6 * * 1"}]` (weekly, not nightly, to
      bound spend) to the existing `live-tests` job; keep `workflow_dispatch`.
- [ ] Limit it to one document (`LIVE_TEST_DOC_ID=contract_001`), `SPEND_CAP_PER_RUN_USD=0.05`,
      `SPEND_CAP_GLOBAL_USD=0.20`, and skip-with-success on quota `429` (already in
      `tests/test_live.py:126`).
- [ ] Upload the resulting trace JSON as a workflow artifact; add a job summary line
      (model, tokens, cost, root cause).
- [ ] `tests/test_live.py`: assert the chosen doc produces a grounded entity and a diagnosis
      (extend, don't duplicate).
- [ ] `docs/limitations.md` §16: replace "validated manually" with "weekly scheduled live job;
      PRs still never spend quota".

**Don't**
- Don't run live tests on `pull_request` or `push`.
- Don't require a live run to merge.
- Don't raise the spend caps above a few cents for CI.

**Verify**
`gh workflow run ci.yml` (or wait for the schedule), artifact present, job summary shows cost > 0
with real prices set.

### 5.2 PostgreSQL operational tests (#18 residual)

**Do**
- [ ] `tests/test_postgres_ops.py` (mark `postgres`):
      - pool exhaustion: `PG_POOL_MAX` small, concurrent requests queue and succeed, no deadlock;
      - connection kill: `SELECT pg_terminate_backend(...)` on an idle connection, next operation
        reconnects;
      - `close()` twice is idempotent; rebuild after close works (already partially covered);
      - long-running transaction does not permanently wedge the pool.
- [ ] Add `PG_POOL_MIN`/`PG_POOL_MAX` settings (defaults 1/10) wired into `storage_postgres.py`;
      document them in `.env.example` and `docs/deployment` (new file or README section).
- [ ] `make test-postgres-ops` target; CI `postgres` job runs it.
- [ ] `docs/limitations.md` §18: state exactly what is now proven (pool sizing, reconnect,
      termination) and what is still out of scope (HA/failover, `pg_ctl` restart of a managed
      instance).

**Don't**
- Don't stand up Patroni/HA in CI.
- Don't test failover by killing the CI service container (flaky).
- Don't change the SQLite default.

**Verify**
`make test-postgres-ops` green locally against Docker Postgres; CI `postgres` job green.

### 5.3 Hosted deploy & smoke (needs owner credentials)

**Do**
- [ ] Deploy `render.yaml` as-is (SQLite + persistent disk, `AUTH_ENABLED=false`) and record the URL
      in `docs/deployment.md`.
- [ ] Extend `scripts/smoke.sh`: `ping → /runtime (assert prices_configured) → run → flag →
      diagnose → confirm → /analytics`. Fail on any non-2xx or missing field.
- [ ] Run the smoke script against the live URL; paste the transcript into the PR.
- [ ] `docs/limitations.md`: replace "deploy unverified" with the URL and date.

**Don't**
- Don't commit secrets, API keys, or the live URL's admin key.
- Don't enable auth on the public demo without also shipping the key-entry UX (already present) and
  documenting the key in the README's operator section only.
- Don't claim deployment success from a local `uvicorn` run.

**Verify**
`bash scripts/smoke.sh https://<live-url>` exits 0; the transcript is in the PR.

### Exit criteria
Live-model path has recurring evidence; PG operations are tested, not assumed; the public demo is
proven end-to-end by a script anyone can rerun.

---

## Phase 6 — Residual UI/UX gaps (≈3 h)

**Goal:** close the two concrete gaps named in `tasks/todo.md` ("Gaps left") and the cheap wins from
the deferred C2 list. Not a redesign.

### Do
- [ ] Empty-trace state: `frontend/src/components/TraceDetailView.tsx` shows an explicit "this trace
      has no spans (fixture row)" panel with the trace id and a link back, instead of an empty
      detail pane.
- [ ] Ground-truth diff: `TokenDiffView.tsx` already reads `trace.expected_type`; verify the API
      always populates it for dataset runs (`api.py:452-464`), and add a fallback line when it is
      `null` ("ground truth not available for this document") instead of the current ambiguous
      display.
- [ ] Analytics CSV export: `AnalyticsView.tsx` "Export CSV" button that serialises the rendered
      tables client-side (no new endpoint).
- [ ] Keyboard shortcuts: `g o` overview, `g t` traces, `g a` analytics, `/` focus search,
      `?` help — implemented in `App.tsx` with an accessible help dialog.
- [ ] Frontend tests: `frontend/src/api/__tests__/` + a component test for the empty state and the
      CSV serializer (`vitest`).

**Don't**
- Don't add WebSockets/real-time updates — the queue poll and refresh control are sufficient and
  a socket is a new failure surface.
- Don't add a theme toggle or chart library; the CSS tokens already support both schemes if a
  toggle is ever requested, but it is not required.
- Don't touch the design system or route structure.

**Verify**
`cd frontend && npm test && npm run build`; manual check at 1440/768/390 for the empty trace and
CSV download.

---

## Phase 7 — Documentation reconciliation & release (≈2 h)

**Do**
- [ ] `docs/limitations.md`: update all touched rows; move #5/#7b/#9/#12/#17 from "known gap" to
      "mitigated" with the exact knob or command; keep #4/#10 honest.
- [ ] New ADRs: `0017-queue-fairness.md`, `0018-prompt-archive.md`, `0019-diagnosis-redaction-policy.md`,
      `0020-payload-reference-mode.md`. Follow the existing ADR shape (context/decision/consequences).
- [ ] `README.md`: status, new env vars, new CLI commands, `/runtime` fields, "prices unset" note.
- [ ] `docs/interview-talking-points.md`: refresh the test count and add the two new answers
      ("why not persist unredacted traces?", "why no broker?").
- [ ] `PHASE-BF-SUMMARY.md`: append a "Phase G — remaining limitations" section with results.
- [ ] Bump `pipeline_version`/`analyzer_version` only if behaviour changed (Phase 3 does); record it
      in the ADR.

**Don't**
- Don't claim a limitation is resolved in prose unless a test named in the same doc proves it.
- Don't renumber existing ADRs.
- Don't edit `PLAN.md` (the historical 2-day plan stays as written).

**Verify**
A doc-consistency pass: every row in the limitations summary table links to a test or a command;
`grep -rn "not implemented\|no .* yet" docs/` returns nothing that contradicts the code.

---

## Sequencing & dependencies

```
Phase 0 (baseline)
   │
   ├── Phase 1 (config honesty) ──────────────┐
   │                                          │
   ├── Phase 2 (payload + re-diagnosis) ──┐   │
   │                                      │   │
   └── Phase 3 (templates + propagation) ─┼───┤
                                          │   │
                    Phase 4 (fairness) ───┘   │
                                              │
                    Phase 5 (verify/deploy) ◄─┘   (needs 1, 2, 3 merged)
                                              │
                    Phase 6 (UI residual) ◄───┘
                                              │
                    Phase 7 (docs/release)
```

- Phase 1 and Phase 3 are independent and can run in parallel.
- Phase 4 touches `queue.py`/`worker.py`; do it after Phase 2 so both changes meet in one review.
- Phase 5 is last in code terms because it verifies everything else.
- Phase 6 can run any time after Phase 1 (it consumes `/runtime`).
- Phase 7 closes the release.

### Effort

| Phase | Work | Estimate |
|---|---|---|
| 0 | Baseline + contract test | 1 h |
| 1 | Prices + auth-off policy | 3 h |
| 2 | Payload mode + re-diagnosis | 5 h |
| 3 | Prompt archive + propagation | 6 h |
| 4 | Fairness + dead letter | 6 h |
| 5 | Live CI + PG ops + deploy | 4 h + credentials |
| 6 | UI residual | 3 h |
| 7 | Docs + release | 2 h |
| **Total** | | **≈30 h** |

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| New config flag silently ignored (the historical `OTEL_*` bug) | Medium | High | Every flag gets a binding test in `tests/test_config.py`; "never let a setting do nothing" rule |
| Reference mode breaks uploads on ephemeral disks | Medium | High | Default stays `inline`; enqueue-time guard rejects unresolvable docs |
| Re-diagnosis contract change breaks the UI | Medium | Medium | Return stored diagnosis (200) instead of erroring; UI banner only for the forced path |
| Prompt extraction changes a byte | Low | High | Hash-equality test comparing pre/post strings; no wording edits in the move commit |
| Propagation rule lowers benchmark accuracy | Medium | Medium | Treat a drop as a bug; keep the old heuristic behind the same check name and compare fixtures |
| Fairness subquery slows claim on SQLite | Low | Medium | Index already covers `(status, priority DESC, created_at)`; add per-tenant index `(tenant_id, status)` if `EXPLAIN` shows a scan |
| PG ops tests flaky in CI | Medium | Medium | Bound concurrency, use `pg_terminate_backend` on a known PID, retry once with backoff |
| Live CI spends money | Low | Medium | Weekly schedule, one doc, `$0.05` per-run cap, skip on 429 |
| Deploy needs owner credentials | High | Medium | Explicitly a Phase 5.3 user-action item; do not fake it |

---

## Definition of done (whole plan)

- [ ] `make check` green; `make test-postgres` and `make test-postgres-ops` green with a server.
- [ ] `cd frontend && npm test && npm run build` green.
- [ ] `DRY_RUN=true python -m forensics.cli demo` reproduces all 15 labels and 5/5 seeded root causes.
- [ ] `DRY_RUN=true python -m forensics.cli benchmark-analyzer` still 100% step/category on eligible
      faults, with `context_loss` reported `n/a` (not a miss).
- [ ] Every new env var: bound, documented in `.env.example`, covered by a test.
- [ ] `docs/limitations.md` table has no row claiming "not implemented" for something shipped, and no
      row claiming "resolved" without a named test or command.
- [ ] Hosted smoke transcript attached; live-model artifact present.
- [ ] No unredacted document text in `job_queue` after a reference-mode run.

---

## Explicitly out of scope (do not build these)

These are tempting but wrong for this tool's stated purpose ("forensics, not monitoring"):

- A monitoring/alerting platform (live dashboards, paging, SLOs).
- A general-purpose agent framework or multi-agent orchestration.
- A vector database / semantic search over traces.
- A full dataflow/taint analysis engine for propagation.
- Multi-region, HA, or autoscaling infrastructure.
- A plugin marketplace or third-party plugin execution sandbox.
- Rewrites of SQLite storage, the React app, or the analyzer algorithm.

If any of these becomes a real requirement, it is a new project with its own plan — not a phase here.
