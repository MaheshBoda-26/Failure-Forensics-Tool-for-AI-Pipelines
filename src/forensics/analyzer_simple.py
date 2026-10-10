"""Deterministic backward trace analyzer — root cause localization without judge LLM.

Runs deterministic checks per span:
- Entity grounding: entity value must appear in source document
- Label in enum: classification must be a valid DocumentType
- Date regex: date entities must match YYYY-MM-DD pattern

First span with ANY failed check = root cause.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from forensics.models import (
    Diagnosis,
    DocumentType,
    FailureCategory,
    Span,
    StepScore,
    Trace,
)

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class CheckResult:
    """Result of a single deterministic check."""

    check_name: str
    passed: bool
    evidence: str
    step_name: str


def _parse_span_output(span: Span) -> dict[str, Any]:
    """Parse a span's serialized output as JSON data.

    Never use eval()/exec() here: output_json is LLM-controlled text, so
    evaluating it is arbitrary code execution triggered by model output or by
    anyone able to write a trace file.
    """
    if not span.output_json:
        return {}
    parsed = json.loads(span.output_json)
    if not isinstance(parsed, dict):
        raise ValueError(f"span {span.span_id} output is {type(parsed).__name__}, expected object")
    return parsed


def check_entity_grounding(span: Span, document_text: str) -> list[CheckResult]:
    """Check that all extracted entities appear in the source document."""
    results: list[CheckResult] = []

    if span.step_name != "extraction":
        return results

    try:
        output_data = _parse_span_output(span)
        entities_data = output_data.get("entities", [])
    except (ValueError, TypeError):
        msg = "Failed to parse extraction output"
        return [CheckResult("entity_grounding", False, msg, span.step_name)]

    for entity_data in entities_data:
        if isinstance(entity_data, dict):
            value = entity_data.get("value", "")
            entity_type = entity_data.get("entity_type", "")
        else:
            value = getattr(entity_data, "value", "")
            entity_type = getattr(entity_data, "entity_type", "")

        if not value:
            continue

        if value in document_text:
            msg = f"Entity '{value}' ({entity_type}) found in document"
            results.append(CheckResult("entity_grounding", True, msg, span.step_name))
        else:
            msg = f"Entity '{value}' ({entity_type}) NOT found in document (hallucination)"
            results.append(CheckResult("entity_grounding", False, msg, span.step_name))

    return results


def check_label_enum(span: Span) -> list[CheckResult]:
    """Check that classification output is a valid DocumentType."""
    results: list[CheckResult] = []

    if span.step_name != "classification":
        return results

    try:
        output_data = _parse_span_output(span)
        doc_type = output_data.get("document_type", "")
    except (ValueError, TypeError):
        msg = "Failed to parse classification output"
        return [CheckResult("label_enum", False, msg, span.step_name)]

    if not doc_type:
        msg = "Missing document_type in classification output"
        return [CheckResult("label_enum", False, msg, span.step_name)]

    try:
        DocumentType(doc_type)
        msg = f"Valid document type: {doc_type}"
        results.append(CheckResult("label_enum", True, msg, span.step_name))
    except ValueError:
        valid_types = [t.value for t in DocumentType]
        msg = f"Invalid document type: '{doc_type}' (valid: {valid_types})"
        results.append(CheckResult("label_enum", False, msg, span.step_name))

    return results


def check_date_regex(span: Span) -> list[CheckResult]:
    """Check that date entities match YYYY-MM-DD pattern."""
    results: list[CheckResult] = []

    if span.step_name != "extraction":
        return results

    try:
        output_data = _parse_span_output(span)
        entities_data = output_data.get("entities", [])
    except (ValueError, TypeError):
        msg = "Failed to parse extraction output"
        return [CheckResult("date_regex", False, msg, span.step_name)]

    for entity_data in entities_data:
        if isinstance(entity_data, dict):
            value = entity_data.get("value", "")
            entity_type = entity_data.get("entity_type", "")
        else:
            value = getattr(entity_data, "value", "")
            entity_type = getattr(entity_data, "entity_type", "")

        if entity_type.lower() == "date":
            if DATE_PATTERN.match(value):
                msg = f"Date '{value}' matches YYYY-MM-DD pattern"
                results.append(CheckResult("date_regex", True, msg, span.step_name))
            else:
                msg = f"Date '{value}' does NOT match YYYY-MM-DD pattern"
                results.append(CheckResult("date_regex", False, msg, span.step_name))

    return results


def run_deterministic_checks(trace: Trace, document_text: str) -> dict[str, list[CheckResult]]:
    """Run all deterministic checks on all spans in a trace."""
    all_results = {}

    for span in trace.spans:
        step_results = []

        step_results.extend(check_entity_grounding(span, document_text))
        step_results.extend(check_label_enum(span))
        step_results.extend(check_date_regex(span))

        if step_results:
            all_results[span.step_name] = step_results

    return all_results


def build_step_scores(
    trace: Trace, check_results: dict[str, list[CheckResult]], document_text: str
) -> list[StepScore]:
    """Build StepScore objects from check results."""
    step_scores = []

    for span in trace.spans:
        step_results = check_results.get(span.step_name, [])

        local_checks = {}
        local_pass = True
        global_pass = True
        evidence = []

        for result in step_results:
            local_checks[result.check_name] = result.passed
            if not result.passed:
                local_pass = False
                global_pass = False
            evidence.append(result.evidence)

        # Local fidelity: all checks for this step pass
        local_fidelity = 1.0 if local_pass else 0.0
        # Global fidelity: for now same as local (no propagation analysis in v0)
        global_fidelity = 1.0 if global_pass else 0.0

        step_scores.append(
            StepScore(
                step_name=span.step_name,
                local_fidelity=local_fidelity,
                global_fidelity=global_fidelity,
                local_pass=local_pass,
                global_pass=global_pass,
                evidence=evidence,
                deterministic_checks=local_checks,
            )
        )

    return step_scores


def find_root_cause(step_scores: list[StepScore]) -> tuple[str, FailureCategory, float, list[str]]:
    """Find the first span with any failed check = root cause."""
    for score in step_scores:
        if not score.local_pass:
            # Determine category from failed checks
            failed_checks = [
                name for name, passed in score.deterministic_checks.items() if not passed
            ]

            if "entity_grounding" in failed_checks:
                category = FailureCategory.EXTRACTION_HALLUCINATION
            elif "label_enum" in failed_checks:
                category = FailureCategory.MISCLASSIFICATION
            elif "date_regex" in failed_checks:
                category = FailureCategory.PROMPT_FAILURE  # date format issue
            else:
                category = FailureCategory.UNKNOWN

            confidence = 0.9 if category != FailureCategory.UNKNOWN else 0.5
            return score.step_name, category, confidence, score.evidence

    # No failures found
    return "none", FailureCategory.UNKNOWN, 1.0, ["All deterministic checks passed"]


def analyze_trace(trace: Trace, document_text: str) -> Diagnosis:
    """Analyze a trace and produce a root cause diagnosis."""
    # Run deterministic checks
    check_results = run_deterministic_checks(trace, document_text)

    # Build step scores
    step_scores = build_step_scores(trace, check_results, document_text)

    # Find root cause
    root_cause_step, category, confidence, evidence_chain = find_root_cause(step_scores)

    # Create diagnosis
    diagnosis = Diagnosis(
        diagnosis_id=f"diag-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        trace_id=trace.trace_id,
        root_cause_step=root_cause_step,
        category=category,
        confidence=confidence,
        evidence_chain=evidence_chain,
        step_scores=step_scores,
        analyzer_version="0.1.0-deterministic",
        model_id="deterministic-v0",
        provider="local",
        created_at=datetime.now(UTC),
    )

    return diagnosis


def analyze_trace_from_storage(trace_id: str, document_text: str) -> Diagnosis | None:
    """Analyze a trace loaded from storage."""
    from forensics.storage import Storage

    storage = Storage()
    trace = storage.get_trace(trace_id)
    if not trace:
        return None
    return analyze_trace(trace, document_text)
