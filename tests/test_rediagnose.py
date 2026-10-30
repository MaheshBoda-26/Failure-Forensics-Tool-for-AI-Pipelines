"""Phase 2.2: re-diagnosis under PII redaction.

When `PII_REDACTION_ENABLED=true` the persisted trace spans are redacted, so
re-running the analyzer against them would flag every real entity as a
hallucination. The endpoint contract:

* stored diagnosis exists -> return it, `X-Diagnosis-Source: stored`
* no stored diagnosis, no `?allow_redacted=true` -> 409
* `?allow_redacted=true` -> fresh (degraded) analysis,
  `X-Diagnosis-Source: redacted-recompute`, `degraded_reason` set
* redaction off -> unchanged fresh analysis
"""

import pytest
from fastapi.testclient import TestClient

from forensics import api
from forensics.analyzer_simple import analyze_trace
from forensics.config import reset_settings
from forensics.models import Span, Trace
from forensics.storage import Storage

DOC = "Invoice INV-001 from Acme Corp, contact alice@example.com, total $10,000."


def _trace(storage: Storage, document_id: str = "invoice_001") -> Trace:
    trace = Trace(document_id=document_id, tenant_id="default", spans=[])
    trace.spans.append(
        Span(
            trace_id=trace.trace_id,
            step_name="intake",
            input_json="{}",
            output_json='{"cleaned_text": "invoice"}',
            model_id="m",
            provider="p",
            prompt="p",
            raw_response="r",
            status="success",
        )
    )
    trace.spans.append(
        Span(
            trace_id=trace.trace_id,
            step_name="extraction",
            input_json="{}",
            output_json='{"entities": [{"value": "alice@example.com", "entity_type": "email"}]}',
            model_id="m",
            provider="p",
            prompt="p",
            raw_response="r",
            status="success",
        )
    )
    trace.spans.append(
        Span(
            trace_id=trace.trace_id,
            step_name="classification",
            input_json="{}",
            output_json='{"document_type": "invoice", "confidence": 0.9}',
            model_id="m",
            provider="p",
            prompt="p",
            raw_response="r",
            status="success",
        )
    )
    trace.spans.append(
        Span(
            trace_id=trace.trace_id,
            step_name="summarization",
            input_json="{}",
            output_json='{"summary": "An invoice from Acme Corp.", "key_points": ["Acme"]}',
            model_id="m",
            provider="p",
            prompt="p",
            raw_response="r",
            status="success",
        )
    )
    storage.save_trace(trace)
    return trace


def _redacted_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PII_REDACTION_ENABLED", "true")
    reset_settings()


def _redacted_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PII_REDACTION_ENABLED", raising=False)
    reset_settings()


# --- Stored path: return the stored diagnosis --------------------------------


def test_stored_diagnosis_returned_with_stored_header(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _redacted_on(monkeypatch)
    try:
        storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
        monkeypatch.setattr(api, "_storage", storage)

        trace = _trace(storage)
        stored = analyze_trace(trace, DOC, "invoice")
        stored.tenant_id = "default"
        storage.create_diagnosis(stored)

        client = TestClient(api.app)
        response = client.post(f"/traces/{trace.trace_id}/diagnose")

        assert response.status_code == 200
        assert response.headers["X-Diagnosis-Source"] == "stored"
        body = response.json()
        assert body["root_cause_step"] == stored.root_cause_step
        assert body["category"] == stored.category.value
        assert body.get("degraded_reason") is None
    finally:
        reset_settings()


# --- 409: no stored diagnosis, redaction on, no override --------------------


def test_no_stored_diagnosis_returns_409(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _redacted_on(monkeypatch)
    try:
        storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
        monkeypatch.setattr(api, "_storage", storage)

        trace = _trace(storage)
        # Deliberately no stored diagnosis.

        client = TestClient(api.app)
        response = client.post(f"/traces/{trace.trace_id}/diagnose")

        assert response.status_code == 409
        body = response.json()
        assert "redacted" in body["detail"].lower()
        assert "allow_redacted" in body["detail"]
    finally:
        reset_settings()


# --- Forced path: allow_redacted=true ----------------------------------------


def test_allow_redacted_returns_degraded_marker(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _redacted_on(monkeypatch)
    try:
        storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
        monkeypatch.setattr(api, "_storage", storage)

        trace = _trace(storage)
        # Deliberately no stored diagnosis; force re-computation.

        client = TestClient(api.app)
        response = client.post(f"/traces/{trace.trace_id}/diagnose?allow_redacted=true")

        assert response.status_code == 200
        assert response.headers["X-Diagnosis-Source"] == "redacted-recompute"
        body = response.json()
        assert body.get("degraded_reason") is not None
        assert "redacted" in body["degraded_reason"].lower()
    finally:
        reset_settings()


# --- No redaction: unchanged fresh analysis ----------------------------------


def test_no_redaction_runs_fresh_analysis(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _redacted_off(monkeypatch)
    try:
        storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
        monkeypatch.setattr(api, "_storage", storage)

        trace = _trace(storage)

        client = TestClient(api.app)
        response = client.post(f"/traces/{trace.trace_id}/diagnose")

        assert response.status_code == 200
        assert response.headers.get("X-Diagnosis-Source") is None
        body = response.json()
        assert body.get("degraded_reason") is None
        assert body["root_cause_step"] in ("none", "extraction")
    finally:
        reset_settings()
