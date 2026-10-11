# Full-Codebase Review — Phase 0 to End

**Scope:** every line of `src/forensics/*` (20 files, ~4,600 lines), `streamlit_app.py`
(726), `demo.sh`, `tests/*` (2,199), `dataset/labels.json`, `pyproject.toml`,
`Makefile`, `.gitignore`, `.pre-commit-config.yaml`.

**Method:** 4 rounds. Read-everything pass → empirical probes (every finding marked
BUG/SECURITY below was reproduced by running it, not inferred) → test-suite audit →
dataset/infra consistency audit.

This document supersedes `docs/REVIEW-phase4-6.md` (that file is kept for the Phase 4-6
narrative; every finding in it still stands).

---

## Executive summary

The project has **two independent structural problems**, and they explain nearly every
individual bug:

1. **The unit tests test the units, not the path.** `test_analyzer_phase3.py` exercises
   each `check_*` function directly. The production path
   `run_deterministic_checks → build_step_scores → find_root_cause` is only ever tested
   with single-result fixtures. Every order-dependent and dict-collapse bug therefore
   passes CI.
2. **`make check` does not cover the code it is meant to gate.** `ruff`/`mypy` are scoped
   to `src` and `tests`; `streamlit_app.py`, `demo.sh` and `docs/` are never linted or
   typechecked. Phases 4-6 shipped with 17 lint errors in the UI that only surfaced at
   pre-commit — after being reported as verified.

Add to those: **zero tests** for `storage` (564 lines), `cli` (301), `eval_simple` (225),
`api` (179), `fake_llm` (207) — 1,476 lines of Phase 4-6 code with no coverage — and a
**dataset whose ground truth the analyzer cannot reproduce**.

**Bottom line:** the forensic *engine* (tracing, telemetry, spend caps, OTel, LLM
client) is genuinely well built and well tested. The *product surface* built on top of it
in Phases 3-6 does not run.

| Area | Verdict |
|---|---|
| Phase 0-1 config / LLM client / OTel / dataset | Solid engine; **dataset ground truth is wrong** |
| Phase 2 tracing + telemetry | Solid; **two fidelity bugs at finalize** |
| Phase 3 analyzer | 24 tests, all passing; **integration path untested and broken** |
| Phase 4 Streamlit UI | **Does not import; crashes on render and on both core actions** |
| Phase 5 eval loop | **Dead at five independent points** |
| Phase 6 analytics / docs / demo | **Reports 0% failure on a dataset seeded with 5 failures** |
| Tests / gates | **Coverage and scope both insufficient** |

---

# CRITICAL — app-breaking, or silently wrong

### C1. Streamlit UI crashes on render: `Span.status.value` (6 sites)
`Span.status` is `Literal["success","failure","degraded"]` → Pydantic materializes it as
a plain `str`. Reproduced: `Span.status = 'success' -> has .value: False`.
`streamlit_app.py:206,208,344,357,711,712` all call `span.status.value`.
(Lines 645/665 use `trace.status.value`, which *is* a `TraceStatus` StrEnum and is fine.)
The span renderer, the pipeline graph, and the expander list all raise `AttributeError`
on first paint.

### C2. Streamlit confirm/override crash: `Storage.update_flag()` does not exist
`streamlit_app.py:548` and `:577` call `storage.update_flag(...)`. `Storage` has no such
method — the real one is `update_diagnosis_confirmation`. Both flag-confirmation
handlers, the central Phase 4/5 interaction, raise `AttributeError`.

### C3. Streamlit category filter crashes: `Trace.diagnosis` does not exist
`streamlit_app.py:649`: `t.diagnosis and t.diagnosis.category.value == category_filter`.
`Trace` has no `diagnosis` field; diagnoses live in a separate table. Reproduced:
`has attr: False`. Any category other than "All" raises `AttributeError`.

### C4. `streamlit_app.py` cannot import in the project environment
```
.venv/bin/python -c "import streamlit_app"
ModuleNotFoundError: No module named 'networkx'
```
`pyproject.toml` declares `streamlit` but **not `plotly` or `networkx`**. The previous
verification of this import was run against system Python 3.14 where the packages had
been installed ad hoc; in the declared `>=3.12,<3.13` venv neither exists.

### C5. `forensics` console script is broken
`pyproject.toml:29` → `forensics = "forensics.cli:main"`. `cli.py` has no `main`
(verified `hasattr(cli,'main') == False`). `pip install -e . && forensics` fails; only
`python -m forensics.cli` works.

### C6. Eval loop is dead at link 1: `Flag.diagnosis_id` is never assigned
`eval_simple.py:211` gates on `if flag.diagnosis_id:`. Nothing in the codebase ever
*assigns* it — both the API and the UI construct `Flag(...)` with the field left `None`.
**`eval-cases` creates 0 cases, forever**, matching observed CLI output.

### C7. Eval never runs the pipeline: coroutine created and discarded
```python
# eval_simple.py:63, inside a *sync* function
_ = pipeline.run(doc)  # async def run(...) -> coroutine, never awaited
```
Reproduced: `RuntimeWarning: coroutine ... was never awaited` /
`fixed=False score=0.0 root=none`. `trace.spans` is always empty, `root_cause_step`
resolves to `"none"`, the `case.failing_step` lookup misses, `fixed` is hardcoded
`False` — **every case reports "STILL FAILING" regardless of pipeline behaviour.** The
regression signal is inverted.

### C8. Eval can never find the document: `doc_map` keyed by doc_id, read with trace_id
```python
doc_map[doc_info["doc_id"]] = ...  # keys like "contract_001"
doc_text = doc_map.get(case.trace_id, "")  # reads a hex trace id
```
Reproduced: `lookup('a3f9c1e2b7d4') -> 'MISS'`. Every case is skipped. <!-- pragma: allowlist secret -->

### C9. Second diagnosis in the same second crashes with a 500
`diagnosis_id=f"diag-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"` — second-resolution
timestamp used as PRIMARY KEY. Two `/diagnose` calls within one second:
`sqlite3.IntegrityError: UNIQUE constraint failed: diagnoses.diagnosis_id`.
`get_diagnosis_for_trace` also then returns an arbitrary colliding row.

### C10. `finalize_trace` silently flips a real failure to SUCCESS
`TraceContext.__exit__` correctly sets `FAILURE`; `finalize_trace` then unconditionally
overwrites: `trace.status = compute_trace_status(trace.spans)`.
Reproduced: `after __exit__=failure -> after finalize_trace=success (spans=0)`.
Any exception raised *outside* a `@traced_step` is recorded as success. This violates
the Phase 2 truthful-status invariant, and no test covers it (see T3).

### C11. `mixed` is not a `DocumentType` — 3 of 15 documents can never be classified
```
DocumentType members : ['contract', 'correspondence', 'invoice', 'report']
labels.json doc_types: ['contract', 'correspondence', 'invoice', 'mixed', 'report']
UNREACHABLE labels   : ['mixed']
```
`mixed_001/002/003` are unclassifiable by construction: `ClassificationOutput.document_type`
is typed to the enum, so the pipeline cannot emit the label the dataset expects.
`validate-dataset` does not check this.

### C12. Two of the five seeded failures have ground truth the analyzer cannot produce
| doc | step | category | reachable? |
|---|---|---|---|
| contract_001 | classification | misclassification | reachable |
| invoice_001 | extraction | extraction_hallucination | reachable |
| report_001 | summarization | prompt_failure | reachable |
| correspondence_001 | **intake** | context_loss | **UNREACHABLE** |
| mixed_001 | **extraction** | propagation_error | **UNREACHABLE** |

- `context_loss` is emitted **only** by `check_context_preservation`, which returns early
  unless `span.step_name == "summarization"`. Labeling it at `intake` is impossible —
  and `intake` is a deterministic non-LLM step (`llm.py` `intake` is a `@staticmethod`
  whose docstring states "Deterministic: no LLM call, no spend").
- `propagation_error` is emitted **only** by `check_propagation`, which returns early
  unless `span.step_name == "summarization"`. Labeling it at `extraction` is impossible.

The eval loop's stated purpose is measuring category accuracy against this ground
truth; it would report ~0% accuracy for reasons that are artifacts of the label
definitions.

---

# WARNING — silently wrong results / data integrity

### W1. Analyzer verdict is order-dependent; a later passing entity masks a hallucination
`build_step_scores` collapses `CheckResult`s into `deterministic_checks[name] = passed`,
so for repeated names the **last wins**. Same extraction step, one hallucinated + one
grounded entity:

| order | `deterministic_checks` | category |
|---|---|---|
| bad first, good last | `{'entity_grounding': True}` | `unknown` (conf 0.5) |
| good first, bad last | `{'entity_grounding': False}` | `extraction_hallucination` |

The diagnosis flips on entity ordering alone. The synthetic
`context_preservation_any_failed` key is a band-aid applied to exactly one of six checks;
the other five share the defect.

### W2. Schema failure miscategorised as `extraction_hallucination`
Non-JSON extraction output yields three parse failures (`entity_grounding`,
`date_regex`, `prompt_schema`); `find_root_cause` tests `entity_grounding` first and
discards the `prompt_schema` evidence. Reproduced:
`category=extraction_hallucination (expected prompt_failure)`.

### W3. `create_eval_run` writes `completed_at=""`, which cannot be read back
`get_eval_run` then fails validation: `ValidationError: completed_at — Input should be a
valid datetime, input is too short`. Same `""` idiom in `write_trace_to_db` for
`created_at`/`completed_at`.

### W4. Span timestamps do not bracket the work
`traced_step` sets `started_at=_utc_now(), ended_at=_utc_now()` **after** the step
returns (`tracing.py:248`). Reproduced: `latency_ms=354` but
`started_at→ended_at delta=5e-06s`. Every span claims ~0 duration in its timestamps
while `latency_ms` is real — so "Avg Time to Root Cause" and any time-to-detection
metric is meaningless, and `load_trace_from_db`'s `ORDER BY started_at` gives
unreliable ordering.

### W5. Eval cases contain no ground truth
`flag_to_eval_case` sets `corrected_output = {"note": flag.note or "Human corrected"}` —
a note, not a corrected output — and `run_eval_case` never compares `bad_output` against
it. The loop can only re-run deterministic checks; it cannot detect correctness.

### W6. `faults.py` is unimplemented, so there is no ground-truth source
It is a stub ("Status: not implemented. See PROMPT.md D6"). The fault-injection harness
is what PROMPT.md requires for objectively scoring category accuracy.

### W7. Eval falsifies version metadata
`run_eval` hardcodes `pipeline_version="0.1.0"`, `prompt_versions={}` ("Would come from
settings"). `_get_prompt_version()` in `models.py` returns `pipeline_version` as a
"proxy", so `prompt_version` is never a prompt hash — yet `EvalCase`/`EvalRun` group and
report by it.

### W8. Spend cap discards the cost of the call that trips it
`SpendTracker.check_and_add` raises *before* incrementing. The call was already made and
paid for, but neither the per-run nor the global total records it, so global spend
under-reports actual money spent.

### W9. API `confirm` is a dead end
`api.confirm_diagnosis` contains, under "If confirmed, create eval case":
`if flags: # In a real app, we'd link the flag to the diagnosis / pass`.
The API→eval path documented in `demo.sh` cannot work.

### W10. `/eval/runs` is a stub despite the storage method existing
`return []` with the comment "Would need to implement list_eval_runs in storage" —
`Storage.list_eval_runs` was added in Phase 4-6 and is never called.

### W11. Analytics reports 0% failure on a dataset seeded with 5 failures
`failure_rate` counts only `TraceStatus.FAILURE`. Dry-run traces are `SUCCESS` (the
seeded bad outputs are semantic, not status-bearing), so `/analytics` reports
`Failure Rate: 0.0%` — the headline number of the demo is wrong.

### W12. Repeated step names collapse in `run_deterministic_checks`
`all_results[span.step_name] = step_results` overwrites on duplicates: two
`classification` spans → one result set, while `build_step_scores` still emits two
StepScores. Both spans get the same verdict from the same check data, regardless of
their actual outputs. Affects any retried step.

### W13. Confirmation is never verified
`create_eval_cases_from_confirmed_flags` is named "from confirmed flags" but checks only
`flag.diagnosis_id` — never `diagnosis.confirmed` or `diagnosis.overridden`.

### W14. `override_category` is unvalidated
`api.ConfirmDiagnosisRequest.override_category: str | None` is written straight to the
DB column with no `FailureCategory` check; arbitrary strings persist and later fail
`EvalCase` validation.

### W15. No dedup on eval-case creation
`eval-cases` twice creates duplicate cases; `EvalCase` has no uniqueness constraint on
`(trace_id, failing_step)`.

### W16. Demo never verifies the failures it advertises
`cli.demo` collects `failure_step` and `failure_category` then never uses them, and
prints "Seeded failures in dataset: 5" without checking any fired. The
`3/15 classifications correct` figure is an artifact of the dry-run stub always
returning `CONTRACT` — not an evaluation.

### W17. `eval` and `eval-run` are byte-identical
Two CLI commands, same implementation.

### W18. `make seed` can never succeed and misdescribes the dataset
`cli.seed` always does `raise typer.Exit(code=1)` ("not yet implemented"), and both
`cli.seed` and `Makefile:41` say "50-document" while 15 exist on disk.

### W19. `make run-ui` launches a project that does not exist
```make
run-ui: ## frontend dev server (Phase 4; not scaffolded yet)
	cd frontend && npm run dev
```
`frontend/` contains only `README.md`. There is **no** make target for the actual UI
(`streamlit_app.py`).

### W20. `TraceStatus.DEGRADED` is unreachable in production
`traced_step` only ever writes `"success"` or `"failure"`, so the `degraded` branch in
`compute_trace_status` and the `elif span.status == "degraded"` branch in
`otel_export._emit_span` are dead. `analytics.py` docstring aside, nothing produces it.

### W21. `otel_export._emit_span` accepts an unused `exporter` parameter
Passed at both call sites, never referenced in the body.

### W22. `global_fidelity` is a lie by construction
`build_step_scores` sets `global_fidelity = 1.0 if global_pass else 0.0` — identical to
`local_fidelity` — and accepts a `document_text` argument it never uses (G8 below). The
local/global distinction the model documents promise does not exist.

### W23. `_span_payload` drops `args[0]` unconditionally
Correct for bound methods, but silently discards the first argument of any free function
that gets instrumented.

### W24. Validator ordering gives a misleading config error
`validate_judge_family_separation` runs before `validate_model_configs_complete`, so
empty families surface as "Judge model family '' matches pipeline family" instead of
"family is empty".

---

# SECURITY

### S1. `Settings` leaks the API key via `repr()` (Critical)
`openrouter_api_key: str = Field(...)` — not `SecretStr`. Reproduced:
`api_key appears in repr(settings)? True`. Any log line, traceback, or debug print that
reprs `Settings` emits the plaintext key.

### S2. API key can be written into trace files (Critical, latent)
`_span_payload` uses `json.dumps(..., default=str)`. If a `Settings` or client object
reaches a traced step's args, its `str()` — including the API key — is serialized into
`input_json` on disk. Reproduced: `str(Settings) written into span input_json contains
key? True`. The `_span_payload` docstring explicitly claims dropping `self` prevents
this; that claim is false. The protection is incidental (no step passes one today —
verified `API key in spans? False`) and `default=str` removes the barrier for any step
added later.

### S3. Classification prompt has no prompt-injection fence (High)
`_get_step_prompt("classification", ...)` interpolates entity values directly:
`fenced=False untrusted_notice=False raw_entity_interpolated=True`. Entity values are
LLM-derived from untrusted document text. `extraction` and `summarization` both apply
`_fence_document` + `UNTRUSTED_NOTICE`; `classification` applies neither, so a document
can steer classification through extracted text.

### S4. `judge_analyze` has no fence and an unbounded payload (Medium)
`Document: {document_text[:3000]}` plus `json.dumps(trace_data)` with no truncation —
the untrusted notice is absent and `trace_data` (full span outputs) is unbounded.

### S5. No authentication on any API endpoint (Medium)
`/traces`, `/flag`, `/diagnose`, `/diagnoses/{id}/confirm` are open, and
`FlagRequest.user_id` is client-supplied and trivially spoofed — so the "human confirmed"
signal that gates eval-case creation carries no integrity guarantee. Combined with W13
(never verified) and C6, the trust chain is entirely absent.

### S6. `data/cache/` is not gitignored (Medium)
`data/*.db` and `data/traces/*.json` are ignored; `data/cache/` and `data/spend.json` are
not. The response cache stores full prompts — which contain verbatim document text — so
a routine `git add -A` can commit sensitive corpus data and spending records.

### S7. Full document text is exported to OTel (Low, opt-in)
`otel_export._span_attributes` sets `gen_ai.prompt` (up to 4,000 chars) containing
document content. No-op unless an exporter is configured, but any collector receives the
corpus verbatim. No redaction hook.

---

# TESTS — why the bugs shipped

### T1. Zero coverage for all of Phases 4-6
Untested modules: `storage` (564), `cli` (301), `fake_llm` (207), `eval_simple` (225),
`api` (179) — **1,476 lines, no tests**. The 109 passing tests cover only
config/llm/models/tracing/analyzer/otel/pipeline.

### T2. The tests test the units, not the path
This is the single most important test finding.
`test_multiple_hallucinations_all_reported` (test_analyzer_phase3.py:656) asserts that
two hallucinated entities are both reported — by calling `check_entity_grounding()`
**directly**, bypassing `build_step_scores`. The production path collapses exactly those
two results into one. The test passes; the pipeline mis-classifies.

Every one of the 24 analyzer tests calls a `check_*` function or feeds a
single-result fixture. `run_deterministic_checks → build_step_scores → find_root_cause`
is never exercised with multiple same-named results — precisely the case that is broken
(W1, W12).

### T3. No test covers `finalize_trace` on a failed trace
Every status test asserts on `ctx.trace.status` *before* `finalize_trace` runs. The
`finalize_trace` tests use successful traces. C10 therefore has no coverage.

### T4. The DEGRADED parametrized test never uses the real path
`test_trace_status_derived_from_span_statuses` builds spans by hand
(`_span(f"s{i}", s)`), so it tests `compute_trace_status` in isolation. Combined with
W20 (degraded unreachable), the test passes for a value production cannot produce.

### T5. Tests write into the repository's real `data/` directory
`test_trace_totals_aggregate_span_tokens` (line 230) and
`test_finalize_trace_sets_completion_fields` (line 269) call
`finalize_trace(trace)` with no `traces_dir`, which defaults to `Path("data/traces")`.
Verified: trace JSON count in `data/traces` went **17 → 19** across a test run.
Non-hermetic, pollutes the working tree, order-dependent, and indistinguishable from
real demo output.

### T6. Misleading test names
`test_caught_failure_still_degrades_trace` asserts `TraceStatus.FAILURE`, not degraded.

---

# PROCESS / packaging

### G1. `make check` does not cover root-level files
`ruff check src tests`, `ruff format --check src tests`, `mypy packages=["forensics"]`.
`streamlit_app.py`, `demo.sh` and `docs/` are never linted or typechecked. This is why 17
lint errors accumulated in the Phase 4 UI and only appeared at pre-commit — after being
reported as verified.

### G2. `fake_llm.py` (207 lines) is dead code
Zero references outside its own file. It contains the `expected_type` / seeded-failure
injection support that would make the seeded-failure dataset reproducible, but it is
never wired into `Pipeline` — which is *why* the dataset's 5 seeded failures cannot fire
(C11/C12) and why the demo shows 3/15.

### G3. Five unimplemented stub modules ship in the wheel
`analyzer.py`, `eval.py`, `analytics.py`, `faults.py`, `taxonomy.py` — each a docstring
ending "Status: not implemented". `faults.py` is the significant one (W6).

### G4. `.DS_Store` is tracked despite being in `.gitignore`
Confirmed: 1 tracked instance. `.gitignore` does not untrack.

### G5. Hardcoded CWD-relative paths
`cli.run`, `cli.demo`, `cli.validate_dataset`, `api.diagnose_trace`,
`eval_simple.run_eval` all use `Path("dataset/...")` instead of `settings.dataset_dir`.
Running from another directory fails or silently 404s.

### G6. `update_eval_run` binds a raw `datetime` to sqlite
Relies on the default adapter deprecated in 3.12 and slated for removal; `requires-python`
pins 3.12, so this is a forward-compat landmine rather than a live bug.

### G7. `_detect_encoding`'s `UnicodeEncodeError` branch is dead
`text.encode("utf-8")` cannot raise for a `str`.

### G8. `deadlock`/unused-parameter smells in the analyzer
`build_step_scores(trace, check_results, document_text)` never uses `document_text`
(G8/W22). `_await_and_record(..., input_data)` receives and immediately `del`s it.

---

# Recommended fix order

1. **Eval loop** — C6, C7, C8, W9, W15. Five independent breaks in one path.
2. **Streamlit UI** — C1, C2, C3, C4. Four independent crashes.
3. **Trace status + analytics** — C10, W11. Failures currently reported as success.
4. **Dataset ground truth** — C11, C12, W18. Without this, the eval loop has nothing
   meaningful to measure.
5. **Secrets** — S1 (`SecretStr`), S2 (drop `default=str`), S6 (gitignore).
6. **Gates** — G1 (extend `make check` to root files) and T1/T2 (tests for
   `storage`/`eval_simple`/`api`, plus integration-path analyzer tests) **before any
   further feature work**. Everything above would have been caught by these two changes.
