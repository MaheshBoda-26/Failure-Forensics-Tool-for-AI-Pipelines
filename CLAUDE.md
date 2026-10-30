# CLAUDE.md — Project Context for AI Assistants

## Project: Failure Forensics Tool

A failure forensics tool for multi-step LLM pipelines.
Current status: **Complete & Demo-Ready** (FastAPI + React 19 UI + 7 deterministic checks + cross-family Judge LLM + OTel + PII redaction + RBAC auth + worker queue + evaluation flywheel).

## Quick Reference

| Command | Purpose |
|---|---|
| `./demo.sh` | One-command launcher (check + seed + FastAPI `:8000` + Vite `:5173`) |
| `make seed` | Pre-load 5 real NIM traces + 15 dataset traces into SQLite |
| `make check` | Run ruff + mypy strict + pytest (must pass) |
| `python -m forensics.cli demo` | Run all 15 docs, print summary |
| `python -m forensics.cli run <doc>` | Single document pipeline run |
| `make run-web` | Launch React trace explorer UI |
| `python -m forensics.cli eval` | Run eval cases, print table |
| `python -m forensics.cli analytics` | Print analytics JSON |
| `python -m forensics.cli benchmark-analyzer` | Run fault injection scorecard |

## Architecture Summary

**Pipeline (4 steps):** `Intake → Extraction → Classification → Summarization`
- Each step wrapped with `@traced_step` decorator
- Produces 4 spans per trace, saved to JSON (`data/traces/`) + SQLite (`data/forensics.db`)

**Analyzer (deterministic v0):** Runs 7 checks per span (`src/forensics/checks.py` & `src/forensics/analyzer_simple.py`):
1. **Entity grounding** — extracted entity value must appear in source text
2. **Label enum** — classification output must be valid `DocumentType`
3. **Date regex** — date entities must match `YYYY-MM-DD` (flexible formats accepted)
4. **Context preservation** — extraction entities must appear in summarization
5. **Propagation** — downstream steps correctly read upstream outputs
6. **Schema validity** — LLM outputs conform to expected structure
7. **Label grounding** — classification matches ground truth (when available)

**Root cause** = first span with ANY failed check.

**Judge LLM (v1):** Cross-family model (`meta/llama-3.1-8b-instruct`) with structured output for semantic analysis when deterministic checks pass or require tie-breaking.

## Key Files

```
src/forensics/
├── config.py          # Env settings, startup validation, model config
├── models.py          # All Pydantic models (Document, Span, Trace, Diagnosis, Flag, EvalCase, etc.)
├── llm.py             # NVIDIA NIM client with structured output, caching, spend caps
├── pipeline.py        # 4 pipeline steps + chain runner
├── tracing.py         # @traced_step, TraceContext, JSON writer, SQLite init
├── storage.py         # Unified Storage class (traces, flags, diagnoses, eval)
├── checks.py          # Extensible check plugin registry + 7 built-in checks
├── analyzer_simple.py # Deterministic analyzer + Judge LLM integration
├── eval_simple.py     # Flag→EvalCase flywheel, eval runner
├── api.py             # FastAPI (/runtime, /pipeline/run, /analytics/benchmark, etc.)
├── cli.py             # Typer CLI (ping, run, demo, seed, eval, analytics, benchmark-analyzer)
├── auth.py            # Multi-tenant RBAC API key verification
├── queue.py           # Atomic SQLite job queue
├── worker.py          # Background pipeline worker
├── scheduler.py       # Periodic evaluation scheduler + webhook alerts
├── otel_export.py     # OpenTelemetry span export
├── pii.py             # Pre-storage regex PII redaction
└── faults.py          # 7-fault injection harness for benchmarking
```

## Dataset

15 documents in `dataset/documents/` (3 per type):
- `contract_001-003.txt`
- `invoice_001-003.txt`
- `report_001-003.txt`
- `correspondence_001-003.txt`
- `mixed_001-003.txt`

Ground truth in `dataset/labels.json` — 5 seeded failures (`contract_001`, `invoice_003`, `report_003`, `correspondence_002`, `mixed_001`).

## Environment Variables (`.env`)

```bash
NVIDIA_API_KEY=...                    # Required for live mode (DRY_RUN=false)
PIPELINE_MODEL_ID=minimaxai/minimax-m3.1-flash
PIPELINE_MODEL_PROVIDER=minimaxai
PIPELINE_MODEL_FAMILY=minimaxai
PIPELINE_MODEL_PRICE_IN=0.20
PIPELINE_MODEL_PRICE_OUT=0.80
JUDGE_MODEL_ID=meta/llama-3.1-8b-instruct
JUDGE_MODEL_PROVIDER=meta
JUDGE_MODEL_FAMILY=meta
JUDGE_MODEL_PRICE_IN=0.30
JUDGE_MODEL_PRICE_OUT=0.90
JUDGE_FAMILY_SEPARATION_REQUIRED=true
SPEND_CAP_PER_RUN_USD=0.50
SPEND_CAP_GLOBAL_USD=20.00
CACHE_ENABLED=true
DRY_RUN=false
```

## Code Style

- `ruff` for linting/formatting (line length 100)
- `mypy` strict mode — all type annotations required
- No `Any` types without justification
- Pydantic v2 for all data models
- `pytest` for tests, `@pytest.mark.live` for API tests
