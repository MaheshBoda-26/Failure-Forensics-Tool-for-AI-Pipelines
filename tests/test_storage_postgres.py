"""PostgreSQL backend: the parts of `StorageBase` that SQLite cannot prove.

`storage_postgres` shipped as an opt-in backend that had never been executed: no
test connected to a server, so three defects were invisible —

* the schema was never created (`__init__` left it to the caller), so the first
  query failed on a fresh database;
* the trace index was never written (`save_trace` only called `finalize_trace`,
  which writes a *SQLite* index), so `list_traces` returned nothing;
* every synchronous wrapper used `asyncio.run()`, which creates a new event loop
  per call, while the `asyncpg` pool stays bound to the loop that created it —
  so the second call reused connections from a closed loop.

These tests need a live server. Set `FORENSICS_TEST_POSTGRES_URL` and run
`make test-postgres`; without it the whole module skips, so the default suite
stays hermetic.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from forensics.models import (
    DEFAULT_TENANT_ID,
    Diagnosis,
    EvalCase,
    EvalResult,
    EvalRun,
    FailureCategory,
    Flag,
    Span,
    Tenant,
    Trace,
    TraceStatus,
)

PG_URL = os.environ.get("FORENSICS_TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not PG_URL,
        reason="set FORENSICS_TEST_POSTGRES_URL to run the PostgreSQL backend tests",
    ),
]


def _wipe(url: str) -> None:
    """Drop every table so each test starts from a database with no schema.

    That is the interesting case: the backend has to create its own schema on
    first use, which is exactly what used to be missing.
    """
    import asyncpg

    async def run() -> None:
        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            for row in rows:
                await conn.execute(f'DROP TABLE IF EXISTS "{row["tablename"]}" CASCADE')
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    _wipe(PG_URL)

    from forensics.storage_postgres import PostgresStorage

    st = PostgresStorage(traces_dir=tmp_path / "traces")
    try:
        yield st
    finally:
        st.close()


def _span(trace_id: str, step: str = "extraction") -> Span:
    return Span(
        trace_id=trace_id,
        step_name=step,
        input_json="{}",
        output_json="{}",
        model_id="test-model",
        provider="test",
        prompt="p",
        raw_response="r",
        latency_ms=120,
    )


def _trace(trace_id: str, tenant_id: str = DEFAULT_TENANT_ID) -> Trace:
    trace = Trace(trace_id=trace_id, document_id="doc_001", tenant_id=tenant_id)
    trace.spans = [_span(trace_id, "intake"), _span(trace_id)]
    return trace


def _diagnosis(trace_id: str, tenant_id: str = DEFAULT_TENANT_ID) -> Diagnosis:
    return Diagnosis(
        trace_id=trace_id,
        root_cause_step="extraction",
        category=FailureCategory.EXTRACTION_HALLUCINATION,
        confidence=0.8,
        analyzer_version="0.1.0",
        model_id="test-model",
        provider="test",
        tenant_id=tenant_id,
    )


# ─── Traces ─────────────────────────────────────────────────────────────────


def test_schema_is_created_on_first_use(storage: object) -> None:
    """A fresh database must work without a manual `_init_schema()` call."""
    st = storage
    assert st.list_traces() == []  # type: ignore[attr-defined]


def test_trace_round_trip_and_tenant_scoping(storage: object) -> None:
    st = storage
    st.save_trace(_trace("t_acme", "acme"))  # type: ignore[attr-defined]
    st.save_trace(_trace("t_default", DEFAULT_TENANT_ID))  # type: ignore[attr-defined]

    assert {t.trace_id for t in st.list_traces()} == {"t_acme", "t_default"}  # type: ignore[attr-defined]
    assert {t.trace_id for t in st.list_traces(tenant_id="acme")} == {"t_acme"}  # type: ignore[attr-defined]

    loaded = st.get_trace("t_acme")  # type: ignore[attr-defined]
    assert loaded is not None
    assert [span.step_name for span in loaded.spans] == ["intake", "extraction"]
    # Tenant scoping applies to point reads too, not just lists.
    assert st.get_trace("t_acme", tenant_id="other") is None  # type: ignore[attr-defined]
    assert st.load_trace("missing") is None  # type: ignore[attr-defined]


def test_resaving_a_trace_replaces_its_spans(storage: object) -> None:
    st = storage
    trace = _trace("t1")
    st.save_trace(trace)  # type: ignore[attr-defined]

    trace.spans = [_span("t1", "classification")]
    st.save_trace(trace)  # type: ignore[attr-defined]

    loaded = st.get_trace("t1")  # type: ignore[attr-defined]
    assert loaded is not None
    assert [span.step_name for span in loaded.spans] == ["classification"]


def test_latest_root_cause_steps_reads_the_latest_diagnosis(storage: object) -> None:
    st = storage
    st.save_trace(_trace("t1"))  # type: ignore[attr-defined]
    older = st.create_diagnosis(_diagnosis("t1"))  # type: ignore[attr-defined]
    newer = st.create_diagnosis(  # type: ignore[attr-defined]
        Diagnosis(
            trace_id="t1",
            root_cause_step="summarization",
            category=FailureCategory.CONTEXT_LOSS,
            confidence=0.7,
            analyzer_version="0.1.0",
            model_id="test-model",
            provider="test",
            created_at=older.created_at.replace(year=older.created_at.year + 1),
        )
    )

    steps = st.latest_root_cause_steps(["t1"])  # type: ignore[attr-defined]
    assert steps == {"t1": newer.root_cause_step}


# ─── Flags and diagnoses ────────────────────────────────────────────────────


def test_flag_and_diagnosis_round_trip(storage: object) -> None:
    st = storage
    st.save_trace(_trace("t1", "acme"))  # type: ignore[attr-defined]

    flag = st.create_flag(Flag(trace_id="t1", user_id="alice", tenant_id="acme"))  # type: ignore[attr-defined]
    assert st.get_flag(flag.flag_id, tenant_id="acme") is not None  # type: ignore[attr-defined]
    assert st.get_flag(flag.flag_id, tenant_id="other") is None  # type: ignore[attr-defined]
    assert [f.flag_id for f in st.list_flags_for_trace("t1", tenant_id="acme")] == [flag.flag_id]  # type: ignore[attr-defined]

    diagnosis = st.create_diagnosis(_diagnosis("t1", "acme"))  # type: ignore[attr-defined]
    fetched = st.get_diagnosis(diagnosis.diagnosis_id, tenant_id="acme")  # type: ignore[attr-defined]
    assert fetched is not None
    assert fetched.root_cause_step == "extraction"
    assert st.get_diagnosis_for_trace("t1", tenant_id="acme") is not None  # type: ignore[attr-defined]

    st.update_diagnosis_confirmation(  # type: ignore[attr-defined]
        diagnosis.diagnosis_id,
        confirmed=True,
        overridden=True,
        override_category=FailureCategory.MISCLASSIFICATION.value,
        override_reason="human review",
        override_step="classification",
    )
    updated = st.get_diagnosis(diagnosis.diagnosis_id)  # type: ignore[attr-defined]
    assert updated is not None
    assert updated.confirmed is True
    assert updated.override_category == FailureCategory.MISCLASSIFICATION


# ─── Eval data ──────────────────────────────────────────────────────────────


def test_eval_case_run_and_result_round_trip(storage: object) -> None:
    st = storage
    st.save_trace(_trace("t1", "acme"))  # type: ignore[attr-defined]

    case = st.create_eval_case(  # type: ignore[attr-defined]
        EvalCase(
            case_id="case_1",
            trace_id="t1",
            failing_step="extraction",
            bad_output={},
            corrected_output={},
            category=FailureCategory.EXTRACTION_HALLUCINATION,
            pipeline_version="0.1.0",
            prompt_version="test",
            tenant_id="acme",
        )
    )
    assert st.find_eval_case("t1", "extraction", tenant_id="acme") is not None  # type: ignore[attr-defined]
    assert st.find_eval_case("t1", "extraction", tenant_id="other") is None  # type: ignore[attr-defined]
    assert [c.case_id for c in st.list_eval_cases(tenant_id="acme")] == [case.case_id]  # type: ignore[attr-defined]

    run = st.create_eval_run(  # type: ignore[attr-defined]
        EvalRun(pipeline_version="0.1.0", prompt_versions={}, tenant_id="acme")
    )
    st.update_eval_run(run.run_id, total_cases=1, passed_count=1, failed_count=0)  # type: ignore[attr-defined]
    reloaded = st.get_eval_run(run.run_id, tenant_id="acme")  # type: ignore[attr-defined]
    assert reloaded is not None and reloaded.passed_count == 1
    assert [r.run_id for r in st.list_eval_runs(tenant_id="acme")] == [run.run_id]  # type: ignore[attr-defined]

    st.create_eval_result(  # type: ignore[attr-defined]
        EvalResult(
            run_id=run.run_id,
            case_id="case_1",
            step_name="extraction",
            score=1.0,
            fixed=True,
            details={"reason": "ok"},
            tenant_id="acme",
        )
    )
    results = st.get_eval_results_for_run(run.run_id, tenant_id="acme")  # type: ignore[attr-defined]
    assert len(results) == 1
    assert results[0].details == {"reason": "ok"}


# ─── Auth: tenants and keys ─────────────────────────────────────────────────


def test_tenants_and_api_keys_work_on_postgres(storage: object) -> None:
    """`AuthManager` used to reach into SQLite internals; it must work here."""
    from forensics.auth import AuthManager

    st = storage
    manager = AuthManager(st, prefix="ffk_", rounds=4)  # type: ignore[arg-type]

    tenant = manager.create_tenant("Acme")
    assert manager.get_tenant(tenant.tenant_id) is not None
    # The schema seeds a `default` tenant, so membership is what matters here.
    assert tenant.tenant_id in [t.tenant_id for t in manager.list_tenants()]

    api_key, plaintext = manager.create_api_key(tenant.tenant_id, scopes=["read"])
    assert api_key.scopes == ["read"]
    stored = manager.get_key(api_key.key_id)
    assert stored is not None and stored.scopes == ["read"]

    validated = manager.validate_api_key(plaintext)
    assert validated is not None and validated.key_id == api_key.key_id
    # A read-only key still authenticates; scope enforcement is the API's job.
    assert validated.allows("read") and not validated.allows("write")

    assert manager.revoke_key(api_key.key_id) is True
    assert manager.validate_api_key(plaintext) is None

    assert manager.set_tenant_active(tenant.tenant_id, False) is True
    assert manager.get_tenant(tenant.tenant_id) is None
    assert manager.get_tenant(tenant.tenant_id, active_only=False) is not None
    assert manager.set_tenant_active("missing", False) is False


def test_repeated_operations_reuse_one_event_loop(storage: object) -> None:
    """Two sequential calls must not hit a pool tied to a closed loop.

    Each synchronous wrapper used to call `asyncio.run()`, which creates and
    then closes a new loop while the pooled connections stayed bound to the
    first one — so the *second* operation failed with a closed-loop error.
    """
    st = storage
    for index in range(5):
        st.save_trace(_trace(f"t{index}"))  # type: ignore[attr-defined]
        assert st.get_trace(f"t{index}") is not None  # type: ignore[attr-defined]
    assert len(st.list_traces()) == 5  # type: ignore[attr-defined]


def test_factory_selects_postgres_from_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`get_storage()` must return the PostgreSQL backend when DATABASE_URL is set."""
    from forensics import storage as storage_module

    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("TRACES_DIR", str(tmp_path / "traces"))
    storage_module.reset_storage()
    try:
        backend = storage_module.get_storage()
        assert type(backend).__name__ == "PostgresStorage"
        assert backend.list_traces() == []
    finally:
        storage_module.reset_storage()


def test_trace_status_is_persisted(storage: object) -> None:
    st = storage
    trace = _trace("t1")
    trace.status = TraceStatus.FAILURE
    st.save_trace(trace)  # type: ignore[attr-defined]

    listed = st.list_traces(status=TraceStatus.FAILURE)  # type: ignore[attr-defined]
    assert [t.trace_id for t in listed] == ["t1"]
    assert st.list_traces(status=TraceStatus.SUCCESS) == []  # type: ignore[attr-defined]


def test_tenant_defaults_to_default_on_saved_traces(storage: object) -> None:
    """A tenant-less object must land in `default`, not vanish."""
    st = storage
    trace = _trace("t1")
    trace.tenant_id = Tenant(tenant_id=DEFAULT_TENANT_ID, name="default").tenant_id
    st.save_trace(trace)  # type: ignore[attr-defined]

    assert st.list_traces(tenant_id=DEFAULT_TENANT_ID)  # type: ignore[attr-defined]


def test_close_stops_the_event_loop_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown must join the loop thread, not leak one per backend.

    `close()` closed the pool but left its loop running on a daemon thread, so
    anything that rebuilds the backend leaked a thread each time. Invisible in a
    short run; a real leak in a long-lived process.
    """
    import threading

    from forensics.storage_postgres import PostgresStorage

    monkeypatch.setenv("DATABASE_URL", PG_URL)
    before = threading.active_count()

    st = PostgresStorage(traces_dir=tmp_path / "traces2")
    st.list_traces()  # forces the loop and pool into existence
    assert threading.active_count() == before + 1
    assert st._loop is not None

    st.close()

    assert st._loop is None
    assert threading.active_count() == before
