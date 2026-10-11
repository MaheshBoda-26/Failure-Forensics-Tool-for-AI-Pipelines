"""Deterministic backward trace analyzer — root cause localization without judge LLM.

Runs deterministic checks per span:
- Entity grounding: entity value must appear in source document
- Label in enum: classification must be a valid DocumentType
- Date regex: date entities must match YYYY-MM-DD pattern
- Context preservation: entities from extraction must appear in summarization
- Propagation check: downstream steps must correctly read upstream outputs
- Schema validity: LLM outputs must conform to expected structure

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
    """Result of a single deterministic check.

    `check_name` is the *kind* of check, not a unique id: a step can legitimately
    produce many results of the same kind (one per entity). Aggregation therefore
    uses AND, never last-write-wins, so a verdict cannot depend on the order
    entities happen to appear in.
    """

    check_name: str
    passed: bool
    evidence: str
    step_name: str


def aggregate_checks(results: list[CheckResult]) -> dict[str, bool]:
    """Collapse check results to one verdict per check kind.

    Repeated kinds are combined with AND. Writing them into a dict instead
    (last-wins) made the category depend on entity order: with [bad, good] the
    grounded entity overwrote the hallucination and the step was reported as
    `unknown` instead of `extraction_hallucination`.
    """
    verdicts: dict[str, bool] = {}
    for result in results:
        verdicts[result.check_name] = verdicts.get(result.check_name, True) and result.passed
    return verdicts


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


def _extract_entities(output_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Safely extract entities list from output."""
    entities = output_data.get("entities", [])
    if not isinstance(entities, list):
        return []
    return entities


def _extract_string_field(output_data: dict[str, Any], field: str) -> str:
    """Safely extract a string field from output."""
    value = output_data.get(field, "")
    return str(value) if value is not None else ""


def check_entity_grounding(span: Span, document_text: str) -> list[CheckResult]:
    """Check that all extracted entities appear in the source document."""
    results: list[CheckResult] = []

    if span.step_name != "extraction":
        return results

    try:
        output_data = _parse_span_output(span)
        entities_data = _extract_entities(output_data)
    except (ValueError, TypeError):
        # Unparseable output is not evidence of hallucination; prompt_schema
        # reports it as a schema failure. Emitting a grounding failure here
        # outranked prompt_schema in find_root_cause and mislabelled every
        # malformed extraction as extraction_hallucination.
        return results

    for entity_data in entities_data:
        # All test data uses dicts; object branch is unreachable but kept for completeness
        value = entity_data.get("value", "")
        entity_type = entity_data.get("entity_type", "")

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
        doc_type = _extract_string_field(output_data, "document_type")
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
        entities_data = _extract_entities(output_data)
    except (ValueError, TypeError):
        # Schema failure, reported by check_prompt_schema.
        return results

    for entity_data in entities_data:
        # All test data uses dicts; object branch is unreachable but kept for completeness
        value = entity_data.get("value", "")
        entity_type = entity_data.get("entity_type", "")

        if entity_type.lower() == "date":
            if DATE_PATTERN.match(value):
                msg = f"Date '{value}' matches YYYY-MM-DD pattern"
                results.append(CheckResult("date_regex", True, msg, span.step_name))
            else:
                msg = f"Date '{value}' does NOT match YYYY-MM-DD pattern"
                results.append(CheckResult("date_regex", False, msg, span.step_name))

    return results


def check_context_preservation(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    """Check that entities from extraction appear in summarization.

    Context loss: an entity was correctly extracted and grounded in the source,
    but does not appear in the summarization output.
    """
    results: list[CheckResult] = []

    if span.step_name != "summarization":
        return results

    # Find the extraction span to get grounded entities
    extraction_span = next((s for s in trace.spans if s.step_name == "extraction"), None)
    if not extraction_span:
        return results

    try:
        extraction_data = _parse_span_output(extraction_span)
        extraction_entities = _extract_entities(extraction_data)
    except (ValueError, TypeError):
        return results

    # Grounded entities: those whose value appears in source document
    grounded_entities = []
    for entity_data in extraction_entities:
        # All test data uses dicts; object branch is unreachable
        value = entity_data.get("value", "")
        entity_type = entity_data.get("entity_type", "")

        if value and value in document_text:
            grounded_entities.append((value, entity_type))

    if not grounded_entities:
        return results  # No grounded entities to preserve

    # Check summarization output for these entities
    try:
        output_data = _parse_span_output(span)
        summary = _extract_string_field(output_data, "summary")
        key_points = output_data.get("key_points", [])
    except (ValueError, TypeError):
        return results

    # Search in both summary and key_points
    searchable_text = f"{summary} {' '.join(key_points)}"

    for value, entity_type in grounded_entities:
        if value in searchable_text:
            msg = f"Grounded entity '{value}' ({entity_type}) preserved in summarization"
            results.append(CheckResult("context_preservation", True, msg, span.step_name))
        else:
            msg = f"Grounded entity '{value}' ({entity_type}) LOST in summarization (context loss)"
            results.append(CheckResult("context_preservation", False, msg, span.step_name))

    return results


def check_propagation(span: Span, trace: Trace) -> list[CheckResult]:
    """Check that downstream steps correctly interpret upstream outputs.

    Propagation error: step N was correct, but step N+1 misread its output.
    Currently checks: classification → summarization (doc type consistency).

    Only triggers if summarization is substantial enough to reasonably
    contain document-type terminology, or if it uses OTHER type's terminology.
    """
    results: list[CheckResult] = []

    if span.step_name != "summarization":
        return results

    # Need both classification and summarization
    classification_span = next((s for s in trace.spans if s.step_name == "classification"), None)
    if not classification_span:
        return results

    try:
        class_data = _parse_span_output(classification_span)
        classified_type = _extract_string_field(class_data, "document_type")
    except (ValueError, TypeError):
        return results

    if not classified_type:
        return results

    try:
        output_data = _parse_span_output(span)
        summary = _extract_string_field(output_data, "summary")
        key_points = output_data.get("key_points", [])
    except (ValueError, TypeError):
        return results

    searchable_text = f"{summary} {' '.join(key_points)}".strip()

    # Only check propagation if the summarization has some substance
    # (more than a few words). A trivial "ok" summary can't be analyzed.
    word_count = len(searchable_text.split())
    if word_count < 3:
        return results

    type_keywords = {
        "contract": ["contract", "agreement", "party", "clause", "effective"],
        "invoice": ["invoice", "bill", "total", "due", "amount"],
        "report": ["report", "analysis", "findings", "section"],
        "correspondence": ["letter", "email", "memo", "dear", "sincerely"],
        "mixed": ["mixed", "multiple", "various"],
    }

    classified_lower = classified_type.lower()
    if classified_lower not in type_keywords:
        return results

    expected_keywords = type_keywords[classified_lower]

    # Strong signal: summary uses a DIFFERENT document type's keywords
    for other_type, other_keywords in type_keywords.items():
        if other_type == classified_lower:
            continue
        if any(kw in searchable_text.lower() for kw in other_keywords):
            msg = (
                f"Propagation error: classification='{classified_type}' but summarization uses "
                f"'{other_type}' terminology (context: {searchable_text[:120]})"
            )
            results.append(CheckResult("propagation", False, msg, span.step_name))
            return results

    # Weak signal: summary lacks expected terminology (only if substantial)
    if word_count >= 10 and not any(kw in searchable_text.lower() for kw in expected_keywords):
        msg = (
            f"Propagation error: classification='{classified_type}' but summarization lacks "
            f"expected terminology (context: {searchable_text[:120]})"
        )
        results.append(CheckResult("propagation", False, msg, span.step_name))

    return results


def check_prompt_schema(span: Span) -> list[CheckResult]:
    """Check that LLM outputs conform to expected schema.

    Prompt failure: schema-invalid output, missing required fields,
    or outputs that indicate the model ignored instructions.
    """
    results: list[CheckResult] = []

    if span.step_name == "extraction":
        try:
            output_data = _parse_span_output(span)
            entities = output_data.get("entities")
        except (ValueError, TypeError):
            msg = "Failed to parse extraction output (schema invalid)"
            return [CheckResult("prompt_schema", False, msg, span.step_name)]

        if not isinstance(entities, list):
            msg = f"Extraction 'entities' must be a list, got {type(entities).__name__}"
            return [CheckResult("prompt_schema", False, msg, span.step_name)]

        # Check each entity has required fields
        for i, entity in enumerate(entities):
            if not isinstance(entity, dict):
                msg = f"Entity {i} must be an object, got {type(entity).__name__}"
                return [CheckResult("prompt_schema", False, msg, span.step_name)]
            if "value" not in entity:
                msg = f"Entity {i} missing required field 'value'"
                return [CheckResult("prompt_schema", False, msg, span.step_name)]
            if "entity_type" not in entity:
                msg = f"Entity {i} missing required field 'entity_type'"
                return [CheckResult("prompt_schema", False, msg, span.step_name)]

    elif span.step_name == "classification":
        try:
            output_data = _parse_span_output(span)
        except (ValueError, TypeError):
            msg = "Failed to parse classification output (schema invalid)"
            return [CheckResult("prompt_schema", False, msg, span.step_name)]

        doc_type = output_data.get("document_type")
        if doc_type is None:
            msg = "Classification missing required field 'document_type'"
            return [CheckResult("prompt_schema", False, msg, span.step_name)]
        if not isinstance(doc_type, str) or not doc_type.strip():
            msg = "Classification 'document_type' must be a non-empty string"
            return [CheckResult("prompt_schema", False, msg, span.step_name)]

        confidence = output_data.get("confidence")
        if confidence is None:
            msg = "Classification missing required field 'confidence'"
            return [CheckResult("prompt_schema", False, msg, span.step_name)]
        if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
            msg = "Classification 'confidence' must be a number between 0 and 1"
            return [CheckResult("prompt_schema", False, msg, span.step_name)]

    elif span.step_name == "summarization":
        try:
            output_data = _parse_span_output(span)
        except (ValueError, TypeError):
            msg = "Failed to parse summarization output (schema invalid)"
            return [CheckResult("prompt_schema", False, msg, span.step_name)]

        summary = output_data.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            msg = "Summarization missing required field 'summary' (non-empty string)"
            return [CheckResult("prompt_schema", False, msg, span.step_name)]

        key_points = output_data.get("key_points")
        if not isinstance(key_points, list):
            msg = "Summarization 'key_points' must be a list"
            return [CheckResult("prompt_schema", False, msg, span.step_name)]

    return results


def run_deterministic_checks(trace: Trace, document_text: str) -> dict[str, list[CheckResult]]:
    """Run all deterministic checks on all spans in a trace.

    Keyed by `span_id`, not `step_name`: two spans of the same step (a retry, a
    fan-out) previously overwrote each other's results, and build_step_scores
    then handed both spans the same verdict regardless of their real outputs.
    """
    all_results: dict[str, list[CheckResult]] = {}

    for span in trace.spans:
        step_results: list[CheckResult] = []

        step_results.extend(check_entity_grounding(span, document_text))
        step_results.extend(check_label_enum(span))
        step_results.extend(check_date_regex(span))
        step_results.extend(check_context_preservation(span, trace, document_text))
        step_results.extend(check_propagation(span, trace))
        step_results.extend(check_prompt_schema(span))

        if step_results:
            all_results[span.span_id] = step_results

    return all_results


def build_step_scores(
    trace: Trace, check_results: dict[str, list[CheckResult]], document_text: str
) -> list[StepScore]:
    """Build StepScore objects from check results.

    Repeated check kinds are aggregated with AND via `aggregate_checks`, so the
    verdict for a step cannot depend on the order its checks were produced in.
    `document_text` is accepted for call-site symmetry but unused: global
    fidelity is not separated from local fidelity in v0 (see the limitations doc).
    """
    del document_text
    step_scores = []

    for span in trace.spans:
        step_results = check_results.get(span.span_id, [])

        local_checks = aggregate_checks(step_results)
        local_pass = all(local_checks.values()) if local_checks else True
        evidence = [result.evidence for result in step_results]

        # Local fidelity: all checks for this step pass.
        # Global fidelity: identical to local in v0 (no cross-step attribution).
        local_fidelity = 1.0 if local_pass else 0.0
        global_fidelity = 1.0 if local_pass else 0.0

        step_scores.append(
            StepScore(
                step_name=span.step_name,
                local_fidelity=local_fidelity,
                global_fidelity=global_fidelity,
                local_pass=local_pass,
                global_pass=local_pass,
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

            # Priority order: the most specific explanation of *this step*.
            #
            # `prompt_schema` first: an output we could not parse tells us nothing
            # about grounding, so any co-emitted content check is a downstream
            # symptom of the same malformed response, not an independent failure.
            if "prompt_schema" in failed_checks:
                category = FailureCategory.PROMPT_FAILURE
            elif "entity_grounding" in failed_checks:
                category = FailureCategory.EXTRACTION_HALLUCINATION
            elif "date_regex" in failed_checks:
                category = FailureCategory.PROMPT_FAILURE  # date format issue
            elif "label_enum" in failed_checks:
                category = FailureCategory.MISCLASSIFICATION
            elif "context_preservation" in failed_checks:
                category = FailureCategory.CONTEXT_LOSS
            elif "propagation" in failed_checks:
                category = FailureCategory.PROPAGATION_ERROR
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
    # diagnosis_id must be unique per call, not per second: two diagnoses of
    # different traces inside the same second collided on the PRIMARY KEY.
    diagnosis = Diagnosis(
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
