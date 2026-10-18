# Failure Forensics Tool — Review Findings Fix Plan (2026-10-18)

## Goal
Close the 8 findings from the external review so the README/`docs/limitations.md` guarantees are
actually true: no unauthenticated file read, no plaintext PII leak, no analyzer verdict lost on
save, `DRY_RUN` reproducing the 5 seeded failures, and the React client matching the FastAPI
contract instead of showing hardcoded numbers.

Baseline: `main` @ `2c57354` (working tree has an unrelated `scheduler.py` alert-isolation edit).
Gate for every phase: `make check` (ruff + mypy strict + `pytest -m "not live"`).

## Status — executed 2026-10-18 (read this first)

This plan was executed by **two sessions at once** in the same worktree, which is
why the result is split across two branches:

| Phase | Where it landed |
|---|---|
| A (path traversal, judge persistence) | `fix/review-findings-2` commit `49812e0` |
| B (PII rewrite, analyze-before-redact) | `fix/review-findings-2` commit `15672a9` |
| C (config knobs) | `fix/review-findings-2` commit `15672a9` |
| D1/D2 (`document_id` threading, DRY_RUN fixture) | `fix/review-findings-2` commit `15672a9` |
| D3/D4 (eval `expected_type`, case dedup) | `fix/review-findings-2-tail` (this branch) |
| E (frontend sync) | owned by the concurrent session; **not** in this branch |
| F (docs + verification) | `fix/review-findings-2-tail` (this branch) |

`fix/review-findings-2-tail` is branched from `15672a9` so its diff is purely the
tail (D3, D4, F) and cannot clobber the in-flight frontend work.

Verified on this branch: `ruff check` clean, `ruff format --check` clean, `mypy`
strict clean, **390 tests pass / 0 fail**. `forensics demo` with `DRY_RUN=true`
reports 13/15 classifications and 5/5 step + 5/5 category accuracy; the two misses
are the seeded misclassifications. `benchmark-analyzer` is unchanged at 85.7%
(§6 still holds — the canned extraction returns no entities but for `invoice_001`).

## Verification done while planning (not assumed)

| Claim | How checked | Result |
|---|---|---|
| SPA catch-all can escape `frontend/dist` | read `api.py:707-711` | **Confirmed** — `target = _dist_path / full_path`, only `is_file()` checked |
| `PIPELINE_MODEL_MAX_TOKENS` ignored | ran `Settings()` with `PIPELINE_MODEL_MAX_TOKENS=8192` | **Confirmed** — `max_tokens` stays `4096`; field not declared. `build_model_configs` is `mode="before"`, so it only sees keys for *declared* fields (this is why the `OTEL_*` fix worked and this one does not) |
| `is_reasoning` never read | read `config.py:351-365` | **Confirmed** — `build_model` never reads it |
| `DRY_RUN` does not reproduce the 5 seeds | ran the 4-step pipeline over all 15 docs in dry-run + `analyze_trace` | **Confirmed** — 0/5 declared `(step, category)` pairs reproduced; 12/15 docs misclassify as `contract`; `report_001` yields `propagation_error`, not the declared `prompt_failure` |
| PII offset corruption | read `pii.py:88-109` | **Confirmed** — single scalar `offset` applied to matches found in the *original* text, across patterns sorted by name |
| analyze-after-redact | README order in `worker.py:60-63`, `cli.py:93-96`, `api.py:496-498` | **Confirmed** — `save_trace` → `finalize_trace` redacts in place *before* `analyze_trace` |
| `judge_analysis` / `analyzer_fallback` dropped | `storage.py:120-140` (table), `362-406` (write/read) | **Confirmed** — neither column exists; `Diagnosis` model has both fields |
| eval omits `expected_type`, dup cases | `eval_simple.py:165-186`, `310-358`; `api.py:354-359` | **Confirmed** — `run_eval_case` passes `document_id=case.trace_id`; dedup is per-call `seen`; `confirm_diagnosis` inserts an `EvalCase` per flag unconditionally |
| frontend type drift | `types.ts` vs `models.py`; `TraceDetailView.tsx:33,253,134,379`; `TraceSpine.tsx:200`; `AnalyticsView.tsx:276`; `App.tsx:514` | **Confirmed** — all four hardcoded fallbacks present, at the same content (review's line numbers are slightly off) |

## Phase A — Security & persistence (Critical/High)
- [ ] **A1. Path-traversal guard in the SPA catch-all.** In `api.py:serve_frontend_spa`, resolve
      `target = (_dist_path / full_path).resolve()` and serve the file only when
      `target.is_relative_to(_dist_path.resolve())` **and** `target.is_file()`; otherwise return
      `index.html`. Keep the existing API-404 semantics (the JSON API is mounted first, so this
      route is the last resort).
      → Verify: new test requests `/..%2f..%2f.env`, `/../forensics.db`, an absolute-escape and a
      URL-encoded variant; all get `index.html` (200 text/html) and never file bytes. A normal
      `/assets/...` path still serves.
- [ ] **A2. Persist `judge_analysis` + `analyzer_fallback`.** Add both columns to the `diagnoses`
      `CREATE TABLE` in `storage.py`, and add an idempotent ALTER migration in `migrations.py`
      (extend the migration to cover *column additions*, not just `tenant_id`: add a
      `_COLUMN_ADDITIONS` map so `migrate()` stays the single place that closes old-DB gaps).
      Serialize `judge_analysis` as JSON and `analyzer_fallback` as int in `create_diagnosis`;
      parse both in `_load_diagnosis_row`.
      → Verify: extend `tests/test_storage_eval_api.py` — create a `Diagnosis` with a
      `JudgeAnalysis` and `analyzer_fallback=True`, `save_trace`→`create_diagnosis`→`get_diagnosis`
      round-trips both; a DB created by the *old* schema (pre-created with the current
      `CREATE TABLE` minus the columns) migrates on `Storage()` construction and keeps its rows.

## Phase B — Data fidelity (High)
- [ ] **B1. Rewrite `PIIRedactor.redact`.** Collect `(start, end, entity_type, match)` for every
      enabled pattern, drop overlaps deterministically (earlier `start` wins; on a tie the longer
      span wins), then splice replacements from the highest `start` down so earlier indices stay
      valid. Preserve the `Redaction` records and their original offsets.
      → Verify: extend `tests/test_pii_redaction.py` — (a) custom pattern with a replacement
      *longer* and *shorter* than the match, ordered so it sorts before/after an email and an IP in
      the same string; assert the email/IP are fully gone and the JSON still parses; (b) the existing
      23 tests still pass unchanged (they pin the current public behaviour).
- [ ] **B2. Analyze before redaction.** Run `analyze_trace` on the unredacted in-memory trace, then
      `save_trace` (which redacts in place before persisting). Reorder at all three call sites:
      `worker.process_document`, `cli.run`, `api.trigger_pipeline_demo`.
      To avoid leaking the now-unredacted evidence, redact the diagnosis's free text at its single
      persistence point: `Storage.create_diagnosis` applies the configured redactor to
      `evidence_chain` and to `judge_analysis` string fields when redaction is enabled.
      → Verify: a test that enables redaction, runs a trace whose `output_json` contains an email/IP,
      and asserts (a) the span text was NOT redacted when `analyze_trace` ran, so
      `entity_grounding` passes; (b) the persisted JSON/SQLite span has no plaintext; (c) the
      persisted diagnosis evidence has no plaintext. Regression-test the existing "no entities in
      dry-run" case.

## Phase C — Config bindings (Medium)
- [ ] **C1. Declare the model knobs.** Add
      `{pipeline,pipeline_fallback,judge,cheap}_model_max_tokens: int` and
      `*_model_is_reasoning: bool` to `Settings`, and thread both through `build_model` (read
      `is_reasoning` in `ModelConfig`). Declaring the fields is what makes `mode="before"` receive
      them (proved above).
      → Verify: extend `tests/test_config.py` — `PIPELINE_MODEL_MAX_TOKENS=8192` →
      `settings.pipeline_model.max_tokens == 8192`; `JUDGE_MODEL_IS_REASONING=true` →
      `settings.judge_model.is_reasoning is True`; defaults still `4096`/`False`. Update
      `.env.example` only if it names a var we do not honour.

## Phase D — DRY_RUN fixture & eval loop (High/Medium)
- [ ] **D1. Thread `document_id` to the fixture.** Add a `document_id: str = ""` parameter down
      `Pipeline.run` → the 4 `LLMClient` step methods → `NIMClient.run_with_fallback` →
      `NIMClient.structured`. Callers pass the real id (`worker` already has `doc_id`; `cli` uses
      `doc_path.stem`; `api.run-demo` uses `doc_id`; eval uses the *original* trace's
      `document_id`, fixing the `case.trace_id` misuse).
      → Verify: `tests/test_llm.py` — the dry-run fixture returns the seeded type/entities/summary for
      the 5 seeded ids and the ground-truth type for the other 10; no reliance on prompt keywords for
      the seeded docs.
- [ ] **D2. Make the fixture reproduce all 5 declared failures.** Replace the prompt-keyword
      detection with a `doc_id`-keyed table (ground-truth type per doc, plus the two
      misclassification overrides), keep the keyword heuristic only as the fallback for unknown ids,
      and make `report_001` emit a schema-invalid summarization payload (via
      `SummaryOutput.model_construct`) so it fails as the declared `prompt_failure`.
      → Verify: `tests/test_llm.py` + a new end-to-end test running dry-run over `dataset/labels.json`
      and asserting each `should_fail` doc produces exactly its declared `(failure_step,
      failure_category)` and each non-failing doc produces `("none", unknown)`. This is the README/
      §7 claim, tested.
- [ ] **D3. Pass `expected_type` through eval.** `run_eval_case` gains the evaluator's ground-truth
      type and calls `analyze_trace(trace, doc_text, expected_type)`; resolve it from
      `labels.json` by the original `document_id` (a small shared helper next to the existing
      labels loader).
      → Verify: `tests/test_storage_eval_api.py` — a `misclassification` eval case is reported
      NOT-fixed when the pipeline still misclassifies (today it passes).
- [ ] **D4. Stop duplicate `EvalCase` rows.** In `create_eval_cases_from_confirmed_flags`, seed the
      dedup set from `storage.list_eval_cases()` keyed by `(trace_id, failing_step)` (tenant-scoped).
      In `api.confirm_diagnosis`, skip flags whose `(trace_id, failing_step)` already has a case.
      Add a `UNIQUE(trace_id, failing_step, tenant_id)` index on `eval_cases` via `migrations.py`
      as the backstop.
      → Verify: `tests/test_storage_eval_api.py` — calling `create_eval_cases_from_confirmed_flags`
      twice creates N then 0 new cases; `POST /diagnoses/{id}/confirm` twice creates one case; the
      unique index rejects a raw duplicate insert.

## Phase E — Frontend sync (Medium)
- [ ] **E1. Align `types.ts` with `models.py`.** `EvalRun` → `pipeline_version`,
      `prompt_versions`, `started_at`, `completed_at`, `total_cases`, `passed_count`,
      `failed_count` (drop `baseline_accuracy`/`current_accuracy`/`regressions`/`improvements`).
      Remove `Span.checks` (backend `Span` has none); add `diagnosis_id`, `confidence`, `error`,
      `status` where they actually exist.
      → Verify: `npm run build` (vite + tsc) passes with no `any`-escape.
- [ ] **E2. Derive real numbers, delete the fallbacks.**
      `TraceDetailView` checks evaluated ← `diagnosis.step_scores[].deterministic_checks` (passed/total);
      `TraceSpine` per-span pass count ← the matching `step_scores` entry;
      `AnalyticsView` ← `passed_count`/`total_cases` from the latest run;
      `App.tsx` sidebar failure flag ← `trace.status`/diagnosis, not `document_id.includes("001")`.
      `TokenDiffView` ground truth ← the diagnosis category (or hide the row) instead of a hardcoded
      `"INVOICE"`. Guard every root-cause badge/banner with `root_cause_step !== "none"`, and replace
      the hardcoded `Stage 03 (...)` with the step's real index.
      → Verify: `npm run build`; manual check of a passing trace (no red banner, no "18 / 20 PASS")
      and a seeded failing trace.
- [ ] **E3. Keep the mock data honest.** Any remaining sample numbers (`totalTraces || 15`, `0.0034`
      cost, `N=…`) must be labelled as a sample or removed.

## Phase F — Docs & verification
- [ ] **F1. Correct `docs/limitations.md` §6/§7 and the README seeded-failure table** to match the
      now-real dry-run behaviour; add the new limit if any documented guarantee changes.
- [ ] **F2. Record the decisions** (diagnosis redaction point; fixture keyed by `doc_id` not prompt
      text; `report_001` schema-invalid construction) in the tracker + an ADR if it changes a
      documented contract.
- [ ] **F3. Full gate.** `make check`; `python -m forensics.cli demo` (dry-run) shows the 5 seeded
      failures; `scripts/smoke.sh` against a local server still passes; `cd frontend && npm run build`.

## Open decisions — resolved
1. **`report_001` ground truth.** Resolved: **fix the fixture, keep the label.** The fixture emits a
   schema-invalid summarization payload constructed with `SummaryOutput.model_construct`
   (an empty `summary`, which `min_length=1` makes unrepresentable otherwise), so `labels.json`
   stays the source of truth and the eval loop scores a real `prompt_failure`.
2. **Delivery.** Resolved: isolated worktree + branch (`fix/review-findings-2-tail`) stacked on
   `fix/review-findings-2`, because a second session is committing to that branch concurrently.
3. **Scope.** Resolved: all 8 findings — A–D2 on `fix/review-findings-2`, D3/D4/F here, E owned by
   the concurrent session.

## Decisions recorded in this branch
- **D4 is enforced in two layers.** `Storage.create_eval_case` is idempotent on
  `(trace_id, failing_step, tenant_id)` (returning the existing case, tolerating the
  `IntegrityError` a lost race produces) and `migrations.idx_eval_cases_unique` is the schema
  backstop. Callers additionally pre-seed their dedupe set / check before inserting, so counts stay
  honest. A legacy database holding duplicates is repaired on first `Storage()` construction, keeping
  the earliest row.
- **One ground-truth loader.** `eval_simple.document_ground_truth` is now the single place that reads
  `labels.json`; the CLI, the eval loop, and `POST /traces/{id}/diagnose` all use it, so no analysis
  path can silently skip `check_label_grounding`.
- **`run_eval_case` traces the original document id**, not `case.trace_id`, so document-keyed
  behaviour (the offline fixture, cache keys) applies to the re-run.
- ADR `docs/adr/0016-dry-run-fixture-and-analysis-order.md` records the fixture rewrite and the
  analyze-before-redaction ordering; `docs/limitations.md` §7/§7b and the README seeded-failure note
  were corrected to match measurement.
