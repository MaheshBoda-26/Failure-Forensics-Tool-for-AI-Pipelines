# Failure Forensics Tool for AI Pipelines

Trace every step of a multi-step LLM pipeline, localize the exact origin of a
bad output, and feed human-confirmed failures back into a growing eval dataset.

## Status: Phase 0 scaffold

Repo structure and toolchain are in place. **No module is implemented yet** —
every file in `src/forensics/` carries a docstring stating its purpose and a
"not implemented" marker. See `docs/README.md` for why the plan is not written
yet.

## Requirements

- Python 3.12 (pinned in `pyproject.toml`; `uv` manages the venv)
- Node 22 — only for Phase 4
- An OpenRouter API key in `.env` (copy `.env.example`)

## Quickstart

```bash
make install        # create .venv (Python 3.12) and install deps
cp .env.example .env
$EDITOR .env        # set OPENROUTER_API_KEY and the model slugs
make check          # ruff + mypy strict + pytest
```

## Layout

| Path | Purpose |
|---|---|
| `src/forensics/config.py` | Settings from env + fail-fast startup validation |
| `src/forensics/models.py` | Pydantic domain models (Document, Span, Trace, Diagnosis, …) |
| `src/forensics/llm.py` | OpenRouter client — the single LLM entry point |
| `src/forensics/pipeline.py` | The four steps: intake, extraction, classification, summary |
| `src/forensics/tracing.py` | Span lifecycle + `@traced_step` decorator |
| `src/forensics/storage.py` | JSON payloads + SQLite index and relational tables |
| `src/forensics/taxonomy.py` | Failure categories, detectors, separation rules |
| `src/forensics/analyzer.py` | Backward trace analyzer, root-cause localization |
| `src/forensics/faults.py` | Deterministic fault injection for ground truth |
| `src/forensics/api.py` | FastAPI REST API + UI host |
| `src/forensics/eval.py` | Eval and regression engine |
| `src/forensics/analytics.py` | Dashboard aggregations |
| `src/forensics/cli.py` | Command line entry point |
| `dataset/` | The 50-document demo dataset + `labels.json` ground truth |
| `data/` | Runtime data, gitignored (SQLite + per-trace JSON) |
| `frontend/` | React trace explorer — Phase 4, not scaffolded yet |

## Commands

`make help` lists everything. The ones that matter:

```bash
make check      # lint + typecheck + test
make test       # pytest, live API tests deselected
make test-live  # includes real OpenRouter calls — costs money
make dev        # FastAPI with reload
```

## Rules

- **Never call the live API in a unit test.** Use record/replay; live tests are
  marked `@pytest.mark.live`.
- **No secrets in git.** `.env` is ignored; `detect-secrets` runs in pre-commit.
- **No invented model names or prices.** Model slugs and prices come from
  `.env` and every one is marked `VERIFY BEFORE USE` — OpenRouter slugs and
  pricing change without notice.
- **The judge model must come from a different provider family** than the
  pipeline models. Self-preference bias is a known weakness; config asserts it.
- **No real PII in the dataset.** Every document is forwarded to a third-party
  upstream provider chosen by OpenRouter routing.

## Planning

`PROMPT.md` is the master planning prompt. Fill in its `<builder_context>`
block, then generate the plan and save it as `docs/PLAN.md`.
