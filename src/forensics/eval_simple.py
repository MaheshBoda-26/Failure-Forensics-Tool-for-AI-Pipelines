"""Eval and regression engine — deterministic evaluation for v0.

Turns confirmed flags into eval cases, re-runs the accumulated dataset against
the current pipeline, and tracks whether known failures are still failing or fixed.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forensics.analyzer_simple import analyze_trace, text_contains_entity
from forensics.config import get_settings
from forensics.documents import resolve_document_text
from forensics.llm import using_llm_client
from forensics.models import (
    DEFAULT_TENANT_ID,
    Diagnosis,
    Document,
    EvalCase,
    EvalResult,
    EvalRun,
    Flag,
    Span,
    Trace,
)
from forensics.pipeline import Pipeline
from forensics.storage import get_storage
from forensics.storage_base import StorageBase


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


def document_ground_truth(document_id: str) -> str | None:
    """The labelled document type for `document_id`, when the dataset has one.

    One loader for every caller. `check_label_grounding` is the only check that
    can observe a valid-but-wrong classification, and it runs only when a
    ground-truth type is supplied, so any analysis path that omits this makes
    `misclassification` structurally undetectable — which is how a
    misclassification eval case came to be reported "OK FIXED".
    """
    labels_path = _dataset_dir() / "labels.json"
    try:
        labels = json.loads(labels_path.read_text())
    except (OSError, ValueError):
        return None
    for entry in labels.get("documents", []):
        if entry.get("doc_id") == document_id:
            value = entry.get("doc_type")
            return value if isinstance(value, str) else None
    return None


def _document_text_for_trace(
    storage: StorageBase, trace_id: str, tenant_id: str | None = None
) -> str:
    """Resolve the source document text for a trace.

    A case carries a trace id; the trace carries the document id. Resolving
    through the trace is what makes the document lookup possible at all, and
    `resolve_document_text` covers both the labelled dataset and documents
    uploaded through the API (which is why an uploaded trace is no longer
    silently skipped by the eval loop).
    """
    trace = storage.get_trace(trace_id, tenant_id=tenant_id)
    if trace is None:
        return ""
    return resolve_document_text(trace)


def _corrected_output(bad_output: Any, doc_text: str) -> dict[str, Any]:
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
        if not doc_text:
            return {**bad_output, "_needs_golden": True}
        kept: list[Any] = []
        for entity in bad_output["entities"]:
            # Guard against malformed entries: a non-dict entity cannot be
            # ground-truth-checked, so it is dropped rather than crashing.
            if not isinstance(entity, dict):
                continue
            value = entity.get("value")
            # Same grounding rule as the analyzer: exact, then normalized
            # case-insensitive. A case-sensitive comparison here dropped
            # entities that had *passed* extraction grounding, so the reference
            # output was wrong in the opposite direction.
            if isinstance(value, str) and value and text_contains_entity(value, doc_text):
                kept.append(entity)
        return {**bad_output, "entities": kept}
    return {**bad_output, "_needs_golden": True}


def flag_to_eval_case(flag: Flag, diagnosis: Diagnosis, storage: StorageBase) -> EvalCase | None:
    """Convert a confirmed flag into an eval case.

    `corrected_output` is the failing span's output with the hallucinated/
    malformed entities removed, derived deterministically from the trace. This
    is a *reference* expectation for the failing step, not a human-authored
    golden file: when a human supplied an override_reason it is preserved in
    `corrected_output["_note"]` so the provenance is never lost.
    """
    trace = storage.get_trace(flag.trace_id, tenant_id=flag.tenant_id)
    if not trace:
        return None

    failing_step = diagnosis.override_step or diagnosis.root_cause_step
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
    corrected_output = _corrected_output(bad_output, resolve_document_text(trace))
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
        tenant_id=flag.tenant_id,
    )

    return case


def _corrected_output_holds(case: EvalCase, trace: Trace) -> bool:
    """Whether the re-run complies with the case's deterministic reference.

    `corrected_output` is derived when the case is created (for an extraction
    failure: the bad entities with the hallucinated ones removed). It used to be
    computed and then never read, so a case could be reported fixed without
    anything checking the new output against the reference.
    """
    reference = case.corrected_output or {}
    if not isinstance(reference, dict) or reference.get("_needs_golden"):
        # No checkable reference was derivable; fall back to the analyzer only.
        return True

    expected_entities = reference.get("entities")
    if not isinstance(expected_entities, list):
        return True

    span = next((s for s in trace.spans if s.step_name == case.failing_step), None)
    if span is None:
        return False
    try:
        output = json.loads(span.output_json) if span.output_json else {}
    except (ValueError, TypeError):
        return False
    if not isinstance(output, dict):
        return False
    actual_entities = output.get("entities")
    if not isinstance(actual_entities, list):
        # A non-extraction step: the reference has nothing to compare here.
        return True

    allowed = {
        entity.get("value")
        for entity in expected_entities
        if isinstance(entity, dict) and isinstance(entity.get("value"), str)
    }
    for entity in actual_entities:
        if not isinstance(entity, dict) or not isinstance(entity.get("value"), str):
            return False
        if entity["value"] not in allowed:
            return False
    return True


async def run_eval_case(
    case: EvalCase,
    pipeline: Pipeline,
    doc_text: str,
    document_id: str = "",
    expected_type: str | None = None,
) -> EvalResult:
    """Run a single eval case against the current pipeline.

    This is async because `Pipeline.run` is: the previous sync version created
    the coroutine and never awaited it, so `trace.spans` was always empty,
    `analyze_trace` returned root_cause "none", and every case was reported
    "STILL FAILING" regardless of what the pipeline actually did.

    `document_id` is the *original* document the case came from. It used to be
    the case's `trace_id`, which is a trace id, not a document id, so the
    re-run trace carried a synthetic `document_id` and document-keyed behavior
    (the offline fixture, cache keys) stopped applying.

    `expected_type` is the dataset ground truth. Without it `analyze_trace`
    skips `check_label_grounding`, so a `misclassification` case passed its
    deterministic checks automatically and was reported fixed.
    """
    doc = Document(content=doc_text)
    document_id = document_id or case.trace_id

    from forensics.tracing import trace_context

    with trace_context(document_id=document_id, tenant_id=case.tenant_id) as ctx:
        await pipeline.run(doc, document_id=document_id)
        trace = ctx.trace

    assert trace is not None

    # `analyze_trace` does O(N·M) fuzzy matching, and the judge path blocks on a
    # thread pool. Running it inline in the event loop stalled every other task
    # (including the API's own requests when an eval ran in-process).
    diagnosis = await asyncio.to_thread(analyze_trace, trace, doc_text, expected_type)

    # Fixed = the originally failing step now passes *globally* and is no longer
    # any step's root cause. `local_pass` alone reported "OK FIXED" on runs where
    # an upstream step failed (so the step's own checks had nothing to check), on
    # judge/human-overridden verdicts, and without ever reading the case's
    # deterministic reference output.
    step_score = next((s for s in diagnosis.step_scores if s.step_name == case.failing_step), None)
    fixed = bool(step_score and step_score.global_pass)
    if fixed and diagnosis.root_cause_step == case.failing_step:
        fixed = False
    if fixed:
        fixed = _corrected_output_holds(case, trace)

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
            "global_pass": bool(step_score.global_pass) if step_score else False,
        },
        # The result belongs to whoever owns the case, not to whoever happened to
        # trigger the run.
        tenant_id=case.tenant_id,
    )


async def run_eval(tenant_id: str | None = None) -> EvalRun | None:
    """Run all eval cases against current pipeline and return results.

    `tenant_id=None` evaluates every case (CLI and dev mode); when a tenant is
    given, both the cases considered and the run record created are scoped to it.
    """
    storage = get_storage()

    # Get all eval cases
    cases = storage.list_eval_cases(limit=1000, tenant_id=tenant_id)
    if not cases:
        print("No eval cases found. Create some by flagging traces and confirming.")
        return None

    # Create eval run
    run = EvalRun(
        pipeline_version=_pipeline_version(),
        prompt_versions=_prompt_versions(),
        tenant_id=tenant_id or DEFAULT_TENANT_ID,
    )
    run = storage.create_eval_run(run)

    # Resolve each case's source document and re-run it. `using_llm_client`
    # guarantees the httpx pool is released: constructing the client without a
    # context manager leaked an open connection pool on every CLI eval, API
    # trigger and nightly scheduled run.
    async with using_llm_client(cache_dir=_cache_dir()) as client:
        passed, failed, results = await _evaluate_cases(
            storage, run, cases, Pipeline(client=client)
        )

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


async def _evaluate_cases(
    storage: StorageBase, run: EvalRun, cases: list[EvalCase], pipeline: Pipeline
) -> tuple[int, int, list[EvalResult]]:
    """Re-run every case against the current pipeline: (passed, failed, results).

    Each case's source document is resolved through its own trace. `case.trace_id`
    is a trace id, not a document id: the previous lookup used it directly
    against a doc-id-keyed map, so every case missed and the run evaluated
    nothing.
    """
    passed = 0
    failed = 0
    results: list[EvalResult] = []

    for case in cases:
        trace = storage.get_trace(case.trace_id, tenant_id=case.tenant_id)
        if trace is None:
            print(f"Warning: Trace {case.trace_id} is gone (case {case.case_id})")
            continue
        doc_text = _document_text_for_trace(storage, case.trace_id, tenant_id=case.tenant_id)
        if not doc_text:
            print(f"Warning: No document text for trace {case.trace_id} (case {case.case_id})")
            continue

        # Re-run against the document the case came from and judge against its
        # label, so a misclassification case is scored by the label-grounding
        # check rather than silently skipped.
        resolved_expected = (
            trace.expected_type.value if trace.expected_type else None
        ) or document_ground_truth(trace.document_id)
        result = await run_eval_case(
            case,
            pipeline,
            doc_text,
            document_id=trace.document_id,
            expected_type=resolved_expected,
        )
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

    return passed, failed, results


def print_eval_results(run_id: str) -> None:
    """Print formatted eval results table."""
    storage = get_storage()
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


def create_eval_cases_from_confirmed_flags(tenant_id: str | None = None) -> int:
    """Create eval cases from confirmed diagnoses.

    The previous version required `flag.diagnosis_id` to be set, but nothing
    ever assigned it, so this function returned 0 forever. The real gate is
    `diagnosis.confirmed`: an unconfirmed analyzer verdict must not become a
    regression case, because a wrong category would bake a wrong expectation
    into the suite.

    `tenant_id=None` sweeps every tenant (CLI and dev mode).
    """
    storage = get_storage()

    created = 0
    # Seed the dedupe set from what is already persisted. An in-memory set that
    # started empty on every call made each `eval-cases` invocation re-insert a
    # copy for every confirmed diagnosis, so the suite inflated on every run.
    seen: set[tuple[str, str]] = {
        (c.trace_id, c.failing_step)
        for c in storage.list_eval_cases(limit=100000, tenant_id=tenant_id)
    }

    for trace in storage.list_traces(limit=1000, tenant_id=tenant_id):
        diagnosis = storage.get_diagnosis_for_trace(trace.trace_id, tenant_id=tenant_id)
        if diagnosis is None:
            continue
        if not diagnosis.confirmed:
            continue

        # An override is the authoritative category when a human supplied one,
        # and likewise for the step: a human can name the failing step the
        # analyzer missed.
        effective_category = diagnosis.override_category or diagnosis.category
        effective_step = diagnosis.override_step or diagnosis.root_cause_step
        if effective_step in ("", "none"):
            continue

        doc_text = _document_text_for_trace(storage, trace.trace_id, tenant_id=tenant_id)
        if not doc_text:
            print(f"Warning: no document text for trace {trace.trace_id}; skipping")
            continue

        # Deduplicate: one case per (trace, failing step), so re-running this
        # command cannot inflate the suite.
        key = (trace.trace_id, effective_step)
        if key in seen:
            continue
        seen.add(key)

        flags = storage.list_flags_for_trace(trace.trace_id, tenant_id=tenant_id)
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
