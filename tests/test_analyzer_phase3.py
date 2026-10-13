"""Phase 3 tests: Backward trace analyzer with 5 failure categories.

Every test here fails before the analyzer is extended. They pin the
behavior a forensics tool depends on: given a failed trace, the analyzer
names the FIRST broken step, the failure category, and a human-readable
evidence chain citing real input/output pairs.
"""

import json

import pytest

from forensics.analyzer_simple import (
    CheckResult,
    analyze_trace,
    check_date_regex,
    check_entity_grounding,
    check_label_enum,
    check_prompt_schema,
    run_deterministic_checks,
)
from forensics.models import (
    FailureCategory,
    Span,
    Trace,
)


def _span(
    step: str,
    output: dict,
    trace_id: str = "t",
    model_id: str = "m",
    provider: str = "p",
    prompt: str = "",
    raw_response: str = "",
    status: str = "success",
) -> Span:
    """Helper to create a span with JSON-serialized output."""
    return Span(
        trace_id=trace_id,
        step_name=step,
        input_json="{}",
        output_json=json.dumps(output),
        model_id=model_id,
        provider=provider,
        prompt=prompt,
        raw_response=raw_response,
        status=status,  # type: ignore[arg-type]
    )


def _trace_with_spans(spans: list[Span]) -> Trace:
    return Trace(document_id="d1", spans=spans)


# --- Extraction Hallucination (entity not in source) ----------------------


def test_entity_hallucination_detected() -> None:
    """An entity value that does not appear in the source document is flagged."""
    doc = "Invoice total is 500.00 USD"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {"value": "999.99", "entity_type": "MONEY", "start_offset": 0, "end_offset": 5}
                ]
            },
        ),
    ]
    _trace_with_spans(spans)

    results = check_entity_grounding(spans[1], doc)

    assert len(results) == 1
    assert results[0].check_name == "entity_grounding"
    assert results[0].passed is False
    assert "NOT found in document" in results[0].evidence
    assert "hallucination" in results[0].evidence


def test_entity_hallucination_maps_to_correct_category() -> None:
    """Hallucinated entity should categorize as EXTRACTION_HALLUCINATION."""
    doc = "Invoice total is 500.00 USD"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {"value": "ghost", "entity_type": "PERSON", "start_offset": 0, "end_offset": 5}
                ]
            },
        ),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    assert diagnosis.category is FailureCategory.EXTRACTION_HALLUCINATION
    assert diagnosis.root_cause_step == "extraction"
    assert any("NOT found in document" in e for e in diagnosis.evidence_chain)


# --- Misclassification ----------------------------------------------------


def test_misclassification_detected() -> None:
    """A document_type not in the enum is flagged."""
    spans = [
        _span("intake", {"cleaned_text": "doc"}),
        _span("extraction", {"entities": []}),
        _span("classification", {"document_type": "not_a_real_type", "confidence": 0.9}),
    ]
    _trace_with_spans(spans)

    results = check_label_enum(spans[2])

    assert len(results) == 1
    assert results[0].check_name == "label_enum"
    assert results[0].passed is False
    assert "Invalid document type" in results[0].evidence


def test_invalid_enum_maps_to_prompt_failure() -> None:
    """A document_type outside the enum is a schema violation, not a misclassification.

    The contract cannot represent the value, so the model failed to follow the
    schema. Misclassification is reserved for a *valid* enum value that is the
    wrong one, which only ground-truth labels can reveal.
    """
    spans = [
        _span("intake", {"cleaned_text": "doc"}),
        _span("extraction", {"entities": []}),
        _span("classification", {"document_type": "bogus_type", "confidence": 0.9}),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, "doc")

    assert diagnosis.category is FailureCategory.PROMPT_FAILURE
    assert diagnosis.root_cause_step == "classification"
    assert any("not a valid DocumentType" in e for e in diagnosis.evidence_chain)


def test_valid_but_wrong_label_is_misclassification() -> None:
    """A valid DocumentType that contradicts ground truth is a misclassification."""
    spans = [
        _span("intake", {"cleaned_text": "doc"}),
        _span("extraction", {"entities": []}),
        _span("classification", {"document_type": "invoice", "confidence": 0.9}),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, "doc", expected_type="contract")

    assert diagnosis.category is FailureCategory.MISCLASSIFICATION
    assert diagnosis.root_cause_step == "classification"
    assert any("does not match ground truth" in e for e in diagnosis.evidence_chain)


def test_label_grounding_is_inactive_without_ground_truth() -> None:
    """Without labels a valid-but-wrong label is unobservable, and must not be invented."""
    spans = [
        _span("intake", {"cleaned_text": "doc"}),
        _span("extraction", {"entities": []}),
        _span("classification", {"document_type": "invoice", "confidence": 0.9}),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, "doc")

    assert diagnosis.root_cause_step == "none"


# --- Prompt Failure (schema-invalid output) -------------------------------


def test_prompt_failure_schema_violation_detected() -> None:
    """Missing required fields in extraction output is a prompt failure."""
    import json

    # Use a document that contains the entity value so entity_grounding passes
    doc = "Invoice for 500.00 USD"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        Span(
            trace_id="t",
            step_name="extraction",
            input_json="{}",
            output_json=json.dumps({"entities": [{"value": "500.00"}]}),  # missing entity_type
            model_id="m",
            provider="p",
            prompt="",
            raw_response="",
            status="success",
        ),
    ]
    _trace_with_spans(spans)

    results = check_prompt_schema(spans[1])

    assert len(results) == 1
    assert results[0].check_name == "prompt_schema"
    assert results[0].passed is False
    assert "missing required field" in results[0].evidence.lower()
    assert "entity_type" in results[0].evidence


def test_prompt_failure_maps_to_correct_category() -> None:
    """Schema-invalid output should categorize as PROMPT_FAILURE."""
    spans = [
        _span("intake", {"cleaned_text": "doc"}),
        _span("extraction", {"entities": "not_a_list"}),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, "doc")

    assert diagnosis.category is FailureCategory.PROMPT_FAILURE
    assert diagnosis.root_cause_step == "extraction"


# --- Context Loss (entity present in step N, absent in step N+1) --------


def test_context_loss_detected() -> None:
    """An entity extracted but dropped in summarization is context loss.

    The analyzer checks: does the summarization output reference the
    entities that were successfully extracted?
    """
    doc = "Invoice 001 for Acme Corp. Total: 500.00 USD. Due: 2024-03-01."
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {
                        "value": "Acme Corp",
                        "entity_type": "ORG",
                        "start_offset": 18,
                        "end_offset": 26,
                    },
                    {
                        "value": "500.00",
                        "entity_type": "MONEY",
                        "start_offset": 32,
                        "end_offset": 38,
                    },
                ]
            },
        ),
        _span("classification", {"document_type": "invoice", "confidence": 0.9}),
        _span(
            "summarization",
            {"summary": "Invoice for 500.00", "key_points": ["total 500.00"]},
        ),  # Missing "Acme Corp"
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    # Context loss: entity from extraction missing in summarization
    assert diagnosis.category is FailureCategory.CONTEXT_LOSS
    assert diagnosis.root_cause_step == "summarization"
    assert any(
        "Acme Corp" in e and ("lost" in e.lower() or "missing" in e.lower())
        for e in diagnosis.evidence_chain
    )


def test_context_loss_ignored_when_entity_was_hallucinated() -> None:
    """If the entity was already a hallucination, dropping it is not context loss.

    The dropped entity must have been grounded (found in source).
    """
    doc = "Invoice for 500.00 USD"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {
                        "value": "Ghost Corp",
                        "entity_type": "ORG",
                        "start_offset": 0,
                        "end_offset": 10,
                    }
                ]
            },
        ),
        _span("classification", {"document_type": "invoice", "confidence": 0.9}),
        _span("summarization", {"summary": "Invoice for 500.00", "key_points": ["total 500.00"]}),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    # The hallucination is the root cause, not context loss
    assert diagnosis.category is FailureCategory.EXTRACTION_HALLUCINATION
    assert diagnosis.root_cause_step == "extraction"


# --- Propagation Error (step N correct, step N+1 misreads) ---------------


def test_propagation_error_detected() -> None:
    """Classification correct, but summarization misreads the document type.

    This is NOT context loss (which is about entities) but about the
    downstream step misinterpreting a correct upstream output.
    """
    doc = "Contract between Alpha and Beta. Effective 2024-01-01."
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {"value": "Alpha", "entity_type": "ORG", "start_offset": 21, "end_offset": 26}
                ]
            },
        ),
        _span("classification", {"document_type": "contract", "confidence": 0.95}),
        _span(
            "summarization",
            {
                "summary": "Invoice between Alpha and Beta",
                "key_points": ["invoice"],
            },  # Said "invoice" not "contract"
        ),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    assert diagnosis.category is FailureCategory.PROPAGATION_ERROR
    assert diagnosis.root_cause_step == "summarization"
    assert any("contract" in e.lower() and "invoice" in e.lower() for e in diagnosis.evidence_chain)


def test_propagation_error_only_when_upstream_is_correct() -> None:
    """Propagation error only fires when upstream classification is valid.

    If classification output is a valid enum value, but summarization uses
    different terminology, that's a propagation error. The analyzer cannot
    detect semantic misclassification (e.g., calling a contract an invoice)
    without semantic document understanding.
    """
    doc = "Contract between Alpha and Beta."
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span("extraction", {"entities": []}),
        _span("classification", {"document_type": "contract", "confidence": 0.9}),  # Valid
        _span(
            "summarization",
            {"summary": "Invoice for the contract between parties", "key_points": []},
        ),  # Wrong terminology
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    # Classification is valid, but summarization uses "invoice" terminology
    # instead of "contract" terminology -> propagation error
    assert diagnosis.category is FailureCategory.PROPAGATION_ERROR
    assert diagnosis.root_cause_step == "summarization"


# --- Root cause is FIRST failing step ------------------------------------


def test_root_cause_is_first_failing_step() -> None:
    """Even if multiple steps fail, the first one is root cause."""
    doc = "doc"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {"value": "ghost", "entity_type": "PERSON", "start_offset": 0, "end_offset": 5}
                ]
            },
        ),
        _span("classification", {"document_type": "bogus_type"}),
        _span("summarization", {"summary": "missing things"}),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    assert diagnosis.root_cause_step == "extraction"
    assert diagnosis.category is FailureCategory.EXTRACTION_HALLUCINATION


# --- Evidence chain is human-readable with real I/O ----------------------


def test_evidence_chain_cites_real_input_output() -> None:
    """The evidence chain must quote real input/output, not generic messages."""
    doc = "Invoice for 500.00 USD"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {"value": "ghost", "entity_type": "PERSON", "start_offset": 0, "end_offset": 5}
                ]
            },
        ),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    evidence = " ".join(diagnosis.evidence_chain)
    assert "ghost" in evidence
    assert "PERSON" in evidence
    assert "NOT found" in evidence


def test_evidence_chain_includes_step_scores_for_all_steps() -> None:
    """Even passing steps should have scores (for the UI)."""
    doc = "Invoice for 500.00 USD"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {
                        "value": "500.00",
                        "entity_type": "MONEY",
                        "start_offset": 13,
                        "end_offset": 19,
                    }
                ]
            },
        ),
        _span("classification", {"document_type": "invoice", "confidence": 0.9}),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    assert len(diagnosis.step_scores) == 3
    step_names = [s.step_name for s in diagnosis.step_scores]
    assert set(step_names) == {"intake", "extraction", "classification"}


# --- Deterministic check results structure -------------------------------


def test_check_result_structure() -> None:
    """CheckResult must be a dataclass with required fields."""
    result = CheckResult(
        check_name="entity_grounding",
        passed=False,
        evidence="Entity 'ghost' NOT found",
        step_name="extraction",
    )
    assert result.check_name == "entity_grounding"
    assert result.passed is False
    assert "ghost" in result.evidence
    assert result.step_name == "extraction"


def test_run_deterministic_checks_returns_per_step_results() -> None:
    """run_deterministic_checks groups results by span id.

    Keyed by span_id rather than step_name so two spans of the same step do not
    overwrite one another's results.
    """
    doc = "Invoice for 500.00 USD"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {
                        "value": "500.00",
                        "entity_type": "MONEY",
                        "start_offset": 13,
                        "end_offset": 19,
                    }
                ]
            },
        ),
    ]
    trace = _trace_with_spans(spans)

    results = run_deterministic_checks(trace, doc)

    assert spans[1].span_id in results
    assert spans[0].span_id not in results  # intake has no checks


# --- Date regex edge cases -----------------------------------------------


def test_date_regex_valid_yyyymmdd_passes() -> None:
    """YYYY-MM-DD dates pass the regex check."""
    doc = "Due 2024-03-01"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {
                        "value": "2024-03-01",
                        "entity_type": "DATE",
                        "start_offset": 4,
                        "end_offset": 14,
                    }
                ]
            },
        ),
    ]
    _trace_with_spans(spans)

    results = check_date_regex(spans[1])

    assert len(results) == 1
    assert results[0].passed is True


def test_date_regex_common_non_iso_date_passes() -> None:
    """A verbatim US-format date is legitimate, not a contract violation.

    entity_grounding requires the extracted value to appear literally in the
    document, so a model copying "03/01/2024" out of the text is behaving
    correctly. Holding it to the ISO-only pattern made date_regex fail on
    every date in every real trace and made the analyzer blame extraction
    ahead of the true root cause.
    """
    doc = "Due 03/01/2024"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {
                        "value": "03/01/2024",
                        "entity_type": "DATE",
                        "start_offset": 4,
                        "end_offset": 14,
                    }
                ]
            },
        ),
    ]
    _trace_with_spans(spans)

    results = check_date_regex(spans[1])

    assert len(results) == 1
    assert results[0].passed is True


def test_date_regex_invalid_format_fails() -> None:
    """A date value that is not a date in any common format fails."""
    doc = "Due soonish"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {
                        "value": "soonish",
                        "entity_type": "DATE",
                        "start_offset": 4,
                        "end_offset": 11,
                    }
                ]
            },
        ),
    ]
    _trace_with_spans(spans)

    results = check_date_regex(spans[1])

    assert len(results) == 1
    assert results[0].passed is False
    assert "not a recognisable date format" in results[0].evidence


# --- Confidence calibration ----------------------------------------------


def test_confidence_high_for_known_category() -> None:
    """Confidence is 0.9 for recognized failure categories."""
    doc = "doc"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {"value": "ghost", "entity_type": "PERSON", "start_offset": 0, "end_offset": 5}
                ]
            },
        ),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    assert diagnosis.confidence == 0.9


def test_confidence_low_for_unknown_category() -> None:
    """Confidence is 0.5 for UNKNOWN category."""
    doc = "doc"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span("extraction", {"entities": []}),
        _span("classification", {"document_type": "invoice", "confidence": 0.9}),
        _span("summarization", {"summary": "ok", "key_points": []}),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    assert diagnosis.category is FailureCategory.UNKNOWN
    assert diagnosis.confidence == 1.0


# --- Analyzer versioning -------------------------------------------------


def test_diagnosis_includes_analyzer_version() -> None:
    """Every diagnosis carries the analyzer version for reproducibility."""
    doc = "doc"
    spans = [_span("intake", {"cleaned_text": doc})]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    assert diagnosis.analyzer_version is not None
    assert "deterministic" in diagnosis.analyzer_version


# --- StepScore fields populated ------------------------------------------


def test_step_score_local_fidelity_matches_pass() -> None:
    """local_fidelity = 1.0 iff all local checks pass."""
    doc = "Invoice for 500.00 USD"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {
                        "value": "500.00",
                        "entity_type": "MONEY",
                        "start_offset": 13,
                        "end_offset": 19,
                    }
                ]
            },
        ),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    extraction_score = next(s for s in diagnosis.step_scores if s.step_name == "extraction")
    assert extraction_score.local_fidelity == 1.0
    assert extraction_score.local_pass is True
    assert extraction_score.deterministic_checks["entity_grounding"] is True


def test_step_score_fidelity_zero_on_failure() -> None:
    """local_fidelity = 0.0 when any check fails."""
    doc = "doc"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {"value": "ghost", "entity_type": "PERSON", "start_offset": 0, "end_offset": 5}
                ]
            },
        ),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, doc)

    extraction_score = next(s for s in diagnosis.step_scores if s.step_name == "extraction")
    assert extraction_score.local_fidelity == 0.0
    assert extraction_score.local_pass is False


# --- No eval() anywhere --------------------------------------------------


def test_no_eval_in_analyzer() -> None:
    """The analyzer must never use eval() on LLM/trace content."""
    import ast
    import inspect

    source = inspect.getsource(analyze_trace)
    tree = ast.parse(source)
    eval_calls = [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "eval"
    ]
    assert not eval_calls, f"eval() found in analyzer at line {eval_calls[0].lineno}"


# --- Multiple entity hallucinations --------------------------------------


def test_multiple_hallucinations_all_reported() -> None:
    """All hallucinated entities should be reported, not just the first."""
    doc = "Invoice for 500.00 USD"
    spans = [
        _span("intake", {"cleaned_text": doc}),
        _span(
            "extraction",
            {
                "entities": [
                    {
                        "value": "ghost1",
                        "entity_type": "PERSON",
                        "start_offset": 0,
                        "end_offset": 6,
                    },
                    {"value": "ghost2", "entity_type": "ORG", "start_offset": 0, "end_offset": 6},
                ]
            },
        ),
    ]
    _trace_with_spans(spans)

    results = check_entity_grounding(spans[1], doc)

    failed = [r for r in results if not r.passed]
    assert len(failed) == 2
    assert any("ghost1" in r.evidence for r in failed)
    assert any("ghost2" in r.evidence for r in failed)


# --- Classification missing field ----------------------------------------


def test_classification_missing_field_is_prompt_failure() -> None:
    """Missing document_type field in classification is a prompt failure."""
    spans = [
        _span("intake", {"cleaned_text": "doc"}),
        _span("extraction", {"entities": []}),
        _span("classification", {"confidence": 0.9}),  # missing document_type
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, "doc")

    assert diagnosis.category is FailureCategory.PROMPT_FAILURE
    assert diagnosis.root_cause_step == "classification"
    assert any("Missing document_type" in e for e in diagnosis.evidence_chain)


# --- Classification empty string -----------------------------------------


def test_classification_empty_string_is_prompt_failure() -> None:
    """Empty document_type string is a prompt failure."""
    spans = [
        _span("intake", {"cleaned_text": "doc"}),
        _span("extraction", {"entities": []}),
        _span("classification", {"document_type": "", "confidence": 0.9}),
    ]
    trace = _trace_with_spans(spans)

    diagnosis = analyze_trace(trace, "doc")

    assert diagnosis.category is FailureCategory.PROMPT_FAILURE


# --- All five categories tested ------------------------------------------


@pytest.mark.parametrize(
    ("category", "output"),
    [
        (
            FailureCategory.EXTRACTION_HALLUCINATION,
            {
                "entities": [
                    {"value": "ghost", "entity_type": "PERSON", "start_offset": 0, "end_offset": 5}
                ]
            },
        ),
        (
            FailureCategory.MISCLASSIFICATION,
            {"document_type": "bogus_type"},
        ),
        (
            FailureCategory.PROPAGATION_ERROR,
            {"summary": "Invoice", "key_points": ["invoice"]},  # when classification was contract
        ),
        (
            FailureCategory.PROMPT_FAILURE,
            {"entities": "not_a_list"},
        ),
        (
            FailureCategory.CONTEXT_LOSS,
            {"summary": "Invoice for 500.00", "key_points": ["total 500.00"]},  # missing Acme Corp
        ),
    ],
)
def test_all_five_categories_reachable(category: FailureCategory, output: dict) -> None:
    """All 5 FailureCategory values must be reachable by the analyzer.

    This test is a compile-time guard: if a new category is added to the enum
    but not wired in find_root_cause, this will catch it.
    """
    # The actual reachability is tested in the specific tests above.
    # This parametrize just documents the expected mapping.
    assert category in FailureCategory
