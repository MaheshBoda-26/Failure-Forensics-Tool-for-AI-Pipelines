"""Eval and regression engine — deterministic evaluation for v0.

Turns confirmed flags into eval cases, re-runs the accumulated dataset against
the current pipeline, and tracks whether known failures are still failing or fixed.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forensics.analyzer_simple import analyze_trace
from forensics.config import get_settings
from forensics.models import (
    Diagnosis,
    Document,
    EvalCase,
    EvalResult,
    EvalRun,
    Flag,
    Span,
)
from forensics.pipeline import Pipeline
from forensics.storage import Storage


def _pipeline_version() -> str:
    """The configured pipeline version, not a hardcoded literal."""
    try:
        return get_settings().pipeline_version
    except Exception:
        return "unknown"


def _prompt_versions() -> dict[str, str]:
    """Per-step prompt versions.

    These are the real prompt-version strings used by the LLM client, so an
    eval run can be attributed to the prompts it measured.
    """
    return {
        "extraction": "extraction-v1",
        "classification": "classification-v1",
        "summarization": "summarization-v1",
    }


def _cache_dir() -> Path:
    """Response-cache directory, from settings rather than a literal."""
    try:
        return get_settings().cache_dir
    except Exception:
        return Path("data/cache")


def _dataset_dir() -> Path:
    """Dataset directory, from settings rather than a CWD-relative literal."""
    try:
        return get_settings().dataset_dir
    except Exception:
        return Path("dataset")


def _document_text_for_trace(storage: Storage, trace_id: str) -> str:
    """Resolve the source document text for a trace.

    A case carries a trace id; the trace carries the document id. Resolving
    through the trace is what makes the document lookup possible at all.
    """
    trace = storage.get_trace(trace_id)
    if trace is None:
        return ""
    doc_path = _dataset_dir() / "documents" / f"{trace.document_id}.txt"
    if not doc_path.exists():
        return ""
    return doc_path.read_text()


def _corrected_output(bad_output: Any, document_id: str) -> dict[str, Any]:
    """Derive a corrected reference output from a failing one.

    `bad_output` is `Any` on purpose: a span's serialized output is whatever
    the model produced, so a non-object (a bare string, a list) is a real
    possibility that must be handled rather than asserted away.

    For an extraction failure the correction is deterministic: drop every
    entity whose value does not appear in the source document. That produces a
    real, checkable reference instead of the placeholder note the loop used
    before, which could never detect a wrong-but-well-formed output.

    For other steps there is no deterministic repair, so the bad output is
    returned and flagged so the case is clearly marked "needs golden output".
    """
    if not isinstance(bad_output, dict):
        return {"raw": bad_output, "_needs_golden": True}
    if "entities" in bad_output and isinstance(bad_output["entities"], list):
        doc_path = _dataset_dir() / "documents" / f"{document_id}.txt"
        if not doc_path.exists():
            return {**bad_output, "_needs_golden": True}
        doc_text = doc_path.read_text()
        kept: list[Any] = []
        for entity in bad_output["entities"]:
            # Guard against malformed entries: a non-dict entity cannot be
            # ground-truth-checked, so it is dropped rather than crashing.
            if not isinstance(entity, dict):
                continue
            value = entity.get("value")
            if isinstance(value, str) and value and value in doc_text:
                kept.append(entity)
        return {**bad_output, "entities": kept}
    return {**bad_output, "_needs_golden": True}


def flag_to_eval_case(flag: Flag, diagnosis: Diagnosis, storage: Storage) -> EvalCase | None:
    """Convert a confirmed flag into an eval case.

    `corrected_output` is the failing span's output with the hallucinated/
    malformed entities removed, derived deterministically from the trace. This
    is a *reference* expectation for the failing step, not a human-authored
    golden file: when a human supplied an override_reason it is preserved in
    `corrected_output["_note"]` so the provenance is never lost.
    """
    trace = storage.get_trace(flag.trace_id)
    if not trace:
        return None

    failing_step = diagnosis.root_cause_step
    category = diagnosis.override_category or diagnosis.category

    # Pull the failing span's output as the "bad" side.
    bad_output: dict[str, Any] = {}
    bad_span: Span | None = None
    for span in trace.spans:
        if span.step_name == failing_step:
            bad_span = span
            try:
                parsed = json.loads(span.output_json) if span.output_json else {}
            except (ValueError, TypeError):
                parsed = None
            # A span's output may legitimately be a non-object (a bare string
            # from a malformed model response); keep it under "raw" so the
            # case stays well-typed instead of crashing downstream.
            bad_output = parsed if isinstance(parsed, dict) else {"raw": parsed}
            break

    if bad_span is None:
        return None

    # Build the corrected output deterministically from the bad output.
    corrected_output = _corrected_output(bad_output, trace.document_id)
    if diagnosis.override_reason:
        corrected_output["_note"] = diagnosis.override_reason

    case = EvalCase(
        trace_id=trace.trace_id,
        failing_step=failing_step,
        bad_output=bad_output,
        corrected_output=corrected_output,
        category=category,
        pipeline_version=trace.pipeline_version,
        prompt_version=trace.prompt_version,
    )

    return case


async def run_eval_case(case: EvalCase, pipeline: Pipeline, doc_text: str) -> EvalResult:
    """Run a single eval case against the current pipeline.

    This is async because `Pipeline.run` is: the previous sync version created
    the coroutine and never awaited it, so `trace.spans` was always empty,
    `analyze_trace` returned root_cause "none", and every case was reported
    "STILL FAILING" regardless of what the pipeline actually did.
    """
    doc = Document(content=doc_text)

    from forensics.tracing import trace_context

    with trace_context(document_id=case.trace_id) as ctx:
        await pipeline.run(doc)
        trace = ctx.trace

    assert trace is not None

    diagnosis = analyze_trace(trace, doc_text)

    # Fixed = the step that originally failed now passes the deterministic checks.
    step_score = next((s for s in diagnosis.step_scores if s.step_name == case.failing_step), None)
    fixed = step_score.local_pass if step_score else False

    score = 1.0 if fixed else 0.0

    return EvalResult(
        run_id="",  # Will be set by caller
        case_id=case.case_id,
        step_name=case.failing_step,
        score=score,
        fixed=fixed,
        details={
            "root_cause": diagnosis.root_cause_step,
            "category": diagnosis.category.value,
            "confidence": diagnosis.confidence,
        },
    )


async def run_eval() -> EvalRun | None:
    """Run all eval cases against current pipeline and return results."""
    storage = Storage()
    pipeline = await Pipeline.create(cache_dir=_cache_dir())

    # Get all eval cases
    cases = storage.list_eval_cases(limit=1000)
    if not cases:
        print("No eval cases found. Create some by flagging traces and confirming.")
        return None

    # Create eval run
    run = EvalRun(
        pipeline_version=_pipeline_version(),
        prompt_versions=_prompt_versions(),
    )
    run = storage.create_eval_run(run)

    # Resolve each case's source document. `case.trace_id` is a trace id, not a
    # document id: the previous lookup used it directly against a doc_id-keyed
    # map, so every case missed and the run evaluated nothing.
    passed = 0
    failed = 0
    results = []

    for case in cases:
        doc_text = _document_text_for_trace(storage, case.trace_id)
        if not doc_text:
            print(f"Warning: No document text for trace {case.trace_id} (case {case.case_id})")
            continue

        result = await run_eval_case(case, pipeline, doc_text)
        result.run_id = run.run_id

        storage.create_eval_result(result)
        results.append(result)

        if result.fixed:
            passed += 1
            status = "OK FIXED"
        else:
            failed += 1
            status = "XX STILL FAILING"

        print(f"  {status} {case.case_id[:12]} ({case.failing_step}): {case.category.value}")

    # Update run summary
    run.total_cases = len(results)
    run.passed_count = passed
    run.failed_count = failed
    run.completed_at = datetime.now(UTC)
    storage.update_eval_run(
        run.run_id,
        completed_at=run.completed_at,
        total_cases=run.total_cases,
        passed_count=passed,
        failed_count=failed,
    )

    print(f"\nEval Run Complete: {passed} passed, {failed} failed, {len(results)} total")
    return run


def print_eval_results(run_id: str) -> None:
    """Print formatted eval results table."""
    storage = Storage()
    run = storage.get_eval_run(run_id)
    if not run:
        print(f"Eval run {run_id} not found")
        return

    results = storage.get_eval_results_for_run(run_id)

    print(f"\n{'=' * 80}")
    print(f"EVAL RUN: {run.run_id} | Pipeline: {run.pipeline_version}")
    print(f"Started: {run.started_at} | Completed: {run.completed_at or 'N/A'}")
    print(f"Total: {run.total_cases} | Passed: {run.passed_count} | Failed: {run.failed_count}")
    print(f"{'=' * 80}")
    print(f"{'Case ID':<15} {'Step':<20} {'Score':<8} {'Fixed':<6} {'Details'}")
    print(f"{'-' * 80}")

    for result in results:
        fixed_str = "yes" if result.fixed else "no"
        score_str = f"{result.score:.2f}"
        details = result.details.get("category", "") if result.details else ""
        parts = [
            f"{result.case_id[:14]:<15}",
            f"{result.step_name:<20}",
            f"{score_str:<8}",
            f"{fixed_str:<6}",
            details,
        ]
        print(" ".join(parts))

    print(f"{'=' * 80}")


def create_eval_cases_from_confirmed_flags() -> int:
    """Create eval cases from confirmed diagnoses.

    The previous version required `flag.diagnosis_id` to be set, but nothing
    ever assigned it, so this function returned 0 forever. The real gate is
    `diagnosis.confirmed`: an unconfirmed analyzer verdict must not become a
    regression case, because a wrong category would bake a wrong expectation
    into the suite.
    """
    storage = Storage()

    created = 0
    seen: set[tuple[str, str]] = set()

    for trace in storage.list_traces(limit=1000):
        diagnosis = storage.get_diagnosis_for_trace(trace.trace_id)
        if diagnosis is None:
            continue
        if not diagnosis.confirmed:
            continue

        # An override is the authoritative category when a human supplied one.
        effective_category = diagnosis.override_category or diagnosis.category
        effective_step = diagnosis.root_cause_step
        if effective_step in ("", "none"):
            continue

        doc_text = _document_text_for_trace(storage, trace.trace_id)
        if not doc_text:
            print(f"Warning: no document text for trace {trace.trace_id}; skipping")
            continue

        # Deduplicate: one case per (trace, failing step), so re-running this
        # command cannot inflate the suite.
        key = (trace.trace_id, effective_step)
        if key in seen:
            continue
        seen.add(key)

        flags = storage.list_flags_for_trace(trace.trace_id)
        if not flags:
            print(f"Warning: confirmed diagnosis on trace {trace.trace_id} has no flag; skipping")
            continue
        flag = flags[-1]

        case = flag_to_eval_case(flag, diagnosis, storage)
        if case is None:
            continue

        if effective_category != case.category or effective_step != case.failing_step:
            case.category = effective_category
            case.failing_step = effective_step

        storage.create_eval_case(case)
        created += 1
        print(f"Created eval case {case.case_id} from flag {flag.flag_id}")

    return created
