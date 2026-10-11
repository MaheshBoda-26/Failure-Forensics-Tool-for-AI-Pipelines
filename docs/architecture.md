# Failure Forensics Tool — Architecture Document

## Overview

The Failure Forensics Tool is a deterministic observability and root-cause analysis system for multi-step LLM pipelines. It captures every pipeline step's input/output, runs deterministic checks to localize failures, and provides a feedback loop for continuous improvement.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FAILURE FORENSICS TOOL                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────┐ │
│  │   CLI/API    │───▶│   Pipeline   │───▶│   Tracing    │───▶│ Storage  │ │
│  │  (Typer)     │    │  (4 Steps)   │    │  (@traced)   │    │ (JSON+DB)│ │
│  └──────────────┘    └──────────────┘    └──────────────┘    └──────────┘ │
│         │                   │                   │                │        │
│         │                   ▼                   ▼                │        │
│         │            ┌──────────────┐    ┌──────────────┐       │        │
│         │            │  LLM Client  │    │  Analyzer    │       │        │
│         │            │ (OpenRouter) │    │ (Deterministic)       │        │
│         │            └──────────────┘    └──────────────┘       │        │
│         │                   │                   │                │        │
│         ▼                   ▼                   ▼                ▼        │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │                     STREAMLIT UI (Trace Explorer)                    │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │ │
│  │  │Trace List│ │Pipeline  │ │Span Detail│ │Diff View │ │Flag/Flag │  │ │
│  │  │  + Filter│ │Graph     │ │+Prompt/IO │ │+Expected │ │Override  │  │ │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Pipeline (`src/forensics/pipeline.py`)
Four deterministic/async steps:
- **Intake**: Document normalization (whitespace, encoding detection) — no LLM call
- **Extraction**: Entity extraction via LLM (structured output)
- **Classification**: Document type classification via LLM
- **Summarization**: Summary generation via LLM

Each step decorated with `@traced_step("step_name")` for automatic span capture.

### 2. LLM Client (`src/forensics/llm.py`)
Production-grade OpenRouter client with:
- **Tiered structured output**: Native → Schema-in-prompt → Repair
- **Retry logic**: Exponential backoff on transport/429/5xx; fail-fast on 401/403/404
- **Caching**: File-based response cache keyed by (model, provider, prompt, prompt_version)
- **Spend tracking**: Per-run and global caps with SQLite persistence
- **Cost accounting**: Config-driven per-model pricing

### 3. Tracing (`src/forensics/tracing.py`)
- `@traced_step` decorator captures: input, output, prompt, raw_response, tokens, latency, status
- ContextVar-based span telemetry side-channel for LLM provenance
- Single write path: `finalize_trace()` derives status, aggregates tokens/cost, writes JSON + SQLite
- OTel export: `ForensicsSpanProcessor` mirrors spans to OTel with GenAI semantic conventions

### 4. Analyzer (`src/forensics/analyzer_simple.py`)
Deterministic backward analysis (no judge LLM):
- **6 checks per span**: entity_grounding, label_enum, date_regex, context_preservation, propagation, prompt_schema
- Root cause = first span with ANY failed check
- 5 failure categories: extraction_hallucination, misclassification, propagation_error, prompt_failure, context_loss
- Evidence chain: human-readable citations of real input/output

### 5. Storage (`src/forensics/storage.py`)
- JSON files: full span payloads (git-friendly, inspectable)
- SQLite: indexes, flags, diagnoses, eval cases/runs/results
- Single write path: `finalize_trace()` ensures consistency

### 6. Eval Loop (`src/forensics/eval_simple.py`)
- `Flag` → confirmed `Diagnosis` → `EvalCase` (bad_output + corrected_output)
- `run_eval()` re-runs pipeline on eval cases, tracks fixed/still-failing
- Trend tracking: `EvalRun` with pass/fail counts over time

### 7. UI (`streamlit_app.py`)
- Trace list with filters
- Pipeline graph (Plotly + NetworkX, color-coded by status)
- Span detail: input/output/prompt/raw_response/tokens/checks/evidence
- Diff view: expected vs actual vs grounded truth
- Flagging with confirm/override

### 8. API (`src/forensics/api.py`)
FastAPI endpoints: run pipeline, list/get traces, flag, diagnose, confirm/override diagnosis, eval cases/runs, analytics

## Data Flow

```
User Document
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PIPELINE (4 async steps)                                    │
│ 1. Intake → cleaned_text, char_count, encoding              │
│ 2. Extraction → entities[] (value, type, offsets)           │
│ 3. Classification → document_type, confidence               │
│ 4. Summarization → summary, key_points[]                    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼ (each step @traced_step)
┌─────────────────────────────────────────────────────────────┐
│ TRACING                                                     │
│ Span: input, output, prompt, raw_response,                  │
│       tokens, latency, status, error                        │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ ANALYZER (deterministic, backward)                          │
│ Checks: entity_grounding, label_enum, date_regex,           │
│         context_preservation, propagation, prompt_schema    │
│ Root cause = first failing step                             │
│ Category: hallucination/misclassification/propagation/      │
│         prompt_failure/context_loss                         │
│ Evidence chain with real I/O citations                      │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ STORAGE                                                      │
│ JSON: full span payloads                                    │
│ SQLite: index + flags + diagnoses + eval cases/runs/results │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ FEEDBACK LOOP                                               │
│ Flag → Diagnosis → Confirm/Override → EvalCase → EvalRun    │
│ Trend: pass/fail over time, failure type frequency          │
└─────────────────────────────────────────────────────────────┘
```

## Technology Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12+ |
| Pipeline | Async/await, Pydantic v2 |
| LLM Client | httpx (async), OpenRouter API |
| Tracing | ContextVar, custom spans |
| Analyzer | Pure Python, json, re, difflib |
| Storage | JSON files + SQLite (WAL mode) |
| API | FastAPI, Uvicorn |
| UI | Streamlit, Plotly, NetworkX |
| CLI | Typer |
| LLM | OpenRouter (any model) |
| Testing | pytest, httpx.MockTransport |
| Lint/Type | Ruff, MyPy (strict) |

## Configuration

Environment variables (`.env`):
- `OPENROUTER_API_KEY` — required for live mode
- `PIPELINE_MODEL_ID`, `PIPELINE_MODEL_PROVIDER`, etc. — model routing
- `SPEND_CAP_PER_RUN_USD`, `SPEND_CAP_GLOBAL_USD` — cost caps
- `CACHE_ENABLED`, `DRY_RUN` — behavior toggles

## Security Considerations

- API key only in environment, never logged
- Input sanitization: document text fenced in prompts
- No `eval()` on LLM output (uses `json.loads`)
- Spend caps enforced before and after each request
- SQLite WAL mode for concurrent safety

## Deployment

Local:
```bash
make check          # lint + typecheck + tests
streamlit run streamlit_app.py  # UI
python -m forensics.cli demo    # CLI demo
```

Render (free tier):
- Web service: `uvicorn forensics.api:app`
- Background worker: `python -m forensics.cli eval-run` (cron)
- Persistent disk: `/data` for SQLite + JSON traces

## Limitations (v0)

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| No judge LLM | Can't catch semantic failures | Deterministic checks cover structural failures |
| No propagation analysis | Global fidelity = local fidelity | Explicit propagation check for downstream misreads |
| Fake LLM in tests | Dry-run only validates structure | Live mode untested; requires API key |
| No React frontend | Streamlit only | Sufficient for demo; React deferred |
| No OTel collector | Custom OTel export only | Stdout exporter works for demo |
| 15 documents only | Small demo dataset | Seed failures cover 5 categories |
| No live LLM in CI | Tests use MockTransport | Real LLM validated manually |

## Future Work (v1+)

- Judge LLM for semantic quality scoring
- Propagation analysis across steps
- React + React Flow frontend
- Scheduled eval runs + alerting
- Multi-tenant support
- Custom check plugins
- Distributed tracing (full OTel)

## File Structure

```
src/forensics/
├── analyzer_simple.py    # Deterministic backward analyzer
├── api.py                # FastAPI REST endpoints
├── analyzer_simple.py    # Deterministic checks
├── cli.py                # Typer CLI commands
├── config.py             # Settings + validation
├── eval_simple.py        # Eval loop: flag → case → run → trend
├── llm.py                # OpenRouter client (tiers, retry, cache, spend)
├── models.py             # All Pydantic models
├── pipeline.py           # 4-step pipeline + @traced_step
├── storage.py            # JSON + SQLite storage
├── tracing.py            # @traced_step, spans, OTel export
tests/
├── test_analyzer_phase3.py   # 33 analyzer tests
├── test_llm.py               # 14 LLM client tests
├── test_otel_export.py       # 10 OTel export tests
├── test_tracing_phase2.py    # 17 tracing tests
streamlit_app.py             # Streamlit trace explorer
Makefile                     # check, test, demo targets
```

---

*Generated as part of Phase 6 portfolio polish. This document reflects the system as implemented through Phases 0–5.*
