# Failure Forensics Tool — 2-Day Emergency Build Plan

## Constraints
- **2 days** (today + tomorrow), ~12-14 h/day = ~26 h total
- **Solo**, $30 budget, macOS, Render free tier
- **Must ship:** working demo that proves the concept end-to-end

## Strategy: Ruthless Scope Cutting

| Original | 2-Day Version | Time Saved |
|---|---|---|
| React + React Flow (2 days) | **Streamlit** (4 hours) | ~14 h |
| 50 documents | **15 documents** (3 per type, 1 failure each) | ~6 h |
| Full OTel + GenAI attrs | **Custom spans only** (no OTel) | ~4 h |
| Judge self-consistency | **Single judge call** | ~2 h |
| Analytics dashboard | **JSON endpoints only** | ~3 h |
| 12 ADRs | **4 critical ADRs only** | ~2 h |
| Loom + polished demo | **Live demo script** | ~2 h |
| **Total** | | **~33 h saved** |

---

## Day 1 (Today) — Core Pipeline + Tracing + Minimal Analyzer

### Morning (4 h): Fake LLM + 4 Pipeline Steps
| Task | Verify |
|---|---|
| P1-T01: `FakeLLMClient` — returns typed canned responses, record/replay to JSON | Test: monkeypatch httpx → raises; test passes |
| P1-T02: **Intake** — normalize text, return `IntakeOutput` | Unit test |
| P1-T03: **Extraction** — return `ExtractionOutput` with 2-3 hardcoded `Entity` | Unit test |
| P1-T04: **Classification** — return `ClassificationOutput` with `DocumentType` | Unit test |
| P1-T05: **Summarization** — return `SummaryOutput` with template | Unit test |
| P1-T06: **Chain runner** — stitches 4 steps, stamps versions | E2E test produces `PipelineOutput` |

### Afternoon (4 h): Tracing (Custom, No OTel)
| Task | Verify |
|---|---|
| P2-T01: `Span` dataclass + `Trace` container (reuse models) | Test: creates span with all fields |
| P2-T02: `@traced_step` decorator — captures input, output, latency, tokens (fake) | Test: decorated function has span |
| P2-T03: JSON trace writer (atomic tmp+rename) | Test: kill mid-write → no partial file |
| P2-T04: SQLite index — `traces` + `spans` tables only | Test: row inserted per trace |

### Evening (4 h): Dataset + Minimal Analyzer
| Task | Verify |
|---|---|
| P1-T07: **15 documents** in `dataset/documents/` (3 contract, 3 invoice, 3 report, 3 correspondence, 3 mixed) + `labels.json` with 5 seeded failures (1 per type + 1 mixed) | `python -m forensics.cli validate-dataset` passes |
| P3-T01: **Deterministic checks only** — entity grounding (substring), label in enum, date regex | Test: each check has pass/fail fixture |
| P3-T02: **Simple root cause** — run checks per span; first span with ANY failed check = root cause | Test: fault-injected trace blames correct step |
| P3-T03: **Skip judge entirely** — deterministic only for 2-day version | Document as known limitation |

---

## Day 2 (Tomorrow) — Streamlit UI + Feedback Loop + Demo Polish

### Morning (4 h): Streamlit Trace Explorer
| Task | Verify |
|---|---|
| P4-T01: `streamlit_app.py` — trace list (table) + detail (expander) | Runs: `streamlit run streamlit_app.py` |
| P4-T03: **Color-coded spans** — green (all checks pass), yellow (low confidence), red (failed check) | Screenshot: seeded failure shows red |
| P4-T04: **Diff view** — input vs output vs expected (from labels) side-by-side | Screenshot |
| P4-T05: **Flag button** — writes `Flag` to SQLite, triggers analyzer, shows diagnosis | Click → diagnosis appears |

### Afternoon (4 h): Feedback Loop + Eval + Polish
| Task | Verify |
|---|---|
| P5-T01: **Flag → EvalCase** — auto-create from confirmed flag | Test: flag + confirm → case in DB |
| P5-T02: **Eval runner** — re-run all cases, print pass/fail | `python -m forensics.cli eval` outputs table |
| P5-T05: **Analytics JSON** — `/analytics` endpoint with 4 metrics | `curl /analytics` returns JSON |
| P6-T02: **15-doc demo run** — script that runs all, prints summary | `python -m forensics.cli demo` |
| P6-T03: **README + CLAUDE.md** — minimal but complete | `make check` green |

### Evening (2 h): Render Deploy + Final Test
| Task | Verify |
|---|---|
| Render: push to GitHub, connect Render, add persistent disk | Live URL works |
| End-to-end smoke test on live | `ping` → `run` → `flag` → `diagnose` → `confirm` |

---

## Minimal File Changes (Only These)

### New Files
- `src/forensics/fake_llm.py` — `FakeLLMClient` + replay
- `src/forensics/pipeline.py` — 4 steps + chain
- `src/forensics/tracing_simple.py` — `@traced_step`, JSON writer, SQLite
- `src/forensics/analyzer_simple.py` — deterministic checks only
- `src/forensics/eval_simple.py` — flag→case, runner
- `streamlit_app.py` — UI
- `dataset/documents/*.txt` (15 files) + `dataset/labels.json`
- `docs/adr/0001-custom-chain.md`, `0002-llm-client.md`, `0004-storage.md`, `0007-judge-model.md` (only 4)

### Modified
- `src/forensics/cli.py` — add `demo`, `validate-dataset`
- `pyproject.toml` — add `streamlit`, `sqlite-utils`

### Deleted/Deferred
- OTel, OTel exporter, OTel GenAI attrs
- React frontend, React Flow, Vite, TS
- Judge LLM calls, prompt templates, self-consistency
- Analytics dashboard UI, scheduled evals
- 8 ADRs, Loom script

---

## Critical Path (Sequential, No Parallel)

```
fake_llm → pipeline steps → chain → tracing → dataset → analyzer → streamlit → flag/eval → deploy
```

**Zero slack.** If any step slips, cut the next lowest priority:
1. Cut: eval runner → just flag/diagnose
2. Cut: diff view → just show raw spans
3. Cut: Streamlit → CLI-only demo
4. Cut: Render → local demo only

---

## Daily Commands

### Day 1 End Checkpoint
```bash
make check                          # All green
python -m forensics.cli run dataset/documents/contract_001.txt  # Works
python -m forensics.cli validate-dataset                          # Passes
ls data/traces/*.json | wc -l   # 15 trace files
```

### Day 2 End Checkpoint
```bash
streamlit run streamlit_app.py &  # Opens UI
python -m forensics.cli demo      # Runs all 15, prints summary
curl localhost:8000/analytics     # JSON with 4 metrics
git push && render deploy         # Live
```

---

## What Makes This a Credible Portfolio Piece (Even Cut)

1. **Typed pipeline** — 4 steps, Pydantic contracts, versioned
2. **Tracing** — every span captured with input/output/latency
3. **Deterministic analyzer** — finds root cause without judge
4. **Feedback loop** — flag → diagnosis → eval case → regression
5. **Live demo** — 15 docs, 5 failures, UI shows red span, diff, confirm
6. **Honest limitations doc** — "Judge LLM deferred; deterministic only v0"

---

## Start Now

```bash
# 1. Create FakeLLMClient
# 2. Build 4 pipeline steps
# 3. Chain them
# 4. Add @traced_step
# 5. Write 15 documents
```

**First task:** `src/forensics/fake_llm.py`
