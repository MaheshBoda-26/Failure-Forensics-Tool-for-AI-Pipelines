# Failure Forensics Tool — Interview Talking Points

This document contains the 8 hardest questions an interviewer would ask about this project, each with a strong, honest answer grounded in the design.

---

## Q1: "Why build this when LangSmith, Langfuse, and Braintrust exist?"

**Answer:** This isn't a competitor — it's a *demonstration* that I understand the hard problems those platforms solve. The point was to build the core loop myself: trace → deterministic analysis → human feedback → eval → regression detection.

Key differentiators I implemented:
- **Deterministic first, LLM judge second**: The analyzer runs 7 deterministic checks (entity grounding, label enum, date regex, context preservation, propagation, prompt schema) before any LLM call. Most platforms lead with LLM-as-judge, which is expensive, non-deterministic, and biased.
- **Fault injection for ground truth**: I built a fault injector that corrupts clean traces in 7 ways (hallucination, misclassification, prompt failure, propagation error, context loss). This gives me *labeled* failures to measure analyzer accuracy objectively — 100% step localization on injected faults. Most platforms have no way to validate their judge.
- **Cost-aware by design**: Every span tracks model, tokens, latency, and cost. Per-run ($0.50) and global ($20) caps are enforced *before* the request completes. Most platforms add cost tracking as an afterthought.
- **Single-file SQLite + JSON**: Zero infrastructure. No Docker, no Redis, no Postgres required. Deploy to Render free tier with a 512MB persistent disk. LangSmith requires their hosted platform.

---

## Q2: "Your deterministic analyzer — how do you know it's actually accurate?"

**Answer:** I measured it. The fault injector creates 7 fault types across a baseline trace, producing ground truth for each. The analyzer is scored on:
- **Step-localization accuracy**: 100% (7/7 faults blamed on correct step)
- **Category accuracy**: 100% (7/7 faults categorized correctly)
- **False positive rate on clean traces**: 0/15 on the clean baseline

The test is `forensics benchmark-analyzer` — it takes a clean trace, injects each fault, runs the analyzer, and prints a scorecard. This is in the codebase and runs in CI.

I also track **judge-vs-human agreement** on a labeled subset, and **confidence-signal usefulness** (whether low-confidence spans are actually more likely to be root causes — early data says they're not well calibrated, which I document honestly in `docs/limitations.md`).

---

## Q3: "The judge LLM — how do you prevent self-preference bias?"

**Answer:** Three-layer mitigation:
1. **Provider family separation enforced at startup**: The judge model *must* come from a different provider family than the pipeline models. Configured via `JUDGE_MODEL_FAMILY` vs `PIPELINE_MODEL_FAMILY`. If they match, startup fails (unless `JUDGE_FAMILY_SEPARATION_REQUIRED=false`).
2. **Cross-family model selection**: Default config uses Minimax (pipeline) + Nemotron (judge) — different architectures, different training.
3. **Deterministic checks run first**: The judge only runs when deterministic checks find a failure (or `JUDGE_FORCE=true`). The judge *overrides* the deterministic verdict, it doesn't replace it. Both verdicts are stored (`Diagnosis.judge_analysis` + `analyzer_fallback` flag).

The judge prompt enumerates *exactly* which steps to score (derived from trace spans), so it can't hallucinate "nothing failed" on a partial trace. Validator ensures coverage before accepting a judge verdict.

---

## Q4: "How does your propagation error detection actually work? Isn't that just heuristic?"

**Answer:** Yes, it's a heuristic — I document this honestly in `docs/limitations.md` #12. It works by checking if the summarization uses *definitional terminology* from a different document type than what classification output.

The logic:
1. Classification outputs a `DocumentType` (contract, invoice, report, correspondence, mixed)
2. Each type has **definitional markers** — e.g., "invoice"/"receipt"/"subtotal" for invoices; "contract"/"clause"/"indemnif" for contracts
3. Summarization's *opening sentence* is scanned for these markers
4. If a conclusive marker from a *different* type appears, it's a propagation error

Why opening sentence? Because a report *about* contracts will mention "contract" in body — that's not propagation error. The opening frames the document type.

Limitations acknowledged:
- It's not full dataflow analysis
- Cascading failures can be reported at multiple steps
- Mixed documents are exempt (they legitimately mix terminology)

---

## Q5: "Your cost tracking defaults to $0.00 — how is that useful?"

**Answer:** The accounting *code* is correct; the *values* are config. `.env.example` ships all prices as `0.0` with `VERIFY BEFORE USE` banners because:
- Hosted prices change without notice
- Inventing prices is worse than leaving them blank
- The code correctly computes `prompt_tokens × price_in + (completion + reasoning) × price_out`

When real prices are configured, `total_cost_usd`, per-run/global caps, and per-step spend attribution (including the `judge` tag) all work. I demonstrate this with a real run in the README showing 14/15 classifications correct, 100% fault-injection accuracy, and real token/cost numbers from a Minimax run.

---

## Q6: "SQLite for production? What about concurrency and scaling?"

**Answer:** SQLite WAL mode + `busy_timeout=5000` handles the demo scale (single writer, many readers). The design acknowledges limitations honestly in `docs/limitations.md` #12:
- Single-writer bottleneck
- No horizontal scaling
- Worker killed mid-job leaves job `running` (no reaper — documented as #2)

Mitigations:
- Traces written once, read many → WAL handles this well
- `db_path` is substitutable — PostgreSQL backend is implemented in `storage_postgres.py` behind the same `StorageBase` interface. Flip `DATABASE_URL=postgresql://...` to switch.
- `Storage`, `JobQueue`, `AuthManager` all take `db_path` — swap is a substitution, not a rewrite.

For the demo scope (Render free tier, 512MB disk, single instance), SQLite is the right choice. The PostgreSQL backend exists for when you outgrow it.

---

## Q7: "What happens when the worker crashes mid-job? You said there's no reaper."

**Answer:** That was limitation #2 — now **resolved** (see `docs/limitations.md` Resolved section).

Implementation:
- Added `claim_expires_at` column to `job_queue` table
- Worker sets expiration on claim (`QUEUE_STALL_TIMEOUT=300s` default)
- Worker sends heartbeat every `stall_timeout/3` seconds
- Periodic reaper in worker loop reclaims stalled jobs from other workers
- `forensics queue reaper --stall-timeout N` CLI for manual recovery

The worker handles `SIGTERM` cleanly (finishes current batch). For `SIGKILL`/OOM, the job is reclaimed by the reaper within `QUEUE_STALL_TIMEOUT` seconds.

---

## Q8: "What's the hardest technical decision you made, and what would you do differently?"

**Answer:** **Decision**: Building the custom chain instead of LangChain.

**Why**: Tracing clarity and testability mattered more than framework features. Every step is a clean async function with typed Pydantic I/O. The `@traced_step` decorator captures input, output, prompt, raw response, tokens, latency in one line. If steps are spaghetti, tracing is meaningless.

**What I'd do differently**:
1. **Invest in the React UI auth integration earlier** — the bundled UI can't send API keys (limitation #1). The backend auth works; the frontend just needs a key-input field and header injection.
2. **Add a pre-flight cost estimator** — current caps are post-hoc (limitation #8). A `max_tokens × price` check before sending would prevent single-request overshoot.
3. **Build the eval case dedup earlier** — duplicate eval cases inflated the suite silently until I added the unique index in migrations.

**What I'm proud of**: The fault injection harness. It turns one clean trace into 7 labeled failures, making analyzer accuracy *measurable* rather than claimed. That's the difference between a demo and a credible system.

---

## Quick Reference: Project Stats

| Metric | Value |
|--------|-------|
| Tests | 460 passing |
| Lines of code | ~12,000 Python + ~2,500 TSX |
| Document types | 5 (contract, invoice, report, correspondence, mixed) |
| Failure categories | 5 + unknown |
| Deterministic checks | 7 |
| Fault injection types | 7 |
| Seeded failures in demo | 5 (1 per category + mixed) |
| Max demo documents | 150 (generated) |
| Deploy target | Render free tier + 512MB disk |
| Config-driven | 100% (no hardcoded model IDs) |

---

## Demo Script (3 minutes)

1. **Home page** (15s): Show the four pipeline strata, engineering guarantees
2. **Run Demo** (45s): Click "Run Demo (15 Traces)" — watch traces populate in real-time
3. **Trace Explorer** (45s): Click a failed trace → see root cause badge → drill into spans → see failed checks evidence chain
4. **Confirm/Override** (15s): Click "Confirm Diagnosis" → see it become an eval case
5. **Analytics** (30s): Show failure rate, cost trend, model comparison, failure correlation heatmap
6. **DAG + Diff** (30s): Visual graph with green/red/tainted nodes, token diff view
7. **CLI** (15s): `forensics benchmark-analyzer` → 100% accuracy scorecard

Total: ~3 minutes, all live, no video editing needed.
