"""Tests for models module."""

import pytest
from pydantic import ValidationError

from forensics.models import (
    AnalyticsSnapshot,
    ClassificationOutput,
    Diagnosis,
    Document,
    DocumentType,
    Entity,
    EvalCase,
    EvalResult,
    EvalRun,
    ExtractionOutput,
    FailureCategory,
    Flag,
    IntakeOutput,
    PipelineOutput,
    Span,
    StepScore,
    SummaryOutput,
    Trace,
    TraceStatus,
)


def test_document_creation():
    """Document creates with auto-generated ID."""
    doc = Document(content="Test document content")
    assert doc.doc_id
    assert len(doc.doc_id) == 12
    assert doc.content == "Test document content"
    assert doc.type is None


def test_document_with_type():
    """Document can have a ground truth type."""
    doc = Document(content="Invoice #123", type=DocumentType.INVOICE)
    assert doc.type == DocumentType.INVOICE


def test_entity_valid_offsets():
    """Entity with valid start/end offsets."""
    entity = Entity(
        value="John Smith",
        entity_type="person",
        start_offset=10,
        end_offset=20,
    )
    assert entity.value == "John Smith"
    assert entity.start_offset == 10
    assert entity.end_offset == 20


def test_entity_invalid_offsets_fails():
    """Entity with end_offset < start_offset fails validation."""
    with pytest.raises(ValidationError, match="end_offset must be >= start_offset"):
        Entity(
            value="John Smith",
            entity_type="person",
            start_offset=20,
            end_offset=10,
        )


def test_intake_output():
    """IntakeOutput holds document and cleaned text."""
    doc = Document(content="Raw text")
    intake = IntakeOutput(document=doc, cleaned_text="Clean text", char_count=8)
    assert intake.document.doc_id == doc.doc_id
    assert intake.cleaned_text == "Clean text"
    assert intake.char_count == 8


def test_extraction_output():
    """ExtractionOutput holds list of entities."""
    entities = [
        Entity(value="John", entity_type="person", start_offset=0, end_offset=4),
        Entity(value="2024-01-15", entity_type="date", start_offset=10, end_offset=20),
    ]
    extraction = ExtractionOutput(entities=entities, raw_response="LLM response")
    assert len(extraction.entities) == 2
    assert extraction.raw_response == "LLM response"


def test_classification_output():
    """ClassificationOutput with confidence and all scores."""
    classification = ClassificationOutput(
        document_type=DocumentType.CONTRACT,
        confidence=0.95,
        all_scores={
            DocumentType.CONTRACT: 0.95,
            DocumentType.INVOICE: 0.03,
            DocumentType.REPORT: 0.02,
        },
    )
    assert classification.document_type == DocumentType.CONTRACT
    assert classification.confidence == 0.95
    assert classification.all_scores[DocumentType.CONTRACT] == 0.95


def test_summary_output():
    """SummaryOutput with key points."""
    summary = SummaryOutput(
        summary="This is a contract between parties.",
        key_points=["Party A agrees", "Party B agrees"],
    )
    assert summary.summary == "This is a contract between parties."
    assert len(summary.key_points) == 2


def test_pipeline_output():
    """PipelineOutput combines all step outputs."""
    doc = Document(content="Test")
    intake = IntakeOutput(document=doc, cleaned_text="Test", char_count=4)
    extraction = ExtractionOutput(entities=[])
    classification = ClassificationOutput(document_type=DocumentType.CONTRACT, confidence=0.9)
    summary = SummaryOutput(summary="Summary")

    pipeline = PipelineOutput(
        intake=intake,
        extraction=extraction,
        classification=classification,
        summarization=summary,
    )
    assert pipeline.intake.document.doc_id == doc.doc_id
    assert pipeline.classification.document_type == DocumentType.CONTRACT


def test_span_creation():
    """Span creates with required fields."""
    span = Span(
        trace_id="trace-123",
        step_name="extraction",
        input_json='{"text": "input"}',
        output_json='{"entities": []}',
        model_id="openai/gpt-4o-mini",
        provider="openai",
        prompt="Extract entities...",
        raw_response='{"entities": []}',
    )
    assert span.span_id
    assert span.trace_id == "trace-123"
    assert span.step_name == "extraction"
    assert span.status == TraceStatus.SUCCESS
    assert span.confidence == 3


def test_span_with_error():
    """Span can record an error."""
    span = Span(
        trace_id="trace-123",
        step_name="extraction",
        input_json='{"text": "input"}',
        output_json="",
        model_id="openai/gpt-4o-mini",
        provider="openai",
        prompt="Extract...",
        raw_response="",
        error="Rate limited",
        status=TraceStatus.FAILURE,
    )
    assert span.status == TraceStatus.FAILURE
    assert span.error == "Rate limited"


def test_trace_creation():
    """Trace creates with required fields."""
    trace = Trace(
        document_id="doc-123",
        pipeline_version="0.1.0",
        prompt_version="abc123",
    )
    assert trace.trace_id
    assert trace.document_id == "doc-123"
    assert trace.status == TraceStatus.SUCCESS
    assert trace.spans == []


def test_trace_with_spans():
    """Trace can hold multiple spans."""
    trace = Trace(
        document_id="doc-123",
        pipeline_version="0.1.0",
        prompt_version="abc123",
    )
    span1 = Span(
        trace_id=trace.trace_id,
        step_name="intake",
        input_json="{}",
        output_json="{}",
        model_id="openai/gpt-4o-mini",
        provider="openai",
        prompt="",
        raw_response="",
    )
    span2 = Span(
        trace_id=trace.trace_id,
        step_name="extraction",
        input_json="{}",
        output_json="{}",
        model_id="openai/gpt-4o-mini",
        provider="openai",
        prompt="",
        raw_response="",
    )
    trace.spans = [span1, span2]
    assert len(trace.spans) == 2


def test_step_score():
    """StepScore captures local and global fidelity."""
    score = StepScore(
        step_name="extraction",
        local_fidelity=0.9,
        global_fidelity=0.7,
        local_pass=True,
        global_pass=False,
        evidence=["Entity 'X' not in source"],
        deterministic_checks={"entity_grounding": False},
    )
    assert score.step_name == "extraction"
    assert score.local_fidelity == 0.9
    assert score.global_fidelity == 0.7
    assert score.local_pass is True
    assert score.global_pass is False


def test_diagnosis():
    """Diagnosis captures root cause analysis."""
    diagnosis = Diagnosis(
        trace_id="trace-123",
        root_cause_step="extraction",
        category=FailureCategory.EXTRACTION_HALLUCINATION,
        confidence=0.92,
        evidence_chain=["Entity 'John' not found in source document"],
        analyzer_version="0.1.0",
        model_id="google/gemini-1.5-pro",
        provider="google",
    )
    assert diagnosis.root_cause_step == "extraction"
    assert diagnosis.category == FailureCategory.EXTRACTION_HALLUCINATION
    assert diagnosis.confidence == 0.92
    assert diagnosis.confirmed is False
    assert diagnosis.overridden is False


def test_diagnosis_override():
    """Diagnosis can be overridden by human."""
    diagnosis = Diagnosis(
        trace_id="trace-123",
        root_cause_step="extraction",
        category=FailureCategory.EXTRACTION_HALLUCINATION,
        confidence=0.92,
        evidence_chain=[],
        analyzer_version="0.1.0",
        model_id="google/gemini-1.5-pro",
        provider="google",
        overridden=True,
        override_category=FailureCategory.MISCLASSIFICATION,
        override_reason="Actually the classification was wrong",
    )
    assert diagnosis.overridden is True
    assert diagnosis.override_category == FailureCategory.MISCLASSIFICATION


def test_flag():
    """Flag links to trace and optional diagnosis."""
    flag = Flag(
        trace_id="trace-123",
        user_id="user-456",
        note="Output looks wrong",
    )
    assert flag.trace_id == "trace-123"
    assert flag.user_id == "user-456"


def test_eval_case():
    """EvalCase generated from confirmed flag."""
    case = EvalCase(
        trace_id="trace-123",
        failing_step="extraction",
        bad_output={"entities": [{"value": "fake", "type": "person"}]},
        corrected_output={"entities": []},
        category=FailureCategory.EXTRACTION_HALLUCINATION,
        pipeline_version="0.1.0",
        prompt_version="abc123",
    )
    assert case.failing_step == "extraction"
    assert case.category == FailureCategory.EXTRACTION_HALLUCINATION


def test_eval_run():
    """EvalRun tracks a batch evaluation."""
    run = EvalRun(
        pipeline_version="0.1.0",
        prompt_versions={"extraction": "hash1", "classification": "hash2"},
    )
    assert run.run_id
    assert run.pipeline_version == "0.1.0"
    assert run.total_cases == 0
    assert run.passed_count == 0
    assert run.failed_count == 0


def test_eval_result():
    """EvalResult for a single case."""
    result = EvalResult(
        run_id="run-123",
        case_id="case-456",
        step_name="extraction",
        score=0.95,
        fixed=True,
    )
    assert result.score == 0.95
    assert result.fixed is True


def test_analytics_snapshot():
    """AnalyticsSnapshot for dashboard."""
    snapshot = AnalyticsSnapshot(
        total_traces=100,
        failure_rate=0.15,
        failures_by_category={FailureCategory.EXTRACTION_HALLUCINATION: 5},
        failures_by_step={"extraction": 8, "classification": 7},
    )
    assert snapshot.total_traces == 100
    assert snapshot.failure_rate == 0.15
    assert snapshot.failures_by_category[FailureCategory.EXTRACTION_HALLUCINATION] == 5
