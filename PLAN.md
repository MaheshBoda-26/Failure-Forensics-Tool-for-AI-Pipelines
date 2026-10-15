# Failure Forensics Tool — 2-Day Emergency Build Plan

## Constraints
- **2 days** (today + tomorrow), ~12-14 h/day = ~26 h total
- **Solo**, $30 budget, macOS, Render free tier
- **Must ship:** working demo that proves the concept end-to-end

## Strategy: Ruthless Scope Cutting

| Original | 2-Day Version | Time Saved |
|---|---|---|
| React + React Flow (2 days) | **React + Vite + React Flow** (Day 1) | ~14 h |
| 50 documents | **15 documents** (3 per type, 1 failure each) | ~6 h |
| Full OTel + GenAI attrs | **Custom spans + OTel export** | ~4 h |
| Judge self-consistency | **Single judge call, cross-family** | ~2 h |
| Analytics dashboard | **React Analytics + JSON endpoints** | ~3 h |
| 12 ADRs | **4 critical ADRs only** | ~2 h |
| Loom + polished demo | **Live demo script** | ~2 h |
| **Total** | | **~33 h saved** |

---

## Day 1 (Today) — Core Pipeline + Tracing + Analyzer + React UI + CLI

### Morning (4 h): NIM Client + 4 Pipeline Steps
| Task | Verify |
|---|---|
| P1-T01: `NIMClient` — 3-tier structured output, caching, spend caps | Test: monkeypatch httpx → raises; test passes |
| P1-T02: **Intake** — normalize text, return `IntakeOutput` | Unit test |
| P1-T03: **Extraction** — return `ExtractionOutput` with grounded `Entity` | Unit test |
| P1-T04: **Classification** — return `ClassificationOutput` with `DocumentType` | Unit test |
| P1-T05: **Summarization** — return `SummaryOutput` with template | Unit test |
| P1-T06: **Chain runner** — stitches 4 steps, stamps versions | E2E test produces `PipelineOutput` |

### Afternoon (4 h): Tracing + React Frontend Scaffold
| Task | Verify |
|---|---|
| P2-T01: `Span` + `Trace` models with full LLM provenance | Test: creates span with all fields |
| P2-T02: `@traced_step` decorator — captures input, output, latency, tokens | Test: decorated function has span |
| P2-T03: JSON trace writer (atomic tmp+rename) + SQLite index | Test: kill mid-write → no partial file |
| P2-T04: React + Vite + TypeScript scaffold with API client | `npm run dev` serves UI |

### Evening (4 h): Dataset + 7-Check Analyzer
| Task | Verify |
|---|---|
| P1-T07: **15 documents** in `dataset/documents/` (3 contract, 3 invoice, 3 report, 3 correspondence, 3 mixed) + `labels.json` with 5 seeded failures (1 per type + 1 mixed) | `python -m forensics.cli validate-dataset` passes |
| P3-T01: **7 deterministic checks** — entity grounding, label enum, date regex, context preservation, propagation, schema validity, label grounding | Test: each check has pass/fail fixture |
| P3-T02: **Simple root cause** — run checks per span; first span with ANY failed check = root cause | Test: fault-injected trace blames correct step |
| P3-T03: **DRY_RUN mode** — document-aware canned responses for all 5 seeded failures | `python -m forensics.cli demo` works offline |

---

## Day 2 (Tomorrow) — React UI + Judge LLM + OTel + Deploy

### Morning (4 h): React Trace Explorer
| Task | Verify |
|---|---|
| P4-T01: `TraceDetailView` — spine + detail panes, evidence chain | Runs: `make run-web` |
| P4-T02: **DAGGraphView** — dynamic execution graph from trace spans | Screenshot: green/red/tainted nodes |
| P4-T03: **TokenDiffView** — expected vs actual classification/entity grounding | Screenshot |
| P4-T04: **Flag/Confirm/Override** — writes Flag, triggers analyzer, shows diagnosis | Click → diagnosis appears |

### Afternoon (4 h): Judge LLM + OTel + Eval + Analytics
| Task | Verify |
|---|---|
| P5-T01: **Judge LLM** — cross-family, structured output via `NIMClient.structured()` | `JUDGE_FAMILY_SEPARATION_REQUIRED=true` |
| P5-T02: **OTel export** — `otel_export.py` spans to OTLP/HTTP | `OTEL_EXPORTER=otlp` works |
| P5-T03: **Flag → EvalCase** — auto-create on confirm | Test: flag + confirm → case in DB |
| P5-T04: **Eval runner** — re-run all cases, print pass/fail table | `python -m forensics.cli eval` |
| P5-T05: **Analytics JSON** — `/analytics` with failure_rate_over_time | `curl /analytics` returns JSON |
| P5-T06: **Fault injection** — `faults.py` 7 faults + `benchmark-analyzer` CLI | `python -m forensics.cli benchmark-analyzer` |

### Evening (2 h): Render Deploy + Final Test
| Task | Verify |
|---|---|
| Render: push to GitHub, connect Render, add persistent disk | Live URL works |
| End-to-end smoke test on live | `ping` → `run` → `flag` → `diagnose` → `confirm` |

---

## Minimal File Changes (Only These)

### New Files
- `src/forensics/llm.py` — `NIMClient` + 3-tier structured output
- `src/forensics/pipeline.py` — 4 steps + chain
- `src/forensics/tracing.py` — `@traced_step`, JSON writer, SQLite
- `src/forensics/analyzer_simple.py` — 7 deterministic checks
- `src/forensics/eval_simple.py` — flag→case, runner
- `src/forensics/api.py` — FastAPI + `/analytics`, `/eval`
- `src/forensics/otel_export.py` — OTel export
- `src/forensics/pii.py` — PII redaction
- `src/forensics/faults.py` — Fault injection
- `frontend/` — React + Vite + TypeScript trace explorer
- `dataset/documents/*.txt` (15 files) + `dataset/labels.json`
- `docs/adr/0001-custom-chain.md`, `0002-llm-client.md`, `0004-storage.md`, `0007-judge-model.md` (only 4)

### Modified
- `src/forensics/cli.py` — add `demo`, `validate-dataset`, `eval`, `analytics`, `benchmark-analyzer`
- `pyproject.toml` — add `nvidia-nim`, `opentelemetry`, `pii-redaction`
- `package.json` — React + Vite + React Flow + Lucide

### Deleted/Deferred
- `fake_llm.py` — replaced by `llm.py` dry_run mode
- 8 ADRs, Loom script

---

## Critical Path (Sequential, No Parallel)

```
nim_client → pipeline steps → chain → tracing → dataset → analyzer → react_ui → judge/otel → flag/eval → deploy
```

**Zero slack.** If any step slips, cut the next lowest priority:
1. Cut: eval runner → just flag/diagnose
2. Cut: diff view → just show raw spans
3. Cut: React → CLI-only demo
4. Cut: Render → local demo only

---

## Daily Commands

### Day 1 End Checkpoint
```bash
make check                          # All green
python -m forensics.cli run dataset/documents/contract_001.txt  # Works
python -m forensics.cli validate-dataset                          # Passes
ls data/traces/*.json | wc -l   # 15 trace files
make run-web                      # React dev server opens
```

### Day 2 End Checkpoint
```bash
make run-web                      # Opens React UI
python -m forensics.cli demo      # Runs all 15, prints summary
curl localhost:8000/analytics     # JSON with 4 metrics + failure_rate_over_time
python -m forensics.cli benchmark-analyzer  # Fault injection scorecard
git push && render deploy         # Live
```

---

## What Makes This a Credible Portfolio Piece (Even Cut)

1. **Typed pipeline** — 4 steps, Pydantic contracts, versioned
2. **Tracing** — every span captured with input/output/latency + LLM provenance
3. **Deterministic analyzer** — 7 checks find root cause without judge
4. **Judge LLM** — cross-family, structured output for semantic analysis
5. **Feedback loop** — flag → diagnosis → eval case → regression
6. **React UI** — DAG graph, token diff, evidence chain, flag/confirm/override
7. **Fault injection** — 7 faults, scorecard table
8. **Honest limitations doc** — "OTel export implemented, PII redaction in progress"

---

## Start Now

```bash
# 1. Create NIMClient
# 2. Build 4 pipeline steps
# 3. Chain them
# 4. Add @traced_step
# 5. Write 15 documents
```

**First task:** `src/forensics/llm.py`
