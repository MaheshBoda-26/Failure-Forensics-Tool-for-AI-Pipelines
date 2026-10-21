"""Deterministic backward trace analyzer — root cause localization without judge LLM.

Runs deterministic checks per span:
- Entity grounding: entity value must appear in source document
- Label in enum: classification must be a valid DocumentType
- Date regex: date entities must match YYYY-MM-DD pattern
- Context preservation: entities from extraction must appear in summarization
- Propagation check: downstream steps must correctly read upstream outputs
- Schema validity: LLM outputs must conform to expected structure
- Label grounding: classification matches ground truth (when available)

First span with ANY failed check = root cause.

Checks are registered via the @check_plugin decorator in forensics.checks,
so developers can add custom domain checks in 10 lines of code.
"""

import contextlib
import difflib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from forensics.models import (
    Diagnosis,
    DocumentType,
    FailureCategory,
    Span,
    StepScore,
    Trace,
)

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# A real model copies date strings verbatim from the document ("April 15,
# 2024", "Q1 2024"), and entity_grounding requires that literal value. Holding
# those to the ISO-only pattern made date_regex fail on every date in every
# real trace, so the analyzer always blamed extraction as its first failure
# and never reached the actual root cause downstream. The contract check now
# fires only on values that are not a recognisable date in any common format.
DATE_FORMATS = (
    DATE_PATTERN,
    re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$"),
    re.compile(r"^\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}$"),
    re.compile(r"^[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}$"),
    re.compile(r"^(?:19|20)\d{2}$"),
    re.compile(r"^Q[1-4]\s+(?:19|20)\d{2}$"),
    re.compile(
        r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+(?:19|20)\d{2}$"
    ),
    re.compile(r"^(?:19|20)\d{2}-\d{2}$"),
    # Month-day without a year ("January 1") is what documents state when the
    # year is implied by context.
    re.compile(r"^[A-Za-z]{3,9}\.?\s+\d{1,2}$"),
    re.compile(r"^\d{1,2}\s+[A-Za-z]{3,9}\.?$"),
)

# Definitional vocabulary per document type, used by `check_propagation`.
# Each entry is `(pattern, conclusive)`: a conclusive marker stands on its own,
# weaker ones must appear at least twice before they count. Whole-word matching
# matters — `\bbill\b` does not fire on "billing", and `\breport\b` does not
# fire on "reported".
TYPE_MARKERS: dict[str, tuple[tuple[re.Pattern[str], bool], ...]] = {
    "contract": tuple(
        (re.compile(pattern, re.IGNORECASE), conclusive)
        for pattern, conclusive in (
            (r"\bcontracts?\b", True),
            (r"\bagreements?\b", True),
            (r"\bclauses?\b", False),
            (r"\bhereto\b", False),
            (r"\bherein\b", False),
            (r"\bindemnif\w*\b", False),
            (r"\btermination\b", False),
        )
    ),
    "invoice": tuple(
        (re.compile(pattern, re.IGNORECASE), conclusive)
        for pattern, conclusive in (
            (r"\binvoices?\b", True),
            (r"\breceipts?\b", True),
            (r"\bremittance\b", False),
            (r"\bsubtotal\b", False),
            (r"\bamount\s+due\b", False),
            (r"\bpayment\s+terms\b", False),
        )
    ),
    "report": tuple(
        (re.compile(pattern, re.IGNORECASE), conclusive)
        for pattern, conclusive in (
            (r"\breports?\b", True),
            (r"\bfindings\b", False),
            (r"\bmethodology\b", False),
            (r"\bexecutive\s+summary\b", False),
        )
    ),
    "correspondence": tuple(
        (re.compile(pattern, re.IGNORECASE), conclusive)
        for pattern, conclusive in (
            (r"\bletters?\b", True),
            (r"\bemails?\b", True),
            (r"\bmemo(?:randum)?s?\b", True),
            (r"\bdear\b", False),
            (r"\bsincerely\b", False),
            (r"\bregards\b", False),
        )
    ),
    # A `mixed` document legitimately mentions several shapes, so it is exempt
    # from single-type exclusivity; these markers exist only for the weak-signal
    # check on a document that was classified as `mixed`.
    "mixed": ((re.compile(r"\bmixed\b", re.IGNORECASE), False),),
}

# Built-in checks are registered by `forensics.checks` when it is first
# imported. That import is deliberately NOT done here at module scope:
# `checks` imports `CheckResult` and the built-in implementations from this
# module, so importing it here would re-create the import cycle that made the
# CLI, API and analyzer unimportable. The runner is imported lazily inside
# `run_deterministic_checks`, which is the first place it is actually needed.


def is_recognisable_date(value: str) -> bool:
    """True when a date entity is a date in a format a model legitimately emits."""
    return any(pattern.match(value) for pattern in DATE_FORMATS)


def normalize_for_match(value: str) -> str:
    """Collapse whitespace and case so two spellings of a value compare equal."""
    return " ".join(value.split()).casefold()


def text_contains_entity(value: str, text: str) -> bool:
    """True when `text` contains `value`, exactly or case/whitespace-insensitively.

    One grounding rule for extraction, context preservation, eval-case repair
    and the UI. Using a strict `value in text` in some of those places and the
    normalized form in others meant an entity that *passed* grounding was then
    dropped from the corrected output and reported lost from the summary.
    """
    if not value or not text:
        return False
    if value in text:
        return True
    return normalize_for_match(value) in normalize_for_match(text)


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


def _entity_str(entity: Any, key: str) -> str:
    """Read a string field off an entity, tolerating malformed model output.

    The model is asked for objects, but a hallucinated reply can contain `null`
    or a bare number in the array. Calling `.get()` on those raised
    `AttributeError` and took the whole check down; a non-string value cannot be
    a grounded entity, so it is treated as absent.
    """
    if not isinstance(entity, dict):
        return ""
    value = entity.get(key, "")
    return value if isinstance(value, str) else ""


def _join_strings(value: Any) -> str:
    """Join a list of strings, coercing and ignoring anything malformed."""
    if not isinstance(value, list):
        return ""
    return " ".join(item if isinstance(item, str) else str(item) for item in value)


def check_entity_grounding(span: Span, document_text: str) -> list[CheckResult]:
    """Check that all extracted entities appear in the source document.

    Uses exact substring match first, then falls back to case-insensitive
    whitespace-normalized match, and finally to difflib.SequenceMatcher
    with ratio >= 0.85 for minor formatting differences.
    """
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
        value = _entity_str(entity_data, "value")
        entity_type = _entity_str(entity_data, "entity_type")

        if not value:
            continue

        # Exact match (fast path)
        if value in document_text:
            msg = f"Entity '{value}' ({entity_type}) found in document"
            results.append(CheckResult("entity_grounding", True, msg, span.step_name))
            continue

        # Case-insensitive + whitespace-normalized match
        norm_value = " ".join(value.split())
        norm_doc = " ".join(document_text.split())
        if norm_value.lower() in norm_doc.lower():
            msg = f"Entity '{value}' ({entity_type}) found in document (case/whitespace normalized)"
            results.append(CheckResult("entity_grounding", True, msg, span.step_name))
            continue

        # Fuzzy match with SequenceMatcher (ratio >= 0.85)
        # Search for the best matching substring in the document
        matcher = difflib.SequenceMatcher(None, value.lower(), document_text.lower())
        best_ratio = matcher.ratio()

        # Also check substrings of the document for better fuzzy matching
        if best_ratio < 0.85 and len(value) > 3:
            # Slide a window over the document to find best match. `quick_ratio`
            # is a cheap upper bound on `ratio`, so windows that cannot possibly
            # reach 0.85 are skipped: the full ratio is O(n²) in the window, and
            # this loop runs once per entity per document.
            value_lower = value.lower()
            for i in range(len(document_text) - len(value) + 1):
                sub = document_text[i : i + len(value)]
                window_matcher = difflib.SequenceMatcher(None, value_lower, sub.lower())
                if window_matcher.quick_ratio() < 0.85:
                    continue
                ratio = window_matcher.ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    if best_ratio >= 0.999:
                        break

        if best_ratio >= 0.85:
            msg = (
                f"Entity '{value}' ({entity_type}) fuzzy-matched in document "
                f"(ratio: {best_ratio:.2f})"
            )
            results.append(CheckResult("entity_grounding", True, msg, span.step_name))
        else:
            msg = f"Entity '{value}' ({entity_type}) NOT found in document (hallucination)"
            results.append(CheckResult("entity_grounding", False, msg, span.step_name))

    return results


def check_label_grounding(span: Span, expected_type: str | None) -> list[CheckResult]:
    """Check classification against ground-truth labels, when available.

    A *valid* enum value can still be the wrong one, and no amount of
    self-consistency can detect that: the analyzer only sees the model's own
    output. Only the dataset's labels make misclassification observable, so
    this check is active exactly when a ground-truth type is supplied.

    Expected category: misclassification.
    """
    if span.step_name != "classification" or expected_type is None:
        return []

    try:
        output_data = _parse_span_output(span)
        actual = _extract_string_field(output_data, "document_type")
    except (ValueError, TypeError):
        return []

    if actual == expected_type:
        return [
            CheckResult(
                "label_grounding",
                True,
                f"Classification matches ground truth: {actual}",
                span.step_name,
            )
        ]

    return [
        CheckResult(
            "label_grounding",
            False,
            f"Classification '{actual}' does not match ground truth '{expected_type}' "
            "(misclassification)",
            span.step_name,
        )
    ]


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
        value = _entity_str(entity_data, "value")
        entity_type = _entity_str(entity_data, "entity_type")

        if entity_type.lower() == "date":
            if DATE_PATTERN.match(value):
                msg = f"Date '{value}' matches YYYY-MM-DD pattern"
                results.append(CheckResult("date_regex", True, msg, span.step_name))
            elif is_recognisable_date(value):
                msg = f"Date '{value}' is a well-formed non-ISO date"
                results.append(CheckResult("date_regex", True, msg, span.step_name))
            else:
                msg = f"Date '{value}' is not a recognisable date format"
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
        value = _entity_str(entity_data, "value")
        entity_type = _entity_str(entity_data, "entity_type")

        if value and text_contains_entity(value, document_text):
            grounded_entities.append((value, entity_type))

    if not grounded_entities:
        return results  # No grounded entities to preserve

    # Check summarization output for these entities
    try:
        output_data = _parse_span_output(span)
        summary = _extract_string_field(output_data, "summary")
    except (ValueError, TypeError):
        return results

    # Search in both summary and key_points. `key_points` is attacker-adjacent
    # model output: a `null` or a list holding a non-string raised `TypeError`
    # out of `' '.join(...)` and crashed the whole analysis.
    searchable_text = f"{summary} {_join_strings(output_data.get('key_points'))}"

    for value, entity_type in grounded_entities:
        if text_contains_entity(value, searchable_text):
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

    Matching is whole-word against *definitional* markers only. The previous
    version substring-matched generic words ("total", "amount", "due", "party",
    "section", "multiple", "various", "bill"), so any invoice summary mentioning
    an amount due, any report summary mentioning a section, and virtually every
    "mixed" document summary was reported as a propagation error.

    A single definitional noun ("invoice", "receipt", "report", "letter",
    "contract") is conclusive; weaker markers ("subtotal", "clause", "findings")
    only count when at least two distinct ones appear.
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
    except (ValueError, TypeError):
        return results

    searchable_text = f"{summary} {_join_strings(output_data.get('key_points'))}".strip()

    # Only check propagation if the summarization has some substance
    # (more than a few words). A trivial "ok" summary can't be analyzed.
    word_count = len(searchable_text.split())
    if word_count < 3:
        return results

    classified_lower = classified_type.lower()
    if classified_lower not in TYPE_MARKERS:
        return results

    # Strong signal: summary uses a DIFFERENT document type's terminology.
    # A "mixed" document legitimately mentions several shapes, so it is exempt
    # from single-type exclusivity.
    if classified_lower != "mixed":
        for other_type, markers in TYPE_MARKERS.items():
            if other_type in (classified_lower, "mixed"):
                continue
            conclusive = False
            weak_matches: set[str] = set()
            for pattern, is_conclusive in markers:
                if not pattern.search(searchable_text):
                    continue
                if is_conclusive:
                    conclusive = True
                    break
                weak_matches.add(pattern.pattern)
            if conclusive or len(weak_matches) >= 2:
                msg = (
                    f"Propagation error: classification='{classified_type}' but summarization "
                    f"uses '{other_type}' terminology (context: {searchable_text[:120]})"
                )
                results.append(CheckResult("propagation", False, msg, span.step_name))
                return results

    # Weak signal: a substantial summary with none of the expected terminology.
    # The word floor is deliberately high: a short summary is legitimately terse
    # and proves nothing about propagation.
    if word_count >= 15 and not any(
        pattern.search(searchable_text) for pattern, _ in TYPE_MARKERS[classified_lower]
    ):
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

        # A non-empty string outside the enum is a schema violation, not a
        # misclassification: the model emitted a value the contract cannot
        # represent. Validating it here also keeps label_enum for valid-but-wrong.
        try:
            DocumentType(doc_type)
        except ValueError:
            valid_types = [t.value for t in DocumentType]
            msg = (
                f"Classification document_type '{doc_type}' is not a valid "
                f"DocumentType (valid: {valid_types})"
            )
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


def run_deterministic_checks(
    trace: Trace, document_text: str, expected_type: str | None = None
) -> dict[str, list[CheckResult]]:
    """Run all deterministic checks on all spans in a trace.

    `expected_type` is the ground-truth document type from the dataset, when
    available. It enables the misclassification check; without it, a valid but
    wrong label is unobservable.

    Keyed by `span_id`, not `step_name`: two spans of the same step (a retry, a
    fan-out) previously overwrote each other's results, and build_step_scores
    then handed both spans the same verdict regardless of their real outputs.
    """
    all_results: dict[str, list[CheckResult]] = {}

    # Imported lazily: `checks` imports this module, so a module-level import
    # here would be a cycle. By the time a trace is analyzed the registry is
    # imported and the built-ins are registered.
    from forensics.checks import run_registered_checks

    for span in trace.spans:
        # Run all registered checks for this span's step
        step_results = run_registered_checks(span, trace, document_text, expected_type)

        if step_results:
            all_results[span.span_id] = step_results

    return all_results


def build_step_scores(
    trace: Trace, check_results: dict[str, list[CheckResult]], document_text: str
) -> list[StepScore]:
    """Build StepScore objects from check results.

    Repeated check kinds are aggregated with AND via `aggregate_checks`, so the
    verdict for a step cannot depend on the order its checks were produced in.
    Global fidelity accounts for upstream failures: a step that passes its own
    checks but consumes corrupted upstream output gets global_fidelity=0.0.
    """
    step_scores = []

    # Track if any upstream step has failed
    upstream_failed = False

    for span in trace.spans:
        step_results = check_results.get(span.span_id, [])
        evidence = [result.evidence for result in step_results]

        if span.status == "failure":
            # A step that raised produced no output worth checking, and no
            # registered check targets a crashed step, so `local_checks` would be
            # empty and `all(...)` would score the crash as a pass.
            local_checks: dict[str, bool] = {"span_status": False}
            local_pass = False
            evidence.append(f"Step raised an exception: {span.error or 'unknown error'}")
        else:
            local_checks = aggregate_checks(step_results)
            local_pass = all(local_checks.values()) if local_checks else True

        # Local fidelity: all checks for this step pass
        local_fidelity = 1.0 if local_pass else 0.0

        # Global fidelity: local fidelity AND no upstream failures
        # If upstream failed, this step's output is tainted even if locally correct
        global_pass = local_pass and not upstream_failed
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

        # Update upstream_failed for downstream steps
        if not local_pass:
            upstream_failed = True

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
            elif "date_regex" in failed_checks:
                # A malformed date is a formatting contract violation, not a
                # fabrication: the value may well be grounded in the source.
                category = FailureCategory.PROMPT_FAILURE
            elif "entity_grounding" in failed_checks:
                category = FailureCategory.EXTRACTION_HALLUCINATION
            elif "label_enum" in failed_checks or "label_grounding" in failed_checks:
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


def reachable_failure_pairs() -> set[tuple[str, str]]:
    """The (step, category) pairs the analyzer can actually emit.

    Maps each check name to the category it produces, then restricts to the
    steps that check inspects. The dataset's seeded ground truth must be
    reproducible against the analyzer, otherwise "category accuracy" measures
    the label definitions rather than model behaviour: two of the five seeded
    failures were seeded at steps where their category can never be produced.
    """
    # check name -> category produced by that check
    check_category = {
        "prompt_schema": FailureCategory.PROMPT_FAILURE,
        "entity_grounding": FailureCategory.EXTRACTION_HALLUCINATION,
        "date_regex": FailureCategory.PROMPT_FAILURE,
        "label_enum": FailureCategory.MISCLASSIFICATION,
        "label_grounding": FailureCategory.MISCLASSIFICATION,
        "context_preservation": FailureCategory.CONTEXT_LOSS,
        "propagation": FailureCategory.PROPAGATION_ERROR,
    }
    # which step each check inspects
    check_step = {
        "prompt_schema": {"extraction", "classification", "summarization"},
        "entity_grounding": {"extraction"},
        "date_regex": {"extraction"},
        "label_enum": {"classification"},
        "label_grounding": {"classification"},
        "context_preservation": {"summarization"},
        "propagation": {"summarization"},
    }
    pairs: set[tuple[str, str]] = set()
    for check, category in check_category.items():
        for step in check_step[check]:
            pairs.add((step, category.value))
    # UNKNOWN is reachable at any step.
    for step in check_step["prompt_schema"]:
        pairs.add((step, FailureCategory.UNKNOWN.value))
    return pairs


def analyze_trace(trace: Trace, document_text: str, expected_type: str | None = None) -> Diagnosis:
    """Analyze a trace and produce a root cause diagnosis.

    `expected_type` is the dataset's ground-truth document type. Supplying it
    enables misclassification detection; omitting it makes the analyzer purely
    self-consistency based.

    If JUDGE_ENABLED=true and (deterministic finds failure OR JUDGE_FORCE=true),
    the judge LLM runs for semantic analysis and may override the deterministic verdict.
    """
    from forensics.config import get_settings

    settings = get_settings()

    # Run deterministic checks
    check_results = run_deterministic_checks(trace, document_text, expected_type)

    # Build step scores
    step_scores = build_step_scores(trace, check_results, document_text)

    # Find root cause (deterministic)
    root_cause_step, category, confidence, evidence_chain = find_root_cause(step_scores)

    # Determine if judge should run
    deterministic_failed = root_cause_step != "none"
    judge_should_run = settings.judge_enabled and (deterministic_failed or settings.judge_force)

    judge_analysis = None
    analyzer_fallback = False

    if judge_should_run:
        try:
            # Import here to avoid circular dependency
            from forensics.llm import using_llm_client
            from forensics.models import JudgeAnalysis

            async def _run_judge() -> dict[str, Any] | None:
                # `create_llm_client` is a coroutine returning a client, not an
                # async context manager; entering it raised AttributeError, the
                # bare `except` swallowed it, and the judge silently never ran.
                async with using_llm_client() as client:
                    return await client.judge_analyze(
                        trace_data=trace.model_dump(mode="json"),
                        document_text=document_text,
                    )

            import asyncio

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                judge_payload = asyncio.run(_run_judge())
            else:
                # analyze_trace is sync but is called from async request
                # handlers; asyncio.run() inside a running loop raises. A worker
                # thread gets its own loop so the judge runs either way.
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    judge_payload = pool.submit(lambda: asyncio.run(_run_judge())).result()

            # judge_analyze returns whatever JSON the judge produced. Anything
            # that does not validate as a JudgeAnalysis is discarded rather than
            # read attribute-by-attribute off a dict, which used to raise and
            # disappear into the same bare except.
            judge_result: JudgeAnalysis | None = None
            if judge_payload:
                # model_id/provider are facts about the call, not the verdict, so
                # they are filled in here instead of being asked of the model.
                payload = {
                    "model_id": settings.judge_model.model_id,
                    "provider": settings.judge_model.provider,
                    **judge_payload,
                }
                with contextlib.suppress(PydanticValidationError):
                    judge_result = JudgeAnalysis.model_validate(payload)

            if judge_result:
                judge_analysis = judge_result
                # Judge overrides deterministic if it disagrees on root cause or category
                if (
                    judge_result.root_cause_step != root_cause_step
                    or judge_result.category != category
                ):
                    analyzer_fallback = True
                    root_cause_step = judge_result.root_cause_step
                    category = judge_result.category
                    confidence = judge_result.confidence
                    # Evidence must describe the step the judge blamed. Indexing
                    # `step_scores[0]` attached "intake" (the first span) to a
                    # failure in "summarization".
                    blamed_step = next(
                        (
                            score
                            for score in judge_result.step_scores
                            if score.step_name == judge_result.root_cause_step
                        ),
                        judge_result.step_scores[0] if judge_result.step_scores else None,
                    )
                    if blamed_step is not None:
                        evidence_chain = blamed_step.evidence

        except Exception:
            # Judge failure: log and fall back to deterministic
            pass

    # Create diagnosis. The judge's provenance is recorded whenever the judge
    # actually ran, not only when it overrode the deterministic verdict: an
    # agreement is still the judge's endorsement, and attributing it to
    # "deterministic-v0" threw that evidence away.
    analyzer_ver = f"{settings.analyzer_version}-deterministic"
    model_id = "deterministic-v0"
    provider_name = "local"
    if judge_analysis is not None:
        analyzer_ver += "+judge"
        model_id = settings.judge_model.model_id
        provider_name = settings.judge_model.provider

    diagnosis = Diagnosis(
        trace_id=trace.trace_id,
        root_cause_step=root_cause_step,
        category=category,
        confidence=confidence,
        evidence_chain=evidence_chain,
        step_scores=step_scores,
        analyzer_version=analyzer_ver,
        model_id=model_id,
        provider=provider_name,
        created_at=datetime.now(UTC),
        judge_analysis=judge_analysis,
        analyzer_fallback=analyzer_fallback,
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
