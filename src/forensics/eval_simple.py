"""Eval and regression engine — deterministic evaluation for v0.

Turns confirmed flags into eval cases, re-runs the accumulated dataset against
the current pipeline, and tracks whether known failures are still failing or fixed.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forensics.analyzer_simple import analyze_trace
from forensics.models import Diagnosis, Document, EvalCase, EvalResult, EvalRun, Flag
from forensics.pipeline import Pipeline
from forensics.storage import Storage


def flag_to_eval_case(flag: Flag, diagnosis: Diagnosis, storage: Storage) -> EvalCase | None:
    """Convert a confirmed flag into an eval case."""
    trace = storage.get_trace(flag.trace_id)
    if not trace:
        return None

    # Find the failing step from diagnosis
    failing_step = diagnosis.root_cause_step
    category = diagnosis.category

    # Get the bad output from the failing span
    bad_output: dict[str, Any] = {}
    for span in trace.spans:
        if span.step_name == failing_step:
            try:
                bad_output = json.loads(span.output_json) if span.output_json else {}
            except Exception:
                bad_output = {"raw": span.output_json}
            break

    # Get corrected output from flag note or diagnosis evidence
    corrected_output: dict[str, Any] = {"note": flag.note or "Human corrected"}

    case = EvalCase(
        trace_id=trace.trace_id,
        failing_step=failing_step,
        bad_output=bad_output,
        corrected_output=corrected_output,
        category=category,
        pipeline_version=trace.pipeline_version,
        prompt_version=trace.prompt_version,
    )

    return storage.create_eval_case(case)


def run_eval_case(case: EvalCase, pipeline: Pipeline, doc_text: str) -> EvalResult:
    """Run a single eval case against the current pipeline."""
    # Create a document from the original trace
    doc = Document(content=doc_text)

    # Run pipeline
    from forensics.tracing import trace_context

    with trace_context(document_id=case.trace_id) as ctx:
        _ = pipeline.run(doc)
        # We need the trace for analysis
        trace = ctx.trace

    # Analyze the trace
    diagnosis = analyze_trace(trace, doc_text)

    # Check if the case is fixed
    # Fixed = the failing step now passes deterministic checks
    step_score = next((s for s in diagnosis.step_scores if s.step_name == case.failing_step), None)
    fixed = step_score.local_pass if step_score else False

    # Score: 1.0 if fixed, 0.0 if still failing
    score = 1.0 if fixed else 0.0

    eval_result = EvalResult(
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

    return eval_result


def run_eval() -> EvalRun | None:
    """Run all eval cases against current pipeline and return results."""
    storage = Storage()
    pipeline = Pipeline()

    # Get all eval cases
    cases = storage.list_eval_cases(limit=1000)
    if not cases:
        print("No eval cases found. Create some by flagging traces and confirming.")
        return None

    # Create eval run
    run = EvalRun(
        pipeline_version="0.1.0",
        prompt_versions={},  # Would come from settings
    )
    run = storage.create_eval_run(run)

    # Load labels for document content
    labels_path = Path("dataset/labels.json")
    with open(labels_path) as f:
        labels_data = json.load(f)

    doc_map = {}
    for doc_info in labels_data.get("documents", []):
        doc_path = Path(f"dataset/documents/{doc_info['doc_id']}.txt")
        if doc_path.exists():
            doc_map[doc_info["doc_id"]] = doc_path.read_text()

    # Run each case
    passed = 0
    failed = 0
    results = []

    for case in cases:
        doc_text = doc_map.get(case.trace_id, "")
        if not doc_text:
            print(f"Warning: No document text for {case.trace_id}")
            continue

        result = run_eval_case(case, pipeline, doc_text)
        result.run_id = run.run_id

        # Save result
        storage.create_eval_result(result)
        results.append(result)

        if result.fixed:
            passed += 1
            status = "✅ FIXED"
        else:
            failed += 1
            status = "❌ STILL FAILING"

        print(f"  {status} {case.case_id[:12]} ({case.failing_step}): {case.category.value}")

    # Update run summary
    run.total_cases = len(results)
    run.passed_count = passed
    run.failed_count = failed
    run.completed_at = datetime.now(UTC)
    storage.update_eval_run(
        run.run_id,
        completed_at=run.completed_at,
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
    print(f"{'Case ID':<15} {'Step':<20} {'Score':<8} {'Fixed':<8} {'Details'}")
    print(f"{'-' * 80}")

    for result in results:
        fixed_str = "✅" if result.fixed else "❌"
        score_str = f"{result.score:.2f}"
        details = result.details.get("category", "") if result.details else ""
        parts = [
            f"{result.case_id[:14]:<15}",
            f"{result.step_name:<20}",
            f"{score_str:<8}",
            f"{fixed_str:<8}",
            details,
        ]
        print(" ".join(parts))

    print(f"{'=' * 80}")


def create_eval_cases_from_confirmed_flags() -> int:
    """Create eval cases from all flags with diagnoses."""
    storage = Storage()

    # Get all traces and check for flags
    traces = storage.list_traces(limit=1000)
    created = 0

    for trace in traces:
        flags = storage.list_flags_for_trace(trace.trace_id)
        for flag in flags:
            if flag.diagnosis_id:
                # Need to analyze trace first
                doc_path = Path(f"dataset/documents/{trace.document_id}.txt")
                if not doc_path.exists():
                    continue
                doc_text = doc_path.read_text()
                diagnosis = analyze_trace(trace, doc_text)

                # Create eval case
                case = flag_to_eval_case(flag, diagnosis, storage)
                if case:
                    created += 1
                    print(f"Created eval case {case.case_id} from flag {flag.flag_id}")

    return created
