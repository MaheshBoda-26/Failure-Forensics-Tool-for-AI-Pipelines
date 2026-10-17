"""Fault-injection harness — deterministic ground truth for the analyzer.

Corrupts a specific step's output in a controlled, documented way so the
analyzer can be scored objectively: step-localization accuracy, category
accuracy, and evidence quality.

This exists because the eval loop measures the analyzer, and without an
injector there is nothing to measure against: every observed failure would
come from wherever the model happened to go wrong. With faults, we *choose*
the failure, we know its category, and the analyzer's verdict becomes
checkable rather than asserted.

Each fault declares the `(step, category)` it is expected to produce. Those
expectations are the same pairs `analyzer_simple.reachable_failure_pairs()`
reports as reachable, so a fault can never encode a failure the analyzer
structurally cannot detect.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from forensics.models import (
    DocumentType,
    Trace,
)


@dataclass(frozen=True)
class Fault:
    """A deterministic corruption of one step's output.

    `step` and `expected_category` form the ground truth the analyzer's
    verdict is scored against.
    """

    name: str
    step: str
    expected_category: str
    apply: Callable[[dict[str, Any], str], dict[str, Any]]
    description: str


def _inject_hallucinated_entity(output: dict[str, Any], document_text: str) -> dict[str, Any]:
    """Add an entity whose value is absent from the source document.

    Expected: extraction_hallucination.
    """
    ghost = "__phantom_entity_not_in_source__"
    entities = list(output.get("entities", []))
    entities.append(
        {
            "value": ghost,
            "entity_type": "organization",
            "start_offset": 0,
            "end_offset": len(ghost),
            "confidence": 0.99,
        }
    )
    return {**output, "entities": entities}


def _inject_bad_date(output: dict[str, Any], document_text: str) -> dict[str, Any]:
    """Emit a date entity that does not match YYYY-MM-DD.

    Expected: prompt_failure (date format violation).
    """
    entities = list(output.get("entities", []))
    entities.append(
        {
            "value": "January 5th, 2024",
            "entity_type": "date",
            "start_offset": 0,
            "end_offset": 17,
            "confidence": 0.8,
        }
    )
    return {**output, "entities": entities}


def _inject_invalid_label(output: dict[str, Any], document_text: str) -> dict[str, Any]:
    """Classify as a type outside the DocumentType enum.

    Expected: prompt_failure when the enum rejects it (schema), which is the
    honest expectation: an out-of-enum label is a schema violation first and a
    misclassification second.
    """
    return {**output, "document_type": "purchase_order"}


def _inject_wrong_label(output: dict[str, Any], document_text: str) -> dict[str, Any]:
    """Classify as a valid but wrong DocumentType.

    A valid enum member, so `label_enum` and `prompt_schema` both pass; only
    the ground-truth check (`label_grounding`) can see it is wrong. The label
    is chosen to avoid a *different* type's keywords so the injection does not
    ALSO trip the propagation check — a fault must isolate one failure.

    The chosen label must also differ from the type already in the output. Most
    demo documents never name their own type ("NON-DISCLOSURE AGREEMENT" contains
    no `contract`), so picking "the first type absent from the text" could set the
    label to the very type that is correct — a silent no-op that scored the
    analyzer as missing a fault it was never given.

    Expected: misclassification (requires expected_type to be passed to the
    analyzer).
    """
    text = document_text.lower()
    present = {t.value for t in DocumentType if t.value in text}
    current = output.get("document_type")
    for candidate in ("contract", "invoice", "report", "correspondence", "mixed"):
        if candidate not in present and candidate != current:
            return {**output, "document_type": candidate}
    # Every type is either named in the text or is the current label; the
    # remaining ones are still valid enum members, so a mismatch is guaranteed as
    # long as the result differs from `current`.
    for candidate in ("contract", "invoice", "report", "correspondence", "mixed"):
        if candidate != current:
            return {**output, "document_type": candidate}
    return {**output, "document_type": DocumentType.REPORT.value}


def _inject_context_loss(output: dict[str, Any], document_text: str) -> dict[str, Any]:
    """Summarize without any of the grounded entities.

    Expected: context_loss.
    """
    return {
        **output,
        "summary": "This document concerns a business matter requiring attention.",
        "key_points": [],
    }


def _inject_propagation_error(output: dict[str, Any], document_text: str) -> dict[str, Any]:
    """Summarize using another document type's terminology.

    Every grounded entity from the extraction span is preserved, so the
    context-preservation check still passes and the only failure is that the
    summary describes a different document type than the classifier chose.

    Expected: propagation_error.
    """
    grounded = [
        token
        for token in ("Acme", "2024-01-15", "10,000", "$10,000", "Beta", "LLC")
        if token in document_text
    ]
    preserved = " ".join(grounded)
    return {
        **output,
        "summary": (
            f"This invoice bills the total amount due. Terms referenced: {preserved}. "
            "Payment is due within thirty days of the invoice date."
        ),
        "key_points": [f"References {preserved}", "Total due"],
    }


def _inject_schema_violation(output: dict[str, Any], document_text: str) -> dict[str, Any]:
    """Replace a required field with a wrong type.

    Expected: prompt_failure.
    """
    return {**output, "confidence": "high"}


# The fault catalogue. `step`/`expected_category` are the ground truth.
FAULTS: tuple[Fault, ...] = (
    Fault(
        name="hallucinated_entity",
        step="extraction",
        expected_category="extraction_hallucination",
        apply=_inject_hallucinated_entity,
        description="Entity value absent from the source document.",
    ),
    Fault(
        name="malformed_date",
        step="extraction",
        expected_category="prompt_failure",
        apply=_inject_bad_date,
        description="Date entity that violates the YYYY-MM-DD contract.",
    ),
    Fault(
        name="invalid_label",
        step="classification",
        expected_category="prompt_failure",
        apply=_inject_invalid_label,
        description="document_type outside the DocumentType enum.",
    ),
    Fault(
        name="wrong_label",
        step="classification",
        expected_category="misclassification",
        apply=_inject_wrong_label,
        description="Valid but incorrect DocumentType.",
    ),
    Fault(
        name="schema_violation",
        step="classification",
        expected_category="prompt_failure",
        apply=_inject_schema_violation,
        description="Required field present with the wrong type.",
    ),
    Fault(
        name="context_loss",
        step="summarization",
        expected_category="context_loss",
        apply=_inject_context_loss,
        description="Summary drops every grounded entity.",
    ),
    Fault(
        name="propagation_error",
        step="summarization",
        expected_category="propagation_error",
        apply=_inject_propagation_error,
        description="Summary adopts a different document type's terminology.",
    ),
)


def fault_by_name(name: str) -> Fault:
    for fault in FAULTS:
        if fault.name == name:
            return fault
    raise KeyError(f"Unknown fault: {name}. Known: {[f.name for f in FAULTS]}")


def inject(trace: Trace, fault: Fault, document_text: str) -> Trace:
    """Return a copy of `trace` with `fault` applied to its target span.

    The original trace is not mutated, so the same trace can be scored with
    several faults and against the clean baseline.
    """
    target = next((s for s in trace.spans if s.step_name == fault.step), None)
    if target is None:
        raise ValueError(f"Trace has no {fault.step!r} span to inject into")

    try:
        output = json.loads(target.output_json) if target.output_json else {}
    except json.JSONDecodeError:
        output = {}
    if not isinstance(output, dict):
        output = {}

    corrupted = fault.apply(output, document_text)
    new_json = json.dumps(corrupted)

    # Copy the trace and the target span; leave every other span untouched.
    new_spans = [
        s.model_copy(update={"output_json": new_json}) if s.span_id == target.span_id else s
        for s in trace.spans
    ]
    return trace.model_copy(update={"spans": new_spans})


def score_fault(
    trace: Trace, fault: Fault, document_text: str, expected_type: str | None = None
) -> dict[str, Any]:
    """Inject a fault, analyze the result, and score against ground truth.

    Returns step-localization accuracy and category accuracy for one fault.
    `expected_type` should be the dataset's ground-truth type so misclassification
    can be observed at all.
    """
    from forensics.analyzer_simple import analyze_trace

    corrupted = inject(trace, fault, document_text)
    diagnosis = analyze_trace(corrupted, document_text, expected_type)

    step_correct = diagnosis.root_cause_step == fault.step
    category_correct = diagnosis.category.value == fault.expected_category

    return {
        "fault": fault.name,
        "expected_step": fault.step,
        "actual_step": diagnosis.root_cause_step,
        "expected_category": fault.expected_category,
        "actual_category": diagnosis.category.value,
        "step_correct": step_correct,
        "category_correct": category_correct,
        "confidence": diagnosis.confidence,
        "evidence": diagnosis.evidence_chain,
    }


def score_all_faults(
    trace: Trace, document_text: str, expected_type: str | None = None
) -> dict[str, Any]:
    """Score every fault against a clean baseline trace."""
    results = [score_fault(trace, f, document_text, expected_type) for f in FAULTS]
    total = len(results)
    return {
        "total": total,
        "step_accuracy": sum(r["step_correct"] for r in results) / total if total else 0.0,
        "category_accuracy": (
            sum(r["step_correct"] and r["category_correct"] for r in results) / total
            if total
            else 0.0
        ),
        "results": results,
    }


__all__ = ["FAULTS", "Fault", "fault_by_name", "inject", "score_all_faults", "score_fault"]
