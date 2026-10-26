# Failure Forensics for LLM Pipelines

A multi-step LLM pipeline can produce a wrong answer while every step reports
success. This tool records all four steps of such a pipeline, points at the
first step that actually broke, and turns each human-confirmed failure into a
permanent regression test.

It is a **forensics** tool, not a monitoring dashboard: it answers *"which step
broke, and why?"* after a bad output, with evidence you can audit.

---

## The loop

```
document
  → pipeline          four traced steps: prompt, response, tokens, latency
  → trace             full evidence per step, written to JSON + SQLite
  → analysis          seven deterministic checks, no model required
  → diagnosis         first failing span + category + evidence chain
  → human confirm     approve the verdict, or override it
  → eval case         bad output + corrected output, version-pinned
  → scheduled eval    replayed nightly; alert on a regression
```

1. **Trace** every step: prompt, raw response, parsed output, tokens, latency.
2. **Localize** the root cause with deterministic checks — no model guessing.
3. **Confirm** (or override) the verdict as a human.
4. **Re-test** confirmed failures on every run, so a fixed bug stays fixed.

---

## Architecture

```
                    ┌───────────────────────────────────────────────┐
   CLI · REST API   │  React explorer (served by FastAPI)           │
   Typer · FastAPI  │  Explorer · DAG + diff · Analytics            │
                    └──────────────────────┬────────────────────────┘
                                           │
                                   process_document
                                           │
        ┌──────────────────────────────────▼───────────────────────────────┐
        │  Pipeline:  intake → extraction → classification → summarization │
        │  Every step is wrapped in @traced_step and emits one Span        │
        └───────────────────────────────┬──────────────────────────────────┘
                                        │
                 ┌──────────────────────┼──────────────────────┐
                 ▼                      ▼                      ▼
          traces/*.json           forensics.db            OTel mirror
           (evidence)          (index + state)            (optional)
                 │
                 ▼
        analyze_trace() — 7 deterministic checks
                 │
        first failing span → root cause + category + evidence
                 │
                 ▼ (optional, off by default)
        Judge LLM — cross-family second opinion
                 │
                 ▼
   flag → diagnosis → confirm/override → EvalCase → eval → alert
```

The API, the demo runner, and the worker queue all call the same
`process_document`; the CLI performs the same trace → analyze → persist
sequence directly. A document therefore produces the same records however it
was submitted.

### Components

| Component | Module | Responsibility |
|---|---|---|
| Settings | `config.py` | Env → typed settings, fail-fast validation, model routing |
| Domain models | `models.py` | Pydantic records: `Document`, `Span`, `Trace`, `Diagnosis`, `Flag`, `EvalCase` |
| Pipeline | `pipeline.py` | The four steps and the chain that runs them |
| Tracing | `tracing.py` | `@traced_step`, span lifecycle, status, JSON + SQLite write |
| LLM client | `llm.py` | NVIDIA NIM: 3-tier structured output, retries, cache, spend caps |
| Analyzer | `analyzer_simple.py` | 7 deterministic checks + root-cause finder |
| Check registry | `checks.py` | Built-in checks and `CHECK_PLUGINS` custom checks |
| Storage | `storage.py` | JSON payloads + SQLite index, all tenant-scoped |
| Eval engine | `eval_simple.py` | Flag → EvalCase → run → fixed/still-failing |
| API | `api.py` | FastAPI JSON API, auth, static hosting of the React build |
| CLI | `cli.py` | `forensics` command: run, demo, eval, auth, queue, scheduler |
| Auth | `auth.py` | bcrypt API keys, tenants, immediate revocation |
| Scheduler | `scheduler.py` | Cron eval + HMAC-signed regression webhooks |
| Queue / worker | `queue.py`, `worker.py` | Atomic SQLite job claims, graceful shutdown |
| PII / OTel | `pii.py`, `otel_export.py` | Redaction at the write boundary; span mirroring |
| Documents | `documents.py` | Doc-id allowlist, path containment, per-tenant uploads |

---

## The pipeline

| # | Step | What it does | LLM? | Output |
|---|---|---|---|---|
| 1 | **Intake** | Whitespace/encoding normalization | No — deterministic | `cleaned_text`, `char_count` |
| 2 | **Extraction** | Pulls entities with character offsets | Yes | `entities[]` (value, type, offsets, confidence) |
| 3 | **Classification** | Assigns one `DocumentType` | Yes | `document_type`, `confidence`, `all_scores` |
| 4 | **Summarization** | Summarizes using the classified type | Yes | `summary`, `key_points[]` |

Document text is always fenced and labeled as untrusted data before it reaches
a prompt, because a document can contain instructions.

Each step is wrapped in `@traced_step`, which captures input, output, prompt,
raw response, token counts, latency, status, and a confidence score. The LLM
client publishes model/token provenance through a `ContextVar`, so a span can
answer *"what did this cost, and which model was wrong?"*

---

## Diagnosis

Seven deterministic checks run per span. No model is required to get a verdict.

| Check | Question it answers | Applies to |
|---|---|---|
| `entity_grounding` | Does each extracted value literally appear in the source? | extraction |
| `label_enum` | Is the label a valid `DocumentType`? | classification |
| `label_grounding` | Does the label match known ground truth? | classification (when labels exist) |
| `date_regex` | Is each date a recognizable date in any common format? | extraction |
| `context_preservation` | Do extracted entities survive into the summary? | summarization |
| `propagation` | Did downstream steps correctly consume upstream output? | summarization |
| `prompt_schema` | Does the output conform to the expected structure? | extraction, classification, summarization |

**Root cause** = the first span with any failed check. Steps after it are marked
*tainted*, not blamed — that distinction is what stops a cascade from being
reported as several independent failures.

Each diagnosis carries a category, a confidence, and an evidence chain of
human-readable citations from the actual input/output.

| Category | Typical cause |
|---|---|
| `extraction_hallucination` | Entity that is not in the document |
| `misclassification` | Valid but wrong document type |
| `context_loss` | Extracted facts dropped from the summary |
| `propagation_error` | Downstream step misread upstream output |
| `prompt_failure` | Malformed or unparseable output |
| `unknown` | Failed, but no specific check matched |

### Optional judge

Set `JUDGE_ENABLED=true` to add a cross-family judge model that scores semantic
fidelity. It runs when deterministic checks find a failure (or always, with
`JUDGE_FORCE=true`) and can override the deterministic verdict; the override is
recorded, not hidden. Off by default: the deterministic checks decide.

### Custom checks

Register a domain-specific check in about ten lines and load it with
`CHECK_PLUGINS=my_module:my_check`. Plugins are validated at startup, so a bad
path fails fast instead of silently doing nothing. See `tests/plugin_samples.py`.

---

## What a trace contains

A `Trace` holds four `Span`s plus totals (latency, tokens, cost). Each span
stores:

- the exact input and output JSON
- the full rendered prompt and the raw model response
- model id, provider, token counts, latency, cache hit flag
- status (`success` / `failure` / `degraded`) and confidence

Every record also carries `pipeline_version`, `prompt_version`, `schema_version`,
and `tenant_id`, so a result can be attributed to the code and prompts that
produced it.

### Storage layout

| Path | Contents |
|---|---|
| `data/traces/<trace_id>.json` | Full trace payload, inspectable and diffable |
| `data/forensics.db` | SQLite index: traces, flags, diagnoses, eval cases/runs/results, queue, auth (WAL mode) |
| `data/cache/` | LLM response cache keyed by model + prompt + prompt version |
| `data/spend.json` | Global spend ledger |
| `data/documents/<tenant>/` | Documents uploaded through the API |

JSON is the evidence; SQLite is the index and the relational state. Both are
written through one `finalize_trace` path, after PII redaction if enabled.

---

## Quickstart

**Requirements:** Python 3.12 (3.13 is not supported), Node.js 20.19+ or
22.12+ for the frontend, and optionally an NVIDIA NIM API key.

```bash
make install                 # .venv, Python deps, frontend deps
cp .env.example .env
$EDITOR .env                 # set NVIDIA_API_KEY + model slugs; for offline mode, DRY_RUN=true
make check                   # ruff + mypy strict + pytest
```

`make check` is green from a clean checkout: **455 tests**, no network, no API
key required.

### Demo — no API key needed

`DRY_RUN=true` replays a checked-in fixture keyed by document id, so the whole
loop works offline.

```bash
DRY_RUN=true python -m forensics.cli demo     # all 15 documents
python -m forensics.cli validate-dataset      # dataset integrity
make run-web                                  # build UI + serve API on :8000
```

The demo reproduces all five seeded failures at their declared step and
category. That is a fixture proving the analyzer and eval loop work — it is not
a model benchmark. A real accuracy number needs a real key.

### Demo — real model, real numbers

With a key and `DRY_RUN=false`, the same command runs the configured model:

```bash
python -m forensics.cli demo                 # 15 documents, paced for the rate limit
python -m forensics.cli benchmark-analyzer   # fault-injection scorecard
python -m forensics.cli seed                 # load the committed real traces
pytest -m live                               # one real end-to-end smoke test
```

Measured on 2026-10-24 with `minimaxai/minimax-m3.1-flash`:

| Measurement | Result |
|---|---|
| Classifications matching the dataset labels | 14/15 (the miss calls the mixed incident report a report) |
| Fault-injection step localization | 100% (7/7) |
| Fault-injection category accuracy | 100% (7/7) |
| Traces with a spurious root cause | 0/15 on the clean baseline |

The five traces under `dataset/live/traces/` are that run's actual output.
`forensics seed` loads them into an instance's database (idempotently), which is
how a fresh deployment has real traces and diagnoses before anyone spends
quota. `pytest -m live` skips when no real key is configured, and also skips
rather than fails when the hosted per-minute quota is exhausted — that is an
environment condition, not a code result.

---

## Web UI

`make run-web` builds the React app and serves it from FastAPI at
`http://127.0.0.1:8000`.

| Tab | What it shows |
|---|---|
| **Home** | What the tool does, the four pipeline strata, engineering guarantees |
| **Trace Explorer** | Trace list with search/status filters, per-step spine, prompt/response detail, checks and evidence, flag and confirm/override |
| **DAG + Diff** | Pipeline graph colored by status, expected vs actual vs grounded-token diff |
| **Analytics** | Failure rate, failures by category and step, time to root cause, eval history |

The header has a **Run Demo** button (runs all 15 documents) and **Settings**,
where an API key can be stored in the browser for authenticated deployments.

`streamlit_app.py` is the legacy explorer; the React app replaced it.

---

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness |
| `POST` | `/pipeline/run` | Run one document inline, or queue it when `QUEUE_ENABLED=true` (202 + `job_id`) |
| `POST` | `/pipeline/run-demo` | Run and diagnose all 15 dataset documents |
| `GET` | `/traces?limit=` | List traces (newest first) |
| `GET` | `/traces/{id}` | One trace with all spans |
| `POST` | `/traces/{id}/flag` | Flag a trace for review |
| `POST` | `/traces/{id}/diagnose` | Re-run the analyzer on a stored trace |
| `GET` | `/traces/{id}/diagnosis` | Latest diagnosis |
| `POST` | `/diagnoses/{id}/confirm` | Confirm or override (category, step, reason) |
| `GET` | `/eval/cases`, `/eval/runs`, `/eval/runs/{id}/results` | Eval history |
| `POST` | `/eval/run` | Trigger an eval run |
| `GET` | `/jobs`, `/jobs/{id}` | Queue status |
| `GET` | `/analytics` | Aggregated reliability snapshot |
| `GET` | `/scheduler/status` | Schedule, next run, recent runs |

When `AUTH_ENABLED=true`, data routes require `Authorization: Bearer ffk_…`.

---

## CLI

```bash
forensics ping                    # verify config and model routing
forensics run <doc.txt>           # one document, end to end
forensics demo                    # all 15 documents + summary
forensics seed                    # load the committed real-model traces
forensics eval                    # replay accumulated eval cases
forensics eval-cases              # turn confirmed flags into eval cases
forensics analytics               # print the analytics snapshot
forensics validate-config         # validate settings without running
forensics validate-dataset        # check all 15 docs and labels (--dir for another corpus)
forensics benchmark-analyzer      # fault-injection scorecard for the analyzer
forensics generate-dataset generate --output dataset_large --per-type 30 --seed 12345
forensics generate-dataset validate --dir dataset_large
```

The generator is seeded and clock-free: the same `--seed` reproduces the corpus
byte-for-byte, which is what makes `dataset_large/` (150 docs, 43 seeded
failures) auditable against the code that produced it.

Operational subcommands:

```bash
forensics auth create-tenant --name "My Team"
forensics auth create-key --tenant <id> --name ci    # key is printed once
forensics auth create-key --tenant <id> --scope read  # read-only key
forensics auth list-keys --tenant <id>
forensics auth revoke-key --key-id <id>

forensics scheduler-start | scheduler-status

forensics worker start
forensics queue enqueue --file doc.txt --expected invoice
forensics queue status --job-id <id>
forensics queue list --status pending
forensics queue reaper [--stall-timeout N]  # reclaim stalled jobs from crashed workers
```

---

## Configuration

Everything is environment-driven; `.env.example` documents every setting.

| Area | Key variables |
|---|---|
| Secrets | `NVIDIA_API_KEY`, `NVIDIA_BASE_URL` |
| Models | `PIPELINE_MODEL_ID/_PROVIDER/_FAMILY`, `PIPELINE_FALLBACK_MODEL_*`, `JUDGE_MODEL_*`, `CHEAP_MODEL_*`, `*_PRICE_IN/_OUT`, `*_MAX_TOKENS` |
| Cost control | `SPEND_CAP_PER_RUN_USD`, `SPEND_CAP_GLOBAL_USD`, `CACHE_ENABLED`, `DRY_RUN` |
| Diagnosis | `JUDGE_ENABLED`, `JUDGE_FORCE`, `CHECK_PLUGINS`, `ANALYZER_VERSION` |
| Privacy | `PII_REDACTION_ENABLED`, `PII_ENTITY_TYPES`, `PII_CUSTOM_PATTERNS` |
| Auth | `AUTH_ENABLED`, `AUTH_API_KEY_PREFIX`, `AUTH_BCRYPT_ROUNDS`, `AUTH_KEY_TTL_DAYS` |
| Scheduling | `EVAL_SCHEDULE`, `EVAL_SCHEDULE_TIMEZONE`, `ALERT_WEBHOOK_URL`, `ALERT_WEBHOOK_SECRET`, `ALERT_REGRESSION_THRESHOLD` |
| Queue | `QUEUE_ENABLED`, `QUEUE_WORKER_CONCURRENCY`, `QUEUE_POLL_INTERVAL`, `QUEUE_JOB_TTL`, `QUEUE_STALL_TIMEOUT` |
| Rate Limiting | `RATE_LIMIT_ENABLED`, `RATE_LIMIT_REQUESTS_PER_MINUTE`, `RATE_LIMIT_BURST` |
| Cost guard | `SPEND_CAP_PER_RUN_USD`, `SPEND_CAP_GLOBAL_USD`, `SPEND_PREFLIGHT_ENABLED` |
| Database | `DATABASE_URL` (PostgreSQL) |
| Observability | `OTEL_EXPORTER`, `OTEL_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_SAMPLING_RATE` |
| Paths / server | `DATA_DIR`, `TRACES_DIR`, `DB_PATH`, `CACHE_DIR`, `SPEND_FILE`, `STATIC_DIR`, `CORS_ORIGINS` |

Settings are validated at startup: every model role needs an id, a provider and
a family, and the judge must come from a different family than the pipeline
models when `JUDGE_FAMILY_SEPARATION_REQUIRED=true`. A misconfigured deploy
fails immediately instead of at the first request.

### Opt-in capabilities

| Capability | Enable with | What changes |
|---|---|---|
| Multi-tenancy | `AUTH_ENABLED=true` | Every record carries a tenant; reads and writes are scoped |
| PII redaction | `PII_REDACTION_ENABLED=true` | Prompt, response, and payloads redacted **before** disk |
| OpenTelemetry | `OTEL_EXPORTER=console\|otlp` | Spans mirrored with GenAI semantic conventions |
| Scheduled eval | `EVAL_SCHEDULE=0 2 * * *` | Nightly eval inside the web process, signed webhook alerts |
| Worker queue | `QUEUE_ENABLED=true` | Runs move to `forensics worker start`; requests return 202 |
| Custom checks | `CHECK_PLUGINS=mod:fn` | Domain checks join the deterministic suite |
| Judge LLM | `JUDGE_ENABLED=true` | Optional semantic second opinion |
| Job Reaper | `QUEUE_STALL_TIMEOUT=300` | Auto-reclaim stalled jobs from crashed workers |
| Rate Limiting | `RATE_LIMIT_ENABLED=true` | Per-tenant token bucket with 429 + `Retry-After` |
| API Key Expiry | `AUTH_KEY_TTL_DAYS=90` | Keys auto-expire after configured days |
| Key scopes | `auth create-key --scope read` | A key can be limited to reads; `write` implies read, `*` is everything |
| PostgreSQL Backend | `DATABASE_URL=postgresql://...` (+ `pip install -e ".[postgres]"`) | Horizontal scaling with asyncpg |

All are off by default, so the base system needs no broker, collector, or
scheduler service — only the model API.

---

## Evaluation loop

1. A trace gets flagged — by a human, or automatically for a failing run.
2. The diagnosis is confirmed or overridden (`override_step` exists for the
   case where a human sees what the analyzer missed).
3. A confirmed failure becomes an `EvalCase`: the bad output and the corrected
   output, pinned to the pipeline and prompt versions that produced it.
4. An eval run replays every case through the current pipeline. A case counts
   as **fixed** only when the analyzer passes it, blames a different step, and
   the corrected output actually holds.
5. Results are stored per run, so accuracy becomes a trend rather than a
   memory. A drop beyond `ALERT_REGRESSION_THRESHOLD` fires an HMAC-signed
   webhook.

`POST /eval/run` seeds cases from diagnosed failures when the suite is empty,
which makes the demo work without manual setup.

---

## Testing

The suite is hermetic: no network, no live model calls, every test writes to a
temporary data directory. Live tests are marked and deselected by default.

```bash
make check                  # lint + typecheck + test
make test                   # pytest only
pytest tests/test_auth.py -v
pytest -m live              # real NIM calls; uses hosted quota
make test-postgres          # the PostgreSQL backend, against a live server
```

The PostgreSQL backend is exercised against a real database, not mocked:

```bash
docker run -d --name forensics-pg \
  -e POSTGRES_PASSWORD=forensics -e POSTGRES_USER=forensics \
  -e POSTGRES_DB=forensics -p 55432:5432 postgres:16-alpine
export FORENSICS_TEST_POSTGRES_URL=postgresql://forensics:forensics@127.0.0.1:55432/forensics  # pragma: allowlist secret
make test-postgres
```

Without that variable the PostgreSQL tests skip, and
`tests/test_schema_parity.py` still compares the two schemas table by table in
every run, so drift cannot ship silently.

Each feature spec ships with the test file that proves it:

| Spec | Test file |
|---|---|
| 01 OpenTelemetry export | `tests/test_otel_export.py` |
| 02 Judge LLM | `tests/test_judge_llm.py` |
| 03 PII redaction | `tests/test_pii_redaction.py` |
| 04 Auth / multi-tenancy | `tests/test_auth.py` |
| 05 Custom check plugins | `tests/test_check_plugins.py` |
| 06 Scheduled eval | `tests/test_scheduler.py` |
| 07 Worker queue | `tests/test_queue.py` |

Storage backends: `test_storage_postgres.py` (live PostgreSQL),
`test_schema_parity.py` (SQLite vs PostgreSQL DDL), `test_benchmark_scorecard.py`
(scorecard aggregation).

Core behaviour: `test_tracing_phase2.py`, `test_analyzer_phase3.py`,
`test_storage_eval_api.py`, `test_llm.py`, `test_dry_run_dataset.py`
(asserts the offline fixture reproduces every label), `test_review_findings.py`
(regression tests for fixed bugs), and `test_imports.py` (import-order guard).

---

## Deploy

`render.yaml` is a Render blueprint: one web service runs
`uvicorn forensics.api:app`, serving the JSON API and the compiled React UI.

```bash
# 1. Render → New → Blueprint → this repository
#    Set NVIDIA_API_KEY in the dashboard (marked sync: false).
# 2. Verify the live service:
./scripts/smoke.sh https://<your-service>.onrender.com
#    With AUTH_ENABLED=true:
API_KEY=ffk_... ./scripts/smoke.sh https://<your-service>.onrender.com
```

`scripts/smoke.sh` walks `health → UI → seed → flag → diagnose → confirm →
analytics` and exits non-zero on the first failure, so it doubles as a deploy
gate.

Two deployment facts worth knowing:

- **The persistent disk needs a paid instance.** On the free plan, delete the
  `disk:` block; the filesystem is ephemeral and the demo is re-seeded by the
  smoke test.
- **The disk mounts at `/var/data`, not the repo's `data/`.** A build recreates
  the checkout, so all output paths are pointed at the mount.

Optional services follow the same switches as local mode: auth (`AUTH_ENABLED`),
nightly eval (`EVAL_SCHEDULE` + webhook vars, with the instance kept running),
and the worker queue (a second service running `forensics worker start`, sharing
the disk and env).

---

## Project layout

```
src/forensics/          Python package (see the component table above)
tests/                  455 hermetic tests
frontend/               React 19 + Vite + TypeScript explorer
dataset/
  documents/            15 synthetic documents, 3 per type
  labels.json           ground truth: type, should_fail, step, category
docs/
  architecture.md       deeper architecture and data flow
  limitations.md        every known gap, with impact and mitigation
  adr/                  architecture decision records
  specs/                per-feature contracts
scripts/smoke.sh        end-to-end deploy gate
streamlit_app.py        legacy explorer
render.yaml, Procfile   deploy definitions
```

---

## Design rules

- **Deterministic first.** A verdict must be explainable from recorded data; the
  judge is an optional second opinion, never the only evidence.
- **One write path.** Traces are persisted through `finalize_trace`, so status,
  totals, redaction, and OTel mirroring cannot drift apart.
- **One LLM entry point.** Prompt, response, tokens, retries, cache, and spend
  are captured in `llm.py`, never in a step.
- **Tenant every record.** Auth off still stamps a concrete tenant, so turning
  auth on later does not orphan data.
- **No secrets in git.** `.env` is ignored; `detect-secrets` runs in pre-commit.
- **No real PII in the dataset.** All documents are synthetic.
- **A spec is not done until its named test file passes.**

### Honest boundaries

This is a forensics tool, not a production monitoring platform. The significant
gaps, all documented in [`docs/limitations.md`](docs/limitations.md):

- A worker killed mid-job leaves that job `running`; there is no reaper.
- SQLite allows one writer at a time (WAL makes readers concurrent).
- Judge and propagation analysis are heuristics, not full dataflow analysis.
- Prices default to `0.0` until real ones are configured, so cost reads zero.
- No rate limiting, no key expiry, no scopes.
- Live model behavior is not exercised in CI.
- The dataset is 15 documents: illustrative, not statistically meaningful.

---

## Documentation

| Question | Read |
|---|---|
| How do the pieces fit? | [`docs/architecture.md`](docs/architecture.md) |
| What does it not do? | [`docs/limitations.md`](docs/limitations.md) |
| Why is it built this way? | [`docs/adr/`](docs/adr) |
| What was each feature supposed to do? | [`docs/specs/`](docs/specs) |
| What is left to do? | [`plan/forensics-fix-and-finish.md`](plan/forensics-fix-and-finish.md) |

## License

MIT.
