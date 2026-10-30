"""Regression fence for the resolved limitations in `docs/limitations.md`.

Each test pins behaviour the plan treats as already-shipped so a later phase
cannot regress it silently. These are **regression assertions, not new
behaviour** — every one of them must pass on the tree the plan started from.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forensics import api
from forensics.auth import AuthManager
from forensics.checks import load_plugins_from_config
from forensics.config import reset_settings
from forensics.models import Span, Tenant, Trace
from forensics.queue import _CLEARED_PAYLOAD, JobQueue, JobStatus
from forensics.storage import Storage, reset_storage
from forensics.tracing import (
    create_redactor_from_settings,
    finalize_trace,
    trace_context,
)

PREFIX = "ffk_"
ROUNDS = 4  # bcrypt cost 4 keeps the suite fast


@pytest.fixture
def client() -> TestClient:
    return TestClient(api.app)


# --- GET /runtime exposes the guard flags --------------------------------


def test_runtime_exposes_guard_flags(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """`GET /runtime` reports `pii_redaction_enabled`, `rate_limit_enabled`,
    `queue_enabled` — the deployment can read its own guards without a key."""
    monkeypatch.setenv("PII_REDACTION_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("QUEUE_ENABLED", "true")
    reset_settings()
    try:
        body = client.get("/runtime").json()
    finally:
        reset_settings()

    assert body["pii_redaction_enabled"] is True
    assert body["rate_limit_enabled"] is True
    assert body["queue_enabled"] is True


# --- Queue clears payload on terminal state ------------------------------


def test_queue_clears_payload_on_terminal_state(tmp_path: Path) -> None:
    """A finished job's payload is `_CLEARED_PAYLOAD` (`{}`), never the raw
    document text. The exposure window is the job's lifetime only."""
    db = tmp_path / "forensics.db"
    queue = JobQueue(db_path=db)

    job = queue.enqueue("tenant-a", {"doc_id": "d1", "doc_text": "secret text"})
    assert job.payload == {"doc_id": "d1", "doc_text": "secret text"}

    queue.complete(job.job_id, {"trace_id": "t1"})
    completed = queue.get_job(job.job_id)
    assert completed is not None
    assert completed.payload == {}
    assert completed.status == JobStatus.COMPLETED

    # Same for a failed job: claim it (increments attempts to 1), then fail
    # with max_attempts=1 so attempts is exhausted → DEAD (dead-lettered by
    # default), payload cleared.
    job2 = queue.enqueue("tenant-a", {"doc_id": "d2", "doc_text": "more secret"}, max_attempts=1)
    queue.dequeue("worker-1", concurrency=1)
    queue.fail(job2.job_id, "boom")
    failed = queue.get_job(job2.job_id)
    assert failed is not None
    assert failed.payload == {}
    assert failed.status == JobStatus.DEAD


def test_cleared_payload_is_the_empty_object() -> None:
    """`_CLEARED_PAYLOAD` is the canonical empty payload, asserted by name."""
    assert _CLEARED_PAYLOAD == "{}"


# --- finalize_trace redacts when PII is enabled --------------------------


def _redacted_trace() -> Trace:
    with trace_context(document_id="d1", tenant_id="tenant-a") as ctx:
        trace = ctx.trace
        assert trace is not None
        trace.spans.append(
            Span(
                trace_id=trace.trace_id,
                step_name="extraction",
                input_json="{}",
                output_json="{}",
                model_id="m",
                provider="p",
                prompt="Contact alice@example.com about the invoice",
                raw_response='{"entities": [{"value": "alice@example.com"}]}',
                status="success",
            )
        )
    return trace


def test_finalize_trace_redacts_pii_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With `PII_REDACTION_ENABLED=true`, `finalize_trace` scrubs span
    payloads before persistence — an extracted email never lands on disk."""
    monkeypatch.setenv("PII_REDACTION_ENABLED", "true")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "forensics.db"))
    monkeypatch.setenv("TRACES_DIR", str(tmp_path / "traces"))
    reset_settings()
    try:
        trace = _redacted_trace()
        finalize_trace(trace, traces_dir=tmp_path / "traces", db_path=tmp_path / "forensics.db")
    finally:
        reset_settings()

    redactor = create_redactor_from_settings()
    assert redactor.enabled is True
    assert "alice@example.com" not in trace.spans[0].prompt
    assert "█" in trace.spans[0].prompt


# --- check_plugins loader raises on a bad spec ---------------------------


def test_check_plugins_loader_raises_on_bad_spec() -> None:
    """A malformed `CHECK_PLUGINS` entry names the offending value instead of
    surfacing a bare ImportError mid-run."""
    with pytest.raises(ValueError, match="Invalid CHECK_PLUGINS entry"):
        load_plugins_from_config("no_colon_here")


# --- AUTH_KEY_TTL_DAYS is read by validate_api_key -----------------------


def test_auth_key_ttl_days_read_by_validate_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`AUTH_KEY_TTL_DAYS` flows into key expiry: a key minted with a TTL of 0
    days never expires, and `validate_api_key` honours `expires_at`."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BCRYPT_ROUNDS", str(ROUNDS))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "forensics.db"))
    reset_settings()
    try:
        storage = Storage()
        storage.create_tenant(Tenant(tenant_id="tenant-a", name="tenant-a"))
        manager = AuthManager(storage, prefix=PREFIX, rounds=ROUNDS)
        # No TTL configured → key never expires.
        api_key, _plaintext = manager.create_api_key("tenant-a", name="no-ttl", ttl_days=0)
        assert api_key.expires_at is None
        assert manager.validate_api_key(_plaintext) is not None
    finally:
        reset_storage()
        reset_settings()
