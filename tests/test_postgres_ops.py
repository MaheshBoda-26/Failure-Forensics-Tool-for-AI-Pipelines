"""PostgreSQL backend operational tests.

Prove what `docs/limitations.md` §18 leaves unproven for a live server:
connection-pool sizing under load, recovery from a killed backend connection,
idempotent shutdown, and that a long-running transaction does not wedge the
pool.

These need a live server. Set `FORENSICS_TEST_POSTGRES_URL` and run
`make test-postgres-ops`; without it the module skips so the default suite
stays hermetic.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from forensics.config import reset_settings
from forensics.models import DEFAULT_TENANT_ID, Span, Trace
from forensics.storage_postgres import PostgresStorage

PG_URL = os.environ.get("FORENSICS_TEST_POSTGRES_URL", "")

if PG_URL:
    try:
        import asyncpg  # noqa: F401
    except ImportError:  # pragma: no cover - depends on the environment
        pytest.skip(
            "asyncpg is not installed: pip install -e '.[postgres]'",
            allow_module_level=True,
        )

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not PG_URL,
        reason="set FORENSICS_TEST_POSTGRES_URL to run the PostgreSQL ops tests",
    ),
]


def _span() -> Span:
    return Span(
        trace_id="t",
        step_name="extraction",
        input_json="{}",
        output_json="{}",
        model_id="m",
        provider="p",
        prompt="p",
        raw_response="r",
        status="success",
    )


def _trace() -> Trace:
    trace = Trace(document_id="d1", tenant_id=DEFAULT_TENANT_ID, spans=[_span()])
    return trace


@pytest.fixture
def storage() -> Iterator[PostgresStorage]:
    reset_settings()
    st = PostgresStorage()
    try:
        yield st
    finally:
        st.close()
        reset_settings()


# --- 1. Pool exhaustion: small max, concurrent ops queue and succeed ---------


def test_pool_exhaustion_queues_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a tiny pool max, concurrent operations still all complete.

    The pool queues waiters instead of deadlocking, so a burst of concurrent
    requests cannot wedge the backend.
    """
    monkeypatch.setenv("PG_POOL_MIN", "0")
    monkeypatch.setenv("PG_POOL_MAX", "2")
    reset_settings()
    st = PostgresStorage()
    try:
        n = 25

        def save(i: int) -> Trace | None:
            trace = Trace(
                document_id=f"doc_pool_exhaust_{i}",
                tenant_id=DEFAULT_TENANT_ID,
                spans=[_span()],
            )
            return st.save_trace(trace)

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(save, i) for i in range(n)]
            results = [future.result() for future in futures]

        assert all(r is not None for r in results), "a pooled operation returned None"
        assert st.list_traces() is not None
    finally:
        st.close()
        reset_settings()


# --- 2. Connection kill: next operation reconnects ---------------------------


def test_connection_kill_reconnects(storage: PostgresStorage) -> None:
    """Terminating a backend connection does not wedge the pool.

    A killed idle connection is discarded by the pool; the next operation
    acquires a fresh connection and succeeds.
    """
    storage.save_trace(_trace())

    async def _kill() -> None:
        p = await storage._get_pool()
        async with p.acquire() as conn:
            pid = conn.get_server_pid() if hasattr(conn, "get_server_pid") else None
        if pid is not None:
            async with p.acquire() as conn:
                await conn.execute("SELECT pg_terminate_backend($1)", pid)

    asyncio.run(_kill())

    # Next operation must succeed on a fresh connection.
    trace = storage.save_trace(_trace())
    assert trace is not None
    assert storage.list_traces() is not None


# --- 3. close() twice is idempotent; rebuild after close works ---------------


def test_close_twice_is_idempotent(storage: PostgresStorage) -> None:
    storage.close()
    storage.close()  # must not raise


def test_rebuild_after_close_works(storage: PostgresStorage) -> None:
    storage.close()
    # A subsequent operation rebuilds the pool and loop.
    trace = storage.save_trace(_trace())
    assert trace is not None
    assert storage.list_traces() is not None


# --- 4. Long-running transaction does not permanently wedge the pool ---------


def test_long_running_transaction_does_not_wedge_pool(storage: PostgresStorage) -> None:
    """An idle-in-transaction connection is released back to the pool.

    A transaction that is left open (the pathological case a misbehaved caller
    could create) is released when the connection returns to the pool, so a
    later operation on a different connection still completes.
    """

    async def _open_and_close() -> None:
        p = await storage._get_pool()
        async with p.acquire() as conn:
            # Begin a transaction but never commit/rollback; releasing the
            # connection returns it to the pool in an idle-in-transaction state.
            await conn.execute("BEGIN")
            await conn.execute("SELECT 1")

    asyncio.run(_open_and_close())

    # The pool must still serve a fresh operation.
    trace = storage.save_trace(_trace())
    assert trace is not None
