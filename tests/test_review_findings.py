"""Regression tests for the code-review findings.

Each test names the defect it pins. They are grouped the same way the review was:
security, backend logic, tracing/concurrency, and the client contracts that
changed.
"""

import asyncio
import json
import sqlite3
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from forensics import api
from forensics.analyzer_simple import analyze_trace
from forensics.auth import AuthManager
from forensics.config import ModelConfig, get_settings, reset_settings, validate_startup
from forensics.documents import (
    document_text_from_trace,
    read_document,
    resolve_document_text,
    store_document,
)
from forensics.eval_simple import flag_to_eval_case
from forensics.llm import (
    NIMClient,
    QuotaExhaustedError,
    SpendTracker,
    TransportError,
)
from forensics.models import (
    Document,
    DocumentType,
    EvalCase,
    ExtractionOutput,
    FailureCategory,
    Flag,
    Span,
    Trace,
)
from forensics.storage import Storage
from forensics.tracing import get_current_span, trace_context, traced_step

DOC = "Contract between Acme Corp and Beta LLC effective 2024-01-15 for $10,000."


# ─── Helpers ────────────────────────────────────────────────────────────────


def _span(trace: Trace, step: str, output: dict, *, status: str = "success") -> Span:
    return Span(
        trace_id=trace.trace_id,
        step_name=step,
        input_json="{}",
        output_json=json.dumps(output),
        model_id="test-model",
        provider="test",
        prompt="p",
        raw_response="r",
        status=status,  # type: ignore[arg-type]
    )


def _trace(
    *,
    document_id: str = "contract_001",
    extraction: dict | None = None,
    classification: dict | None = None,
    summarization: dict | None = None,
    tenant_id: str = "default",
) -> Trace:
    trace = Trace(document_id=document_id, tenant_id=tenant_id, spans=[])
    trace.spans.append(_span(trace, "intake", {"cleaned_text": DOC}))
    trace.spans.append(
        _span(
            trace,
            "extraction",
            extraction
            if extraction is not None
            else {"entities": [{"value": "Acme", "entity_type": "organization"}]},
        )
    )
    trace.spans.append(
        _span(
            trace,
            "classification",
            classification
            if classification is not None
            else {"document_type": "contract", "confidence": 0.9},
        )
    )
    trace.spans.append(
        _span(
            trace,
            "summarization",
            summarization
            if summarization is not None
            else {"summary": "Contract between Acme and Beta LLC.", "key_points": ["Acme"]},
        )
    )
    return trace


def _storage(tmp_path) -> Storage:  # type: ignore[no-untyped-def]
    return Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")


# ─── Security: document ids cannot traverse ─────────────────────────────────


def test_pipeline_request_rejects_a_traversing_doc_id() -> None:
    with pytest.raises(ValidationError):
        api.PipelineRunRequest(doc_id="../../secret", doc_text="hello")


def test_document_model_rejects_a_traversing_doc_id() -> None:
    with pytest.raises(ValidationError):
        Document(doc_id="../etc/passwd", content="hello")


def test_diagnose_never_reads_outside_the_documents_directory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trace carrying `../../secret` must 404, not leak the file's contents."""
    dataset = tmp_path / "dataset"
    (dataset / "documents").mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-CONTENT")

    monkeypatch.setenv("DATASET_DIR", str(dataset))
    reset_settings()

    storage = _storage(tmp_path)
    monkeypatch.setattr(api, "_storage", storage)
    trace = _trace(document_id="../../secret")
    # Drop the intake span so the fallback cannot mask a traversal attempt.
    trace.spans = trace.spans[1:]
    storage.save_trace(trace)

    with pytest.raises(HTTPException) as excinfo:
        api.diagnose_trace(trace.trace_id)
    assert excinfo.value.status_code == 404


def test_diagnose_resolves_a_document_uploaded_through_the_api(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`POST /pipeline/run` traces must stay diagnosable.

    The document used to be persisted nowhere, so every uploaded trace answered
    `404 Document not found` on `/diagnose` and was skipped by the eval loop.
    """
    storage = _storage(tmp_path)
    monkeypatch.setattr(api, "_storage", storage)

    stored_path = store_document("custom_doc_1", DOC)
    assert stored_path is not None and stored_path.is_file()
    assert read_document("custom_doc_1") == DOC

    trace = _trace(document_id="custom_doc_1")
    storage.save_trace(trace)

    diagnosis = api.diagnose_trace(trace.trace_id)
    assert diagnosis.trace_id == trace.trace_id


def test_source_text_falls_back_to_the_trace_intake_span() -> None:
    """A trace whose document file is gone is still analyzable."""
    trace = _trace(document_id="never_written_anywhere")
    assert document_text_from_trace(trace) == DOC
    assert resolve_document_text(trace) == DOC


def test_store_document_rejects_an_unsafe_id() -> None:
    assert store_document("../../evil", "text") is None


# ─── Security: API-key prefix collisions ────────────────────────────────────


def test_keys_with_a_long_configured_prefix_all_authenticate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A 12+-character prefix must not collapse every key onto one lookup row."""
    storage = _storage(tmp_path)
    # Longer than the old fixed 12-character slice, which made every key share
    # one prefix and only the first key minted ever authenticate.
    manager = AuthManager(storage, prefix="ffk_prod_env_", rounds=4)
    first_tenant = manager.create_tenant("first")
    second_tenant = manager.create_tenant("second")

    first_key, first_raw = manager.create_api_key(first_tenant.tenant_id)
    second_key, second_raw = manager.create_api_key(second_tenant.tenant_id)

    assert first_key.prefix != second_key.prefix
    assert manager.validate_api_key(first_raw) is not None
    assert manager.validate_api_key(second_raw) is not None


# ─── Security: PII configuration and storage ────────────────────────────────


def test_invalid_custom_pii_regex_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PII_CUSTOM_PATTERNS", '[{"name": "bad", "pattern": "("}]')
    reset_settings()
    with pytest.raises(ValueError, match="not a valid regex"):
        validate_startup()


def test_invalid_custom_pii_regex_does_not_crash_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redaction runs at the end of a run; a bad pattern must not lose the trace."""
    from forensics.pii import create_redactor_from_settings

    monkeypatch.setenv("PII_CUSTOM_PATTERNS", '[{"name": "bad", "pattern": "("}]')
    monkeypatch.setenv("PII_REDACTION_ENABLED", "true")
    reset_settings()

    redactor = create_redactor_from_settings()
    assert redactor.redact("a@b.com")[0] != "a@b.com"


def test_eval_case_payloads_are_redacted(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PII_REDACTION_ENABLED", "true")
    reset_settings()

    storage = _storage(tmp_path)
    # A case points at a trace, and the foreign key is enforced.
    trace = _trace()
    storage.save_trace(trace)
    case = EvalCase(
        trace_id=trace.trace_id,
        failing_step="extraction",
        category=FailureCategory.EXTRACTION_HALLUCINATION,
        pipeline_version="0.1.0",
        prompt_version="v1",
        bad_output={"email": "person@example.com"},
        corrected_output={"email": "person@example.com", "_note": "contact person@example.com"},
    )
    stored = storage.create_eval_case(case)

    serialized = json.dumps([stored.bad_output, stored.corrected_output])
    assert "person@example.com" not in serialized


def test_alert_webhook_rejects_a_link_local_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://169.254.169.254/latest/meta-data")
    reset_settings()
    with pytest.raises(ValueError, match="private/loopback"):
        validate_startup()


def test_cors_origins_are_never_a_wildcard() -> None:
    assert "*" not in get_settings().cors_origins


# ─── Backend: eval seeding, confirmation and tenant scoping ─────────────────


def test_eval_run_does_not_seed_cases_from_a_passing_trace(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`root_cause_step == "none"` is a pass, not a failure."""
    storage = _storage(tmp_path)
    monkeypatch.setattr(api, "_storage", storage)

    clean = _trace()
    storage.save_trace(clean)
    diagnosis = analyze_trace(clean, DOC)
    assert diagnosis.root_cause_step == "none"
    storage.create_diagnosis(diagnosis)

    result = asyncio.run(api.trigger_eval_run())

    assert result["status"] == "empty"
    assert result["run_id"] is None
    assert storage.list_flags_for_trace(clean.trace_id) == []
    reloaded = storage.get_diagnosis_for_trace(clean.trace_id)
    assert reloaded is not None and reloaded.confirmed is False


def test_rejecting_a_diagnosis_creates_no_flag_or_eval_case(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = _storage(tmp_path)
    monkeypatch.setattr(api, "_storage", storage)

    trace = _trace(extraction={"entities": [{"value": "__ghost__", "entity_type": "org"}]})
    storage.save_trace(trace)
    diagnosis = analyze_trace(trace, DOC, "contract")
    storage.create_diagnosis(diagnosis)

    updated = api.confirm_diagnosis(
        diagnosis.diagnosis_id, api.ConfirmDiagnosisRequest(confirmed=False)
    )

    assert updated.confirmed is False
    assert storage.list_flags_for_trace(trace.trace_id) == []
    assert storage.list_eval_cases(limit=100) == []


def test_override_values_reach_the_eval_case(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The case must be built from the row *after* the override was applied."""
    storage = _storage(tmp_path)
    monkeypatch.setattr(api, "_storage", storage)

    trace = _trace(extraction={"entities": [{"value": "__ghost__", "entity_type": "org"}]})
    storage.save_trace(trace)
    diagnosis = analyze_trace(trace, DOC, "contract")
    assert diagnosis.category is FailureCategory.EXTRACTION_HALLUCINATION
    storage.create_diagnosis(diagnosis)

    api.confirm_diagnosis(
        diagnosis.diagnosis_id,
        api.ConfirmDiagnosisRequest(
            confirmed=True,
            override_category=FailureCategory.CONTEXT_LOSS,
            override_reason="manual review says context loss",
        ),
    )

    cases = storage.list_eval_cases(limit=100)
    assert len(cases) == 1
    assert cases[0].category is FailureCategory.CONTEXT_LOSS
    assert cases[0].corrected_output["_note"] == "manual review says context loss"


def test_eval_case_lookup_is_tenant_scoped(tmp_path) -> None:  # type: ignore[no-untyped-def]
    storage = _storage(tmp_path)
    trace = _trace(tenant_id="tenant-a")
    storage.save_trace(trace)
    diagnosis = analyze_trace(trace, DOC, "contract")

    foreign_flag = Flag(trace_id=trace.trace_id, tenant_id="tenant-b")

    assert flag_to_eval_case(foreign_flag, diagnosis, storage) is None


# ─── Backend: analyzer precision ────────────────────────────────────────────


def test_contract_summary_mentioning_amount_due_is_not_a_propagation_error() -> None:
    """Generic business words used to trigger the propagation check."""
    trace = _trace(
        extraction={"entities": []},
        summarization={
            "summary": (
                "This contract records the total amount due of 500 USD owed by the "
                "party named in section 2."
            ),
            "key_points": ["amount due", "the parties"],
        },
    )
    diagnosis = analyze_trace(trace, DOC, "contract")

    assert diagnosis.root_cause_step == "none"
    assert diagnosis.category is FailureCategory.UNKNOWN


def test_mixed_document_is_exempt_from_single_type_exclusivity() -> None:
    trace = _trace(
        extraction={"entities": []},
        classification={"document_type": "mixed", "confidence": 0.8},
        summarization={
            "summary": "This mixed document combines a contract with an invoice.",
            "key_points": ["mixed"],
        },
    )
    diagnosis = analyze_trace(trace, DOC, "mixed")

    assert diagnosis.root_cause_step == "none"


def test_propagation_still_detects_definitional_terminology() -> None:
    trace = _trace(
        summarization={"summary": "Invoice between Acme and Beta", "key_points": ["invoice"]}
    )
    diagnosis = analyze_trace(trace, DOC, "contract")

    assert diagnosis.root_cause_step == "summarization"
    assert diagnosis.category is FailureCategory.PROPAGATION_ERROR


def test_context_preservation_ignores_case_differences() -> None:
    trace = _trace(
        extraction={"entities": [{"value": "Acme", "entity_type": "organization"}]},
        summarization={"summary": "ACME is the counterparty.", "key_points": []},
    )
    diagnosis = analyze_trace(trace, DOC, "contract")

    assert diagnosis.root_cause_step == "none"


# ─── Backend: fallback and spend tracking ───────────────────────────────────


def _primary_and_fallback() -> tuple[ModelConfig, ModelConfig]:
    primary = ModelConfig(
        model_id="test/primary",
        provider="test",
        family="primary",
        price_in_per_1m=0.0,
        price_out_per_1m=0.0,
    )
    fallback = ModelConfig(
        model_id="test/fallback",
        provider="test",
        family="fallback",
        price_in_per_1m=0.0,
        price_out_per_1m=0.0,
    )
    return primary, fallback


def test_run_with_fallback_retries_a_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    primary, fallback = _primary_and_fallback()
    client = NIMClient(dry_run=True)
    calls: list[str] = []

    async def fake_structured(model_config, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(model_config.model_id)
        if model_config.model_id == primary.model_id:
            raise TransportError("connection reset")
        return "fallback-response"

    monkeypatch.setattr(client, "structured", fake_structured)

    result = asyncio.run(client.run_with_fallback(primary, fallback, "prompt", ExtractionOutput))

    assert result == "fallback-response"
    assert calls == [primary.model_id, fallback.model_id]


def test_run_with_fallback_does_not_retry_an_exhausted_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, fallback = _primary_and_fallback()
    client = NIMClient(dry_run=True)
    calls: list[str] = []

    async def fake_structured(model_config, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(model_config.model_id)
        raise QuotaExhaustedError("worker quota spent", status_code=503)

    monkeypatch.setattr(client, "structured", fake_structured)

    with pytest.raises(QuotaExhaustedError):
        asyncio.run(client.run_with_fallback(primary, fallback, "prompt", ExtractionOutput))

    # The fallback shares the same exhausted worker quota; retrying only burns it.
    assert calls == [primary.model_id]


def test_spend_is_tracked_per_context_and_merged_globally(tmp_path) -> None:  # type: ignore[no-untyped-def]
    tracker = SpendTracker(10.0, 100.0, tmp_path / "spend.json")

    async def job(cost: float) -> float:
        token = tracker.begin_run()
        try:
            await asyncio.sleep(0)
            tracker.check_and_add(cost)
            await asyncio.sleep(0)
            return tracker.run_spend_usd
        finally:
            tracker.end_run(token)

    results = asyncio.run(_gather(job, 0.1, 0.3))
    first, second = results

    # Each task sees its own run total, not the sum of both.
    assert first == pytest.approx(0.1)
    assert second == pytest.approx(0.3)
    # The durable ledger records both.
    assert tracker.global_spend_usd == pytest.approx(0.4)


async def _gather(job, *costs: float) -> list[float]:  # type: ignore[no-untyped-def]
    return list(await asyncio.gather(*(job(cost) for cost in costs)))


# ─── Tracing: context lifetime and orphaned spans ───────────────────────────


def test_current_span_is_visible_during_a_step_and_cleared_after() -> None:
    seen: list[Span | None] = []

    @traced_step("step")
    def work() -> dict[str, bool]:
        seen.append(get_current_span())
        return {"ok": True}

    with trace_context(document_id="d1") as ctx:
        work()
        # After the step, no span is current: the old implementation left the
        # finished span in the context for the whole of the next step.
        assert get_current_span() is None

    assert seen and seen[0] is not None
    assert ctx.trace is not None
    assert [span.step_name for span in ctx.trace.spans] == ["step"]


def test_resaving_a_trace_replaces_its_spans(tmp_path) -> None:  # type: ignore[no-untyped-def]
    storage = _storage(tmp_path)
    trace = _trace()
    storage.save_trace(trace)

    for span in trace.spans:
        span.span_id = uuid.uuid4().hex[:12]
    storage.save_trace(trace)

    with sqlite3.connect(storage.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM spans WHERE trace_id = ?", (trace.trace_id,)
        ).fetchone()[0]
    assert count == len(trace.spans)


# ─── Analyzer: judge evidence attribution ───────────────────────────────────


def test_judge_override_takes_evidence_from_the_blamed_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first step score is usually `intake`, not the root cause."""
    from forensics import llm as llm_module
    from forensics.models import JudgeAnalysis

    judge_payload = {
        "step_scores": [
            {
                "step_name": "intake",
                "local_fidelity": 1.0,
                "global_fidelity": 1.0,
                "evidence": ["INTAKE-EVIDENCE"],
                "semantic_issues": [],
            },
            {
                "step_name": "summarization",
                "local_fidelity": 0.1,
                "global_fidelity": 0.1,
                "evidence": ["SUMMARY-EVIDENCE"],
                "semantic_issues": [],
            },
        ],
        "root_cause_step": "summarization",
        "category": "context_loss",
        "confidence": 0.66,
    }

    class _StubClient:
        async def judge_analyze(self, **_: object) -> dict[str, object]:
            return judge_payload

    class _StubContext:
        async def __aenter__(self) -> "_StubClient":
            return _StubClient()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(llm_module, "using_llm_client", lambda **_: _StubContext())
    monkeypatch.setenv("JUDGE_ENABLED", "true")
    reset_settings()

    trace = _trace(extraction={"entities": [{"value": "__ghost__", "entity_type": "org"}]})
    diagnosis = analyze_trace(trace, DOC, "contract")

    assert diagnosis.analyzer_fallback is True
    assert diagnosis.root_cause_step == "summarization"
    assert diagnosis.evidence_chain == ["SUMMARY-EVIDENCE"]
    assert isinstance(diagnosis.judge_analysis, JudgeAnalysis)


# ─── Contract: DocumentType is used by the request model ────────────────────


def test_pipeline_request_accepts_a_known_document_type() -> None:
    request = api.PipelineRunRequest(
        doc_id="doc_1", doc_text="hello", expected_type=DocumentType.CONTRACT
    )
    assert request.expected_type is DocumentType.CONTRACT
