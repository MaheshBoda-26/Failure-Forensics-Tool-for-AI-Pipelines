# CLAUDE.md — Project Context for AI Assistants

## Project: Failure Forensics Tool

A 2-day emergency build of a failure forensics tool for multi-step LLM pipelines.
Current status: **Day 1 complete, Day 2 in progress**.

## Quick Reference

| Command | Purpose |
|---|---|
| `make check` | Run ruff + mypy strict + pytest (must pass) |
| `python -m forensics.cli demo` | Run all 15 docs, print summary |
| `python -m forensics.cli run <doc>` | Single document pipeline run |
| `streamlit run streamlit_app.py` | Launch trace explorer UI |
| `python -m forensics.cli eval` | Run eval cases, print table |
| `python -m forensics.cli analytics` | Print analytics JSON |

## Architecture Summary

**Pipeline (4 steps):** `Intake → Extraction → Classification → Summarization`
- Each step wrapped with `@traced_step` decorator
- Produces 4 spans per trace, saved to JSON (`data/traces/`) + SQLite (`data/forensics.db`)

**Analyzer (deterministic v0):** Runs 3 checks per span:
1. **Entity grounding** — extracted entity value must appear in source text
2. **Label enum** — classification output must be valid `DocumentType`
3. **Date regex** — date entities must match `YYYY-MM-DD`

**Root cause** = first span with ANY failed check.

**No judge LLM** — deferred to v1. This is intentional for 2-day scope.

## Key Files

```
src/forensics/
├── config.py          # Env settings, startup validation, model config
├── models.py          # All Pydantic models (Document, Span, Trace, Diagnosis, Flag, EvalCase, etc.)
├── fake_llm.py        # Deterministic fake LLM with canned responses + record/replay
├── pipeline.py        # 4 pipeline steps + chain runner
├── tracing.py         # @traced_step, TraceContext, JSON writer, SQLite init
├── storage.py         # Unified Storage class (traces, flags, diagnoses, eval)
├── analyzer_simple.py # Deterministic checks + root cause finder
├── eval_simple.py     # Flag→EvalCase, eval runner
├── api.py             # FastAPI + /analytics endpoint
├── cli.py             # Typer CLI (ping, run, demo, validate-dataset, eval, analytics)
```

## Dataset

15 documents in `dataset/documents/` (3 per type):
- `contract_001-003.txt`
- `invoice_001-003.txt`
- `report_001-003.txt`
- `correspondence_001-003.txt`
- `mixed_001-003.txt`

Ground truth in `dataset/labels.json` — 5 seeded failures (1 per type + 1 mixed).

## Common Tasks

### Add a New Pipeline Step
1. Add output model to `models.py`
2. Add step method to `Pipeline` class in `pipeline.py`
3. Add `@traced_step("step_name")` decorator
4. Add deterministic check to `analyzer_simple.py` if needed
5. Update `run()` method to call new step

### Run Tests
```bash
make test           # Unit tests only
make test-live      # With real NIM calls (uses hosted quota)
```

### Debug a Trace
```python
from forensics.storage import Storage
from forensics.analyzer_simple import analyze_trace
from pathlib import Path

storage = Storage()
trace = storage.get_trace("trace_id_here")
doc_text = Path("dataset/documents/doc_id.txt").read_text()
diagnosis = analyze_trace(trace, doc_text)
print(diagnosis.root_cause_step, diagnosis.category, diagnosis.evidence_chain)
```

## Known Limitations (v0)

1. **Fake LLM** — Returns same canned responses for all docs; extraction entities don't match real docs
2. **No judge LLM** — Analyzer is deterministic only; can't catch subtle semantic failures
3. **No propagation analysis** — Global fidelity = local fidelity in v0
4. **No React frontend** — Streamlit only (React deferred)
5. **No OTel** — Custom tracing only

## Environment Variables (`.env`)

```bash
OPENROUTER_API_KEY=sk-or-...          # Required for live mode
PIPELINE_MODEL_ID=openai/gpt-4o-mini
PIPELINE_MODEL_PROVIDER=openai
PIPELINE_MODEL_FAMILY=openai
PIPELINE_MODEL_PRICE_IN=0.0
PIPELINE_MODEL_PRICE_OUT=0.0
# ... similar for fallback, judge, cheap models
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

## When Making Changes

1. Run `make check` before committing
2. Keep changes surgical — only touch what's needed
3. Match existing patterns in the codebase
4. Add type annotations for all new functions
5. Write tests for new deterministic checks
