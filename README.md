# Failure Forensics Tool for AI Pipelines

Trace every step of a multi-step LLM pipeline, localize the exact origin of a
bad output, and feed human-confirmed failures back into a growing eval dataset.

## Status: Day 1 Complete — Core Pipeline + Tracing + Analyzer + CLI + React UI

✅ **Day 1 (Core Pipeline + React UI)** — All implemented:
- `llm.py` — NVIDIA NIM client with 3-tier structured output, caching, spend caps
- `pipeline.py` — 4 steps: intake → extraction → classification → summarization
- `tracing.py` — `@traced_step` decorator, 4 spans per run, atomic JSON + SQLite
- `storage.py` — JSON payloads + SQLite index (traces, spans, flags, diagnoses, eval)
- `analyzer_simple.py` — 7 deterministic checks (entity grounding, label enum, date regex, context preservation, propagation, schema validity, label grounding)
- 15 documents (3 per type) + `labels.json` with 5 seeded failures
- CLI: `run`, `validate-dataset`, `demo`, `eval`, `analytics`
- **React Frontend** — Trace explorer with DAG graph, token diff, evidence chain, flag/confirm/override
- **FastAPI** — REST API with `/analytics`, `/eval` endpoints

🚧 **Day 2 (OTel + Judge LLM + PII + Deploy)** — In progress:
- OTel export (`otel_export.py`)
- PII redaction (`pii.py`)
- Judge LLM support (cross-family, structured output)
- Fault injection (`faults.py`)
- Deploy to Render (pending)

## Requirements

- Python 3.12 (pinned in `pyproject.toml`; `uv` manages the venv)
- Node.js 18+ (for React frontend)
- An NVIDIA NIM API key in `.env` (copy `.env.example`) — *not required for demo mode*

## Quickstart

```bash
make install        # create .venv (Python 3.12) and install deps
cd frontend && npm install  # install React frontend deps
cp .env.example .env
$EDITOR .env        # set NVIDIA_API_KEY and model slugs (or use dummy for demo)
make check          # ruff + mypy strict + pytest
```

## Demo (No API Key Required)

```bash
# Run all 15 documents through pipeline with tracing
python -m forensics.cli demo

# Validate dataset
python -m forensics.cli validate-dataset

# Run single document
python -m forensics.cli run dataset/documents/contract_001.txt

# Show analytics
python -m forensics.cli analytics

# Run React UI (development)
make run-web

# Start Streamlit UI (legacy)
streamlit run streamlit_app.py
```

## Layout

| Path | Purpose |
|---|---|
| `src/forensics/config.py` | Settings from env + fail-fast startup validation |
| `src/forensics/models.py` | Pydantic domain models (Document, Span, Trace, Diagnosis, …) |
| `src/forensics/llm.py` | NVIDIA NIM client with structured output, caching, spend caps |
| `src/forensics/pipeline.py` | 4 steps: intake, extraction, classification, summarization |
| `src/forensics/tracing.py` | `@traced_step` decorator, span lifecycle, JSON + SQLite writer |
| `src/forensics/storage.py` | JSON payloads + SQLite index and relational tables |
| `src/forensics/analyzer_simple.py` | 7 deterministic checks + root cause finder |
| `src/forensics/eval_simple.py` | Eval engine: flag → EvalCase → runner |
| `src/forensics/api.py` | FastAPI REST API + `/analytics` endpoint |
| `src/forensics/cli.py` | Command line entry point |
| `src/forensics/otel_export.py` | OpenTelemetry export |
| `src/forensics/pii.py` | PII redaction |
| `src/forensics/faults.py` | Fault injection for testing |
| `streamlit_app.py` | Streamlit trace explorer UI |
| `frontend/` | React + Vite + TypeScript trace explorer |
| `dataset/` | 15 demo documents + `labels.json` ground truth |
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
forensics ping              # Test config and model info
forensics run <doc>         # Run pipeline on single document
forensics validate-dataset  # Check all 15 docs exist and are readable
forensics demo              # Run all 15 docs, print classification summary
forensics eval              # Run eval cases, print pass/fail table
forensics analytics         # Print analytics JSON
forensics validate-config   # Validate config without running
forensics benchmark-analyzer # Run fault injection scorecard
```

## React Frontend

```bash
make run-web  # Starts React dev server (Vite) + FastAPI backend
```

Features:
- **Trace list** — Table with trace ID, document, status, spans, latency
- **Trace detail** — Color-coded spans (🟢 pass, 🟡 tainted, 🔴 fail)
- **DAG Graph** — Dynamic execution graph built from trace spans
- **Evidence Chain** — Failed checks with per-step scores
- **Token Diff** — Expected vs actual classification/entity grounding
- **Flag/Confirm/Override** — Creates Flag in SQLite, triggers analyzer, shows diagnosis

## Architecture (v0 - Deterministic + Judge LLM)

```
Document → [Intake] → [Extraction] → [Classification] → [Summarization]
                ↓            ↓                ↓                  ↓
            @traced_step  @traced_step    @traced_step       @traced_step
                ↓            ↓                ↓                  ↓
            Span JSON + SQLite (4 spans per trace)
                ↓
        analyze_trace() — 7 deterministic checks:
            • Entity grounding: value must appear in source text
            • Label enum: classification must be valid DocumentType
            • Date regex: date entities must match YYYY-MM-DD (flexible formats)
            • Context preservation: extraction entities in summarization
            • Propagation: downstream steps correctly read upstream outputs
            • Schema validity: LLM outputs conform to expected structure
            • Label grounding: classification matches ground truth (when available)
        → First span with ANY failed check = root cause
        → Judge LLM (optional): semantic analysis, cross-family, structured output
```

## Seeded Failures (Ground Truth)

| Document | Type | Seeded Failure | Step | Category |
|---|---|---|---|---|
| contract_001 | contract | ✅ | classification | misclassification |
| invoice_001 | invoice | ✅ | extraction | extraction_hallucination |
| report_001 | report | ✅ | summarization | prompt_failure |
| correspondence_001 | correspondence | ✅ | summarization | propagation_error |
| mixed_001 | mixed | ✅ | classification | misclassification |

*Note: DRY_RUN=true returns document-aware canned responses, so actual failures match seeded. Seeded failures are ground truth for eval.*

## Rules

- **Never call the live API in a unit test.** Use record/replay; live tests marked `@pytest.mark.live`.
- **No secrets in git.** `.env` is ignored; `detect-secrets` runs in pre-commit.
- **No invented model names or prices.** Model slugs/prices from `.env`; marked `VERIFY BEFORE USE`.
- **Judge model from different provider family** than pipeline models (config asserts this).
- **No real PII in dataset.** All documents are synthetic.

## Planning

`PLAN.md` is the 2-day emergency build plan. `PROMPT.md` is the master planning prompt.

## License

MIT
