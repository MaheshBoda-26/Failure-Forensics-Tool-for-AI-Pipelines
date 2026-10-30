# Failure Forensics Tool — Viva & Interview Talking Points

This document contains the 8 hardest questions a college external examiner or engineering interviewer will ask about this project, each with a strong, honest answer grounded in the shipped codebase, followed by a step-by-step 3-minute live demo script.

---

## Q1: "Why build this when LangSmith, Langfuse, and Braintrust exist?"

**Answer:** General-purpose observability tools log spans and prompts, or run an expensive LLM-as-judge over the final output, but they don't solve **causal step localization** in multi-step pipelines where step 2 subtly corrupts an entity and step 4 fails downstream.

Key architectural differentiators I built:
- **Deterministic first, cross-family LLM judge second**: The analyzer runs **7 deterministic checks** (`entity_grounding`, `label_enum`, `date_regex`, `context_preservation`, `propagation`, `schema_validity`, `label_grounding`) plus **9 domain plugin checks** before making any LLM judge call. Most platforms lead with LLM-as-judge, which is slow, non-deterministic, and expensive.
- **Fault injection for ground-truth benchmarking**: I built a 7-fault injection harness (`src/forensics/faults.py` and `/analytics/benchmark`) that corrupts clean traces in 7 controlled ways (`hallucinated_entity`, `bad_date_format`, `invalid_enum`, `misclassification`, `context_loss`, `propagation_failure`, `prompt_truncation`). That lets us objectively measure the analyzer's step-localization and category accuracy (**100% across 12 clean baselines**) rather than guessing whether the judge works.
- **Closed-loop human-to-regression flywheel**: When an operator confirms or overrides a root-cause diagnosis in the UI (`POST /diagnoses/{id}/confirm`), the system automatically mints a permanent regression `EvalCase` (`src/forensics/eval_simple.py`) so future prompt or model changes are tested against real historical failures.
- **Zero-infrastructure local-first storage**: Dual JSON-payload + SQLite-WAL index (`src/forensics/storage.py`) with a drop-in PostgreSQL backend (`src/forensics/storage_postgres.py`) verified by schema-parity tests (`tests/test_schema_parity.py`).

---

## Q2: "Your deterministic analyzer — how do you know it's actually accurate?"

**Answer:** We measure it continuously in both the CLI (`forensics benchmark-analyzer`) and the React **Analytics** tab (**Fault injection scorecard**). The fault injector takes every eligible clean trace, injects all 7 fault types, runs `analyze_trace`, and scores whether the analyzer localized both the exact failing step and the failure category:
- **Step-localization accuracy**: **100.0%** across all scoreable injected faults on our 12 clean baseline traces
- **Category accuracy**: **100.0%** across all scoreable injected faults
- **Seeded dataset reproduction**: **5/5** seeded failures in `dataset/labels.json` (`contract_001`, `invoice_003`, `report_003`, `correspondence_002`, `mixed_001`) localized to their exact step and category

When a fault's precondition isn't met on a baseline (for example, `context_loss` on an offline fixture trace that extracted zero salient entities), `Fault.scoreable` marks it `unscoreable` rather than counting a no-op corruption as a false miss.

---

## Q3: "The judge LLM — how do you prevent self-preference bias?"

**Answer:** Three-layer mitigation enforced in code (`src/forensics/config.py` and `src/forensics/analyzer_simple.py`):
1. **Provider family separation enforced at startup**: `validate_startup()` verifies that `JUDGE_MODEL_FAMILY` differs from `PIPELINE_MODEL_FAMILY` and `PIPELINE_FALLBACK_MODEL_FAMILY`. If someone configures the same model family for both pipeline and judge while `JUDGE_FAMILY_SEPARATION_REQUIRED=true`, the service refuses to boot.
2. **Cross-family model pairing**: Default configuration runs `minimaxai/minimax-m3.1-flash` (`minimaxai` family) for the 4-step pipeline and `meta/llama-3.1-8b-instruct` (`meta` family) for the semantic judge.
3. **Deterministic-first auditability**: Deterministic checks always run first. When the judge runs (`JUDGE_ENABLED=true`), both the deterministic step scores and `Diagnosis.judge_analysis` (along with `analyzer_fallback: bool`) are persisted so a human reviewer can see when and why the judge overrode the deterministic analyzer.

---

## Q4: "How does your propagation error detection actually work? Isn't that just heuristic?"

**Answer:** Yes, it is a targeted lexical heuristic — documented transparently in `docs/limitations.md` §11. It detects when the `summarization` step frames a document using definitional terminology that contradicts the upstream `classification` step:
1. `classification` outputs a `DocumentType` (`contract`, `invoice`, `report`, `correspondence`, `mixed`).
2. Each type has conclusive opening-sentence markers (e.g., `"invoice"`, `"receipt"`, `"subtotal"` for invoices; `"agreement"`, `"clause"`, `"indemnif"` for contracts).
3. `check_propagation` inspects the opening sentence of `summarization` (because a report *about* contracts can legitimately mention contracts in its body, whereas the opening sentence frames what the document *is*).
4. If the opening sentence frames the document as a different type than `classification` emitted (or drops upstream fields), `check_propagation` flags a `propagation_error` at `summarization`.

---

## Q5: "How do you prevent runaway LLM costs during demos or batch evaluations?"

**Answer:** We enforce **both** pre-flight and post-hoc spend guards in `src/forensics/llm.py`:
- **Pre-flight cost estimation (`SpendTracker.check_preflight`)**: Before sending an HTTP request to NVIDIA NIM, the client estimates upper-bound cost (`len(prompt) / 4` input tokens + full `max_tokens` output budget at the model's configured per-1M rates in `.env`). If the call would breach `SPEND_CAP_PER_RUN_USD` (`$0.50`) or `SPEND_CAP_GLOBAL_USD` (`$20.00`), it raises `QuotaExhaustedError` before a single token is billed.
- **Post-hoc ledger & SHA-256 response cache**: Actual `prompt_tokens`, `completion_tokens`, and `reasoning_tokens` from the provider response are recorded on every `Span` and summed via `span_cost_usd()`. Content-addressed caching (`CACHE_ENABLED=true`) short-circuits repeated prompts at `$0.00` cost.

---

## Q6: "Why dual storage (JSON files + SQLite WAL) instead of just one database?"

**Answer:** Each store is optimized for its access pattern (ADR 0004):
- **Atomic JSON files (`data/traces/<trace_id>.json`)**: Hold the full, arbitrarily nested span payloads (`prompt`, `raw_response`, `input_json`, `output_json`). Written via temp-file + `os.replace()` after pre-storage PII redaction (`src/forensics/pii.py`), making traces human-inspectable and git-commit-friendly (`dataset/live/traces/`).
- **SQLite WAL index (`data/forensics.db`)**: Indexes metadata, timestamps, latencies, token/cost totals, diagnoses, flags, tenants, API keys, and the job queue so `/analytics` and `/traces` never have to parse dozens of JSON files on each request.
- **Drop-in PostgreSQL parity**: `src/forensics/storage_postgres.py` implements the exact same `StorageBase` interface (`DATABASE_URL=postgresql://...`), and `tests/test_schema_parity.py` verifies table-for-table and column-for-column parity on every `make check` run.

---

## Q7: "What happens when a background worker crashes mid-job?"

**Answer:** Implemented in `src/forensics/queue.py` and `src/forensics/worker.py` (Spec 07):
- Jobs are claimed atomically via `UPDATE job_queue ... RETURNING *` with a lease expiration timestamp (`claim_expires_at = now + QUEUE_STALL_TIMEOUT`, default 300s).
- Active workers send periodic heartbeats every `stall_timeout / 3` seconds to extend their lease.
- A periodic reaper in the worker loop (and `forensics queue reaper` in the CLI) reclaims any job whose lease expired after a worker `SIGKILL` or crash, resetting it to `pending` (up to `max_attempts`) so no document run is ever silently lost.

---

## Q8: "What was the hardest engineering decision you made?"

**Answer:** **Building a custom, zero-framework pipeline and `@traced_step` decorator (`src/forensics/tracing.py`) instead of wrapping LangChain.**

- **Why**: LangChain's internal callback trees obscure the exact boundary between step inputs and outputs, and serializing framework objects into spans risks leaking client state or API keys. With our 1-line `@traced_step("step_name")` decorator and `ContextVar`-scoped `StepTelemetry` side-channel, every pipeline step stays a pure typed Pydantic function (`Document -> IntakeOutput -> ExtractionOutput -> ClassificationOutput -> SummarizationOutput`), while the decorator automatically captures latency, tokens, cost, model provenance, OTel export, and PII redaction.
- **Safety guarantee**: `tests/test_llm.py::test_analyzer_does_not_execute_trace_payload` and `_safe_repr` verify that neither the tracer nor the analyzer ever calls `eval()` or leaks `Settings` secrets into trace files.

---

## Quick Reference: Shipped Project Metrics

| Metric | Value |
|---|---|
| **Automated Test Suite** | **582 passing tests** (`568` backend `pytest` + `14` frontend `vitest`), `0` errors in `ruff` & `mypy --strict` |
| **Pipeline Architecture** | 4 traced steps (`Intake → Extraction → Classification → Summarization`) |
| **Document Types** | 5 (`contract`, `invoice`, `report`, `correspondence`, `mixed`) |
| **Failure Categories** | 5 (`extraction_hallucination`, `misclassification`, `propagation_error`, `prompt_failure`, `context_loss`) + `unknown` |
| **Forensic Checks** | 7 core deterministic checks + 9 domain plugin checks (`src/forensics/custom_checks.py`) + cross-family Judge LLM |
| **Fault Injection Harness** | 7 controlled fault injectors (`100.0%` step & category localization accuracy across 12 clean baselines) |
| **Pre-Seeded Corpus** | 20 traces in `data/forensics.db` (5 committed real NVIDIA NIM traces + 15 dataset traces) + 150-doc `dataset_large/` |
| **Enterprise Guards** | Multi-tenant RBAC (`ffk_` bcrypt keys + `read`/`write` scopes), SQLite token-bucket rate limiter, pre-storage PII redaction, OTel exporter |

---

## 3-Minute Live Evaluation Demo Script

Before the examiners arrive, run `make run-web` and open `http://127.0.0.1:8000`.

1. **Header & Overview (30s)**:
   - Point out the top bar's runtime status pill (`Live NIM · minimax-m3.1-flash` or `Offline fixture`) and `API connected` indicator.
   - Show the **Overview** KPIs (**20 traces**, **30.0% failure rate**, **5 open regressions**) and the **Latest failure** causal spine showing the exact stage where the pipeline broke (`intake → extraction → classification → summarization`).
2. **Interactive Custom Document Run (45s)**:
   - Click **Test document** in the top header or left sidebar.
   - Pick the **Invoice (hallucination fault)** preset (or upload a `.txt` file / paste an examiner's text), select `invoice` as the ground-truth expected type, and click **Run & diagnose**.
   - Watch the UI run all 4 steps, diagnose the failure, and jump directly to the new trace in the **Traces** view.
3. **Trace Forensics & Human-in-the-Loop Confirmation (45s)**:
   - In **Traces**, click through the 4 stages of the spine (`intake`, `extraction`, `classification`, `summarization`).
   - Show the failed deterministic check (`entity_grounding` or `label_grounding`), the natural-language **Evidence chain**, and the **Prompt & raw response** drawer.
   - Click **Confirm** (or **Override** to assign a custom step/category) and explain how confirming a failure automatically mints a regression `EvalCase`.
4. **Execution Graph & Ground-Truth Diff (30s)**:
   - Switch to **Execution graph** to show the React Flow DAG (green upstream nodes, red root-cause node, amber tainted downstream nodes).
   - Scroll to **Input vs Output Comparison** to show extracted entities highlighted inline in the source document and the **Ground-truth vs Observed Output** classification comparison (`expected_type` vs `document_type`).
5. **Analytics, Spend & Fault-Injection Scorecard (30s)**:
   - Switch to **Analytics** to show **Failure correlation (step × category)**, **Model comparison & spend** (`minimaxai/minimax-m3.1-flash` token and USD breakdown), **Evaluation suite regressions** (click **Run eval suite**), and the **Fault injection scorecard** showing **100.0% step and category accuracy** across all 7 injected fault types.
