# Failure Forensics Tool for AI Pipelines

Trace every step of a multi-step LLM pipeline, localize the exact origin of a
bad output, and feed human-confirmed failures back into a growing eval dataset.

## Status

Implemented and tested:

| Area | Where | Evidence |
|---|---|---|
| 4-step pipeline + tracing | `pipeline.py`, `tracing.py` | `tests/test_tracing_phase2.py` |
| 7 deterministic checks + root cause | `analyzer_simple.py` | `tests/test_analyzer_phase3.py` |
| Custom check plugins | `checks.py` | `tests/test_check_plugins.py` (spec 05) |
| Judge LLM (cross-family, structured) | `llm.py` | `tests/test_judge_llm.py` (spec 02) |
| PII redaction before persistence | `pii.py` | `tests/test_pii_redaction.py` (spec 03) |
| API keys + tenant isolation | `auth.py`, `migrations.py` | `tests/test_auth.py` (spec 04) |
| Scheduled eval + regression alerts | `scheduler.py` | `tests/test_scheduler.py` (spec 06) |
| SQLite worker queue | `queue.py`, `worker.py` | `tests/test_queue.py` (spec 07) |
| OTel export | `otel_export.py` | `tests/test_otel_export.py` |
| React trace explorer + FastAPI | `frontend/`, `api.py` | `scripts/smoke.sh` |

`make check` (ruff + mypy strict + pytest) is green from a clean checkout:
**390 tests**, no optional extras required beyond `make install`.

Known gaps, stated plainly:

- **The Render deploy is configured but not yet live.** `render.yaml` and
  `scripts/smoke.sh` are ready and were verified against a local server running
  the exact build and start commands; publishing needs Render account access.
- A worker that dies mid-job leaves that job `running`; there is no reaper yet.
  See `docs/limitations.md`.
- `README`-documented demo paths use `DRY_RUN=true` unless a real
  `NVIDIA_API_KEY` is configured.

## Requirements

- Python 3.12 (pinned in `pyproject.toml`; `uv` manages the venv). 3.13 is not
  supported yet.)
- Node.js 20.19+ or 22.12+ (for the React frontend — `vite@8` and
  `@rolldown/*` declare `"node": "^20.19.0 || >=22.12.0"`)
- An NVIDIA NIM API key in `.env` (copy `.env.example`) — *not required for demo
  mode with `DRY_RUN=true`, which runs fully offline*

## Quickstart

```bash
make install                # .venv + Python deps (incl. otel extra) + frontend deps
cp .env.example .env
$EDITOR .env                # set NVIDIA_API_KEY and model slugs (or leave dummy for demo)
make check                  # ruff + mypy strict + pytest
```

## Demo (No API Key Required)

`DRY_RUN=true` reproduces the five seeded failures from the checked-in dataset
without a credential or a network call. Without it, `demo` calls the real model
and therefore needs `NVIDIA_API_KEY`.

```bash
DRY_RUN=true python -m forensics.cli demo    # all 15 documents, offline
python -m forensics.cli validate-dataset     # dataset integrity
DRY_RUN=true python -m forensics.cli run dataset/documents/contract_001.txt
python -m forensics.cli analytics
make run-web                                 # React UI + API
```

## Deploy

`render.yaml` is a Render blueprint: one web service running
`uvicorn forensics.api:app`, which serves the JSON API and mounts the compiled
React UI.

```bash
# 1. Render → New → Blueprint → this repository. Set NVIDIA_API_KEY in the
#    dashboard (the blueprint marks it sync: false, so it is never read from git).
# 2. Verify the live service:
./scripts/smoke.sh https://<your-service>.onrender.com
#    ...and if AUTH_ENABLED=true:
API_KEY=ffk_... ./scripts/smoke.sh https://<your-service>.onrender.com
```

`scripts/smoke.sh` runs the loop a demo has to survive — `health → seed → flag →
diagnose → confirm → analytics` — and exits non-zero on the first failure, so it
doubles as a deploy gate. It seeds the dataset itself when the instance is empty.

Two caveats, both real:

- **The disk needs a paid instance.** The blueprint declares `plan: starter`
  because `disk:` requires it. On the free plan, delete the `disk:` block: the
  filesystem is ephemeral, `data/` is re-created on every deploy, and the smoke
  test re-seeds the demo. (Render also spins idle free services down after
  ~15 minutes, so a nightly eval would not fire.)
- **The disk is mounted at `/var/data`, not at the repository's `data/`.** A build
  recreates the checkout, so the blueprint points `DATA_DIR`, `TRACES_DIR`,
  `CACHE_DIR`, `DB_PATH`, `SPEND_FILE` and `SCHEDULER_DB_PATH` at the mount
  instead. Same guarantee, none of the build fragility.

Enabling the optional features:

| Feature | Environment | Extra step |
|---|---|---|
| API keys | `AUTH_ENABLED=true` | Mint a key against the deployed database (`forensics auth create-key --tenant default` in a Render shell), then send `Authorization: Bearer ffk_…` |
| Nightly eval + alerts | `EVAL_SCHEDULE=0 2 * * *`, `ALERT_WEBHOOK_URL`, `ALERT_WEBHOOK_SECRET` | None: the scheduler runs inside the web process, so the instance must stay running |
| Worker queue | `QUEUE_ENABLED=true` | A second service with `startCommand: forensics worker start`, sharing the disk and env |

## Layout

| Path | Purpose |
|---|---|
| `src/forensics/config.py` | Settings from env + fail-fast startup validation |
| `src/forensics/models.py` | Pydantic domain models and the `tenant_id` every record carries |
| `src/forensics/llm.py` | NVIDIA NIM client: 3-tier structured output, caching, spend caps |
| `src/forensics/pipeline.py` | 4 steps: intake, extraction, classification, summarization |
| `src/forensics/tracing.py` | `@traced_step` decorator, span lifecycle, JSON + SQLite writer |
| `src/forensics/storage.py` | JSON payloads + SQLite index and relational tables |
| `src/forensics/migrations.py` | Idempotent schema migrations (`tenant_id`, auth tables) |
| `src/forensics/analyzer_simple.py` | 7 deterministic checks + root cause finder |
| `src/forensics/checks.py` | Check registry and `CHECK_PLUGINS` loader |
| `src/forensics/eval_simple.py` | Eval engine: flag → EvalCase → runner |
| `src/forensics/auth.py` | API keys (bcrypt) and tenant management |
| `src/forensics/scheduler.py` | APScheduler nightly eval + webhook regression alerts |
| `src/forensics/queue.py` | SQLite job queue with atomic claims |
| `src/forensics/worker.py` | Pipeline worker + the shared `process_document` |
| `src/forensics/api.py` | FastAPI REST API |
| `src/forensics/cli.py` | Command line entry point |
| `src/forensics/otel_export.py` | OpenTelemetry export |
| `src/forensics/pii.py` | PII redaction (persistence boundary) |
| `src/forensics/faults.py` | Fault injection for the analyzer scorecard |
| `streamlit_app.py` | Legacy Streamlit explorer (React replaced it) |
| `frontend/` | React + Vite + TypeScript trace explorer |
| `dataset/` | 15 demo documents + `labels.json` ground truth |
| `render.yaml`, `Procfile` | Deploy definitions |
| `scripts/smoke.sh` | End-to-end deploy gate |
| `data/` | Runtime data, gitignored (SQLite + per-trace JSON) |

## Commands

`make help` lists everything. The ones that matter:

```bash
make check          # lint + typecheck + test
make test           # pytest, live API tests deselected
make test-live      # includes real NIM calls — hosted quota
make dev            # FastAPI with reload
make run-web        # React dev server + FastAPI
```

### CLI Subcommands

```bash
forensics ping               # test config and model info
forensics run <doc>          # run the pipeline on one document
forensics validate-dataset   # check all 15 docs exist and are readable
forensics demo               # run all 15 docs, print classification summary
forensics eval               # run eval cases, print pass/fail table
forensics analytics          # print analytics
forensics validate-config    # validate config without running
forensics benchmark-analyzer # fault injection scorecard

forensics auth create-tenant --name "My Team"     # prints the tenant_id
forensics auth create-key --tenant <id> --name ci # prints the key ONCE
forensics auth list-keys --tenant <id>            # never prints the key or its hash
forensics auth revoke-key --key-id <id>

forensics scheduler-start    # foreground scheduler (systemd / container PID 1)
forensics scheduler-status   # schedule, next run, recent runs

forensics worker start                                # foreground worker
forensics queue enqueue --file doc.txt --expected invoice
forensics queue status --job-id <id>
forensics queue list --status pending
```

## Testing

Every spec ships with the test file that proves it. The suite is hermetic: no
network, no live model calls (those are marked `live` and deselected by default),
and every test writes to a temporary data directory.

```bash
make test                                        # the whole suite
pytest tests/test_auth.py -v                     # one spec
pytest -m live                                   # real NIM calls, uses quota
```

| Spec | Test file |
|---|---|
| 01 OTel export | `tests/test_otel_export.py` |
| 02 judge LLM | `tests/test_judge_llm.py` |
| 03 PII redaction | `tests/test_pii_redaction.py` |
| 04 auth / multi-tenant | `tests/test_auth.py` |
| 05 custom check plugins | `tests/test_check_plugins.py` |
| 06 scheduled eval | `tests/test_scheduler.py` |
| 07 worker queue | `tests/test_queue.py` |

Core behaviour that the specs build on: `tests/test_tracing_phase2.py`,
`tests/test_analyzer_phase3.py`, `tests/test_storage_eval_api.py`,
`tests/test_llm.py`, `tests/test_models.py`, `tests/test_config.py`,
`tests/test_imports.py` (import-order guard), `tests/test_scaffold.py`.

## Architecture

```
Document → [Intake] → [Extraction] → [Classification] → [Summarization]
                ↓            ↓                ↓                  ↓
            @traced_step  @traced_step    @traced_step       @traced_step
                ↓            ↓                ↓                  ↓
            Span JSON + SQLite (4 spans per trace, tenant-scoped, PII-redacted)
                ↓
        analyze_trace() — 7 deterministic checks:
            • Entity grounding: value must appear in source text
            • Label enum: classification must be a valid DocumentType
            • Date regex: date entities must match a date format
            • Context preservation: extraction entities survive summarization
            • Propagation: downstream steps correctly read upstream outputs
            • Schema validity: LLM outputs conform to the expected structure
            • Label grounding: classification matches ground truth (when available)
        → First span with ANY failed check = root cause
        → Judge LLM (optional, off by default): semantic analysis, cross-family
                ↓
        Flag → diagnose → confirm → EvalCase → nightly eval → regression alert
```

Runs reach the pipeline three ways, all through the same `process_document`:
inline (`POST /pipeline/run`), queued (`QUEUE_ENABLED=true` + `forensics worker
start`), or from the CLI. Multi-tenancy is opt-in (`AUTH_ENABLED=true`); when it
is off, every record lands in one `default` tenant and the API behaves as it did
before auth existed.

## Seeded Failures (Ground Truth)

| Document | Type | Seeded Failure | Step | Category |
|---|---|---|---|---|
| contract_001 | contract | ✅ | classification | misclassification |
| invoice_001 | invoice | ✅ | extraction | extraction_hallucination |
| report_001 | report | ✅ | summarization | prompt_failure |
| correspondence_001 | correspondence | ✅ | summarization | propagation_error |
| mixed_001 | mixed | ✅ | classification | misclassification |

*Note: `DRY_RUN=true` returns document-keyed canned responses, so a dry run
reproduces every row above at its declared step and category, and the ten clean
documents pass. `forensics demo` reports 13/15 classifications: the two misses
are `contract_001` and `mixed_001`, which is the fixture reproducing their seeded
misclassification. Seeded failures are ground truth for eval.
`tests/test_dry_run_dataset.py` asserts this against `labels.json`.*

## Rules

- **Never call the live API in a unit test.** Use record/replay; live tests are
  marked `@pytest.mark.live`.
- **No secrets in git.** `.env` is ignored; `detect-secrets` runs in pre-commit.
- **No invented model names or prices.** Model slugs/prices come from `.env`.
- **The judge model must be from a different provider family** than the pipeline
  models (config asserts this when `JUDGE_FAMILY_SEPARATION_REQUIRED=true`).
- **No real PII in the dataset.** All documents are synthetic.
- **A spec is not done until its named test file passes.**

## Planning

`PLAN.md` is the original 2-day build plan, `PROMPT.md` the master planning
prompt, `plan/forensics-fix-and-finish.md` the fix-and-finish plan this tree
follows, and `docs/specs/` the per-feature specs.

## License

MIT
