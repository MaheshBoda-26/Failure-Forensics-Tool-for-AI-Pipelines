"""SQLite and PostgreSQL-backed job queue (spec 07).

The queue lives in the same SQLite file (or PostgreSQL database when
`DATABASE_URL` is set) as the traces and the relational tables, in WAL mode on
SQLite, so a worker fleet needs no broker: claiming a job is a single atomic
`UPDATE ... RETURNING`, and two workers polling at the same instant cannot
claim the same row. `busy_timeout` is set so a concurrent writer waits instead of
raising `database is locked`.

Two deliberate differences from the spec's sketch:

* `Job` is a pydantic model rather than a dataclass. The API returns jobs
  directly, and pydantic gives the response an actual schema instead of guessing
  at `payload: dict`.
* A job's payload is cleared when the job reaches a terminal state. The payload
  carries the raw document text the pipeline is about to process. It is the
  pipeline's *input*, and redacting it would change what the model sees, so it is
  stored verbatim for the job's lifetime. Clearing at `completed`/`failed` bounds
  that window to the job instead of `QUEUE_JOB_TTL` (24h by default); it does not
  mean the plaintext was never written, and it is not secure erasure — SQLite
  keeps WAL and freelist pages. `docs/limitations.md` §5 and the spec 03
  implementation notes state this as the shipped guarantee. Retries keep it.
"""

import json
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

#: Payload retained for a finished job: empty, never the document text.
_CLEARED_PAYLOAD = "{}"

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS job_queue (
        job_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL,
        priority INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        result TEXT,
        error TEXT,
        worker_id TEXT,
        attempts INTEGER DEFAULT 0,
        max_attempts INTEGER DEFAULT 3,
        claim_expires_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_job_queue_status "
    "ON job_queue(status, priority DESC, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_job_queue_tenant ON job_queue(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_queue_claim_expires ON job_queue(claim_expires_at)",
)


def _parse_iso_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _format_iso_dt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class JobStatus(StrEnum):
    """Lifecycle of a queued job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(BaseModel):
    """One queued pipeline run."""

    job_id: str
    tenant_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus
    priority: int = 0
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    worker_id: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    claim_expires_at: str | None = None


class JobQueue:
    """Create, claim, and finish jobs in SQLite or PostgreSQL."""

    def __init__(self, db_path: Path | None = None, job_ttl: int | None = None) -> None:
        from forensics.storage import _should_use_postgres

        self._use_postgres = db_path is None and _should_use_postgres()
        if db_path is None and not self._use_postgres:
            from forensics.config import get_operational_settings

            db_path = get_operational_settings().db_path
        self.db_path = db_path or Path("data/forensics.db")
        self.job_ttl = job_ttl
        if not self._use_postgres:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    # --- plumbing ---

    def _init_schema(self) -> None:
        with self._conn() as conn:
            # Ensure legacy tables created before claim_expires_at existed gain the column
            existing_tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "job_queue" in existing_tables:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(job_queue)").fetchall()}
                if "claim_expires_at" not in cols:
                    conn.execute("ALTER TABLE job_queue ADD COLUMN claim_expires_at TEXT")
            for statement in _SCHEMA:
                conn.execute(statement)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Connection with WAL and a busy timeout.

        The timeout is what makes concurrent workers viable: without it, the
        second writer to touch the file gets `database is locked` instead of
        waiting its turn.
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _row_to_job(row: Any, cols: list[str] | None = None) -> Job:
        data = dict(zip(cols, row, strict=False)) if cols is not None else dict(row)
        raw_payload = data.get("payload")
        if isinstance(raw_payload, str):
            data["payload"] = json.loads(raw_payload or "{}")
        elif raw_payload is None:
            data["payload"] = {}

        raw_result = data.get("result")
        if isinstance(raw_result, str):
            data["result"] = json.loads(raw_result) if raw_result else None

        for ts_field in ("created_at", "started_at", "completed_at", "claim_expires_at"):
            if ts_field in data:
                data[ts_field] = _format_iso_dt(data[ts_field])
            elif ts_field == "claim_expires_at":
                data["claim_expires_at"] = None
        return Job.model_validate(data)

    # --- producer ---

    def enqueue(
        self,
        tenant_id: str,
        payload: dict[str, Any],
        priority: int = 0,
        max_attempts: int = 3,
    ) -> Job:
        """Add a job and return it (with its generated `job_id`)."""
        if not tenant_id:
            raise ValueError("tenant_id must be non-empty")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        now_dt = datetime.now(UTC)
        job = Job(
            job_id=uuid.uuid4().hex[:16],
            tenant_id=tenant_id,
            payload=payload,
            status=JobStatus.PENDING,
            priority=priority,
            created_at=now_dt.isoformat(),
            max_attempts=max_attempts,
        )
        if self._use_postgres:
            from forensics.storage import get_storage

            storage: Any = get_storage()

            async def _pg_enqueue() -> None:
                async with storage._conn() as conn:
                    await conn.execute(
                        """
                        INSERT INTO job_queue
                        (job_id, tenant_id, payload, status, priority, created_at, max_attempts)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        job.job_id,
                        job.tenant_id,
                        json.dumps(job.payload),
                        job.status.value,
                        job.priority,
                        now_dt,
                        job.max_attempts,
                    )

            storage._run_coro(_pg_enqueue())
            return job

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO job_queue
                (job_id, tenant_id, payload, status, priority, created_at, max_attempts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.tenant_id,
                    json.dumps(job.payload),
                    job.status.value,
                    job.priority,
                    job.created_at,
                    job.max_attempts,
                ),
            )
        return job

    # --- consumer ---

    def dequeue(self, worker_id: str, concurrency: int = 1, stall_timeout: int = 300) -> list[Job]:
        """Atomically claim up to `concurrency` pending jobs, highest priority first."""
        claimed: list[Job] = []
        now_dt = datetime.now(UTC)
        expires_at = now_dt + timedelta(seconds=stall_timeout)

        if self._use_postgres:
            from forensics.storage import get_storage

            storage: Any = get_storage()

            async def _pg_dequeue() -> list[Job]:
                rows_claimed: list[Job] = []
                async with storage._conn() as conn:
                    for _ in range(max(concurrency, 0)):
                        row = await conn.fetchrow(
                            """
                            UPDATE job_queue
                            SET status = $1, started_at = $2, worker_id = $3,
                                attempts = attempts + 1, claim_expires_at = $4
                            WHERE job_id = (
                                SELECT job_id FROM job_queue
                                WHERE status = $5
                                ORDER BY priority DESC, created_at
                                FOR UPDATE SKIP LOCKED
                                LIMIT 1
                            )
                            RETURNING *
                            """,
                            JobStatus.RUNNING.value,
                            now_dt,
                            worker_id,
                            expires_at,
                            JobStatus.PENDING.value,
                        )
                        if row is None:
                            break
                        rows_claimed.append(self._row_to_job(row))
                return rows_claimed

            return list(storage._run_coro(_pg_dequeue()))

        with self._conn() as conn:
            for _ in range(max(concurrency, 0)):
                cursor = conn.execute(
                    """
                    UPDATE job_queue
                    SET status = ?, started_at = ?, worker_id = ?, attempts = attempts + 1,
                        claim_expires_at = ?
                    WHERE job_id = (
                        SELECT job_id FROM job_queue
                        WHERE status = ?
                        ORDER BY priority DESC, created_at
                        LIMIT 1
                    )
                    RETURNING *
                    """,
                    (
                        JobStatus.RUNNING.value,
                        datetime.now(UTC).isoformat(),
                        worker_id,
                        expires_at.isoformat(),
                        JobStatus.PENDING.value,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    break
                claimed.append(self._row_to_job(row, [d[0] for d in cursor.description]))
        return claimed

    def complete(self, job_id: str, result: dict[str, Any]) -> bool:
        """Mark a job completed and drop its payload. Returns False if unknown."""
        now_dt = datetime.now(UTC)
        if self._use_postgres:
            from forensics.storage import get_storage

            storage: Any = get_storage()

            async def _pg_complete() -> bool:
                async with storage._conn() as conn:
                    res = await conn.execute(
                        """
                        UPDATE job_queue
                        SET status = $1, completed_at = $2, result = $3, payload = $4,
                            error = NULL, claim_expires_at = NULL
                        WHERE job_id = $5
                        """,
                        JobStatus.COMPLETED.value,
                        now_dt,
                        json.dumps(result),
                        _CLEARED_PAYLOAD,
                        job_id,
                    )
                    return str(res).endswith(" 1")

            return bool(storage._run_coro(_pg_complete()))

        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE job_queue
                SET status = ?, completed_at = ?, result = ?, payload = ?, error = NULL,
                    claim_expires_at = NULL
                WHERE job_id = ?
                """,
                (
                    JobStatus.COMPLETED.value,
                    now_dt.isoformat(),
                    json.dumps(result),
                    _CLEARED_PAYLOAD,
                    job_id,
                ),
            )
            return cursor.rowcount > 0

    def fail(self, job_id: str, error: str) -> JobStatus:
        """Record a failure, requeueing while attempts remain."""
        now_dt = datetime.now(UTC)
        if self._use_postgres:
            from forensics.storage import get_storage

            storage: Any = get_storage()

            async def _pg_fail() -> JobStatus:
                async with storage._conn() as conn:
                    row = await conn.fetchrow(
                        "SELECT attempts, max_attempts FROM job_queue WHERE job_id = $1",
                        job_id,
                    )
                    if row is None:
                        raise KeyError(f"Unknown job: {job_id}")
                    attempts, max_attempts = int(row["attempts"]), int(row["max_attempts"])
                    if attempts < max_attempts:
                        await conn.execute(
                            "UPDATE job_queue SET status = $1, error = $2, worker_id = NULL, "
                            "claim_expires_at = NULL WHERE job_id = $3",
                            JobStatus.PENDING.value,
                            error,
                            job_id,
                        )
                        return JobStatus.PENDING

                    await conn.execute(
                        """
                        UPDATE job_queue
                        SET status = $1, completed_at = $2, error = $3, worker_id = NULL,
                            payload = $4, claim_expires_at = NULL
                        WHERE job_id = $5
                        """,
                        JobStatus.FAILED.value,
                        now_dt,
                        error,
                        _CLEARED_PAYLOAD,
                        job_id,
                    )
                    return JobStatus.FAILED

            return JobStatus(storage._run_coro(_pg_fail()))

        with self._conn() as conn:
            row = conn.execute(
                "SELECT attempts, max_attempts FROM job_queue WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown job: {job_id}")

            attempts, max_attempts = int(row[0]), int(row[1])
            if attempts < max_attempts:
                conn.execute(
                    "UPDATE job_queue SET status = ?, error = ?, worker_id = NULL, "
                    "claim_expires_at = NULL WHERE job_id = ?",
                    (JobStatus.PENDING.value, error, job_id),
                )
                return JobStatus.PENDING

            conn.execute(
                """
                UPDATE job_queue
                SET status = ?, completed_at = ?, error = ?, worker_id = NULL, payload = ?,
                    claim_expires_at = NULL
                WHERE job_id = ?
                """,
                (
                    JobStatus.FAILED.value,
                    now_dt.isoformat(),
                    error,
                    _CLEARED_PAYLOAD,
                    job_id,
                ),
            )
            return JobStatus.FAILED

    # --- readers ---

    def get_job(self, job_id: str, tenant_id: str | None = None) -> Job | None:
        """Get one job, optionally scoped to a tenant."""
        if self._use_postgres:
            from forensics.storage import get_storage

            storage: Any = get_storage()

            async def _pg_get() -> Job | None:
                sql = "SELECT * FROM job_queue WHERE job_id = $1"
                params: list[Any] = [job_id]
                if tenant_id is not None:
                    sql += " AND tenant_id = $2"
                    params.append(tenant_id)
                async with storage._conn() as conn:
                    row = await conn.fetchrow(sql, *params)
                    return self._row_to_job(row) if row is not None else None

            return cast(Job | None, storage._run_coro(_pg_get()))

        sql = "SELECT * FROM job_queue WHERE job_id = ?"
        params: list[Any] = [job_id]
        if tenant_id is not None:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_job(row, [d[0] for d in cursor.description])

    def list_jobs(
        self,
        tenant_id: str | None = None,
        status: JobStatus | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """List jobs, newest first. Both filters are optional."""
        if self._use_postgres:
            from forensics.storage import get_storage

            storage: Any = get_storage()

            async def _pg_list() -> list[Job]:
                clauses: list[str] = []
                params: list[Any] = []
                idx = 1
                if tenant_id is not None:
                    clauses.append(f"tenant_id = ${idx}")
                    params.append(tenant_id)
                    idx += 1
                if status is not None:
                    clauses.append(f"status = ${idx}")
                    params.append(status.value)
                    idx += 1
                where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
                params.append(limit)
                async with storage._conn() as conn:
                    rows = await conn.fetch(
                        f"SELECT * FROM job_queue{where} ORDER BY created_at DESC LIMIT ${idx}",
                        *params,
                    )
                    return [self._row_to_job(r) for r in rows]

            return list(storage._run_coro(_pg_list()))

        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._conn() as conn:
            cursor = conn.execute(
                f"SELECT * FROM job_queue{where} ORDER BY created_at DESC LIMIT ?", params
            )
            cols = [d[0] for d in cursor.description]
            return [self._row_to_job(row, cols) for row in cursor.fetchall()]

    def counts(self) -> dict[str, int]:
        """Job count per status — what an operator dashboard wants."""
        totals = {status.value: 0 for status in JobStatus}
        if self._use_postgres:
            from forensics.storage import get_storage

            storage: Any = get_storage()

            async def _pg_counts() -> dict[str, int]:
                async with storage._conn() as conn:
                    rows = await conn.fetch(
                        "SELECT status, COUNT(*) AS cnt FROM job_queue GROUP BY status"
                    )
                    for row in rows:
                        totals[str(row["status"])] = int(row["cnt"])
                return totals

            return dict(storage._run_coro(_pg_counts()))

        with self._conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) FROM job_queue GROUP BY status").fetchall()
        for status, count in rows:
            totals[str(status)] = int(count)
        return totals

    # --- housekeeping ---

    def cleanup(self, ttl_seconds: int | None = None) -> int:
        """Delete finished jobs older than the TTL. Returns how many were removed."""
        ttl = ttl_seconds if ttl_seconds is not None else self.job_ttl
        if ttl is None:
            raise ValueError("No TTL configured: pass ttl_seconds or construct with job_ttl")
        cutoff_dt = datetime.now(UTC) - timedelta(seconds=ttl)

        if self._use_postgres:
            from forensics.storage import get_storage

            storage: Any = get_storage()

            async def _pg_cleanup() -> int:
                async with storage._conn() as conn:
                    res = await conn.execute(
                        "DELETE FROM job_queue WHERE status IN ($1, $2) "
                        "AND completed_at IS NOT NULL AND completed_at < $3",
                        JobStatus.COMPLETED.value,
                        JobStatus.FAILED.value,
                        cutoff_dt,
                    )
                    return int(str(res).split()[-1])

            return int(storage._run_coro(_pg_cleanup()))

        cutoff = cutoff_dt.isoformat()
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM job_queue WHERE status IN (?, ?) AND completed_at IS NOT NULL "
                "AND completed_at < ?",
                (JobStatus.COMPLETED.value, JobStatus.FAILED.value, cutoff),
            )
            return cursor.rowcount

    def heartbeat(self, job_id: str, worker_id: str, stall_timeout: int = 300) -> bool:
        """Extend the claim expiration for a job being processed."""
        expires_at = datetime.now(UTC) + timedelta(seconds=stall_timeout)
        if self._use_postgres:
            from forensics.storage import get_storage

            storage: Any = get_storage()

            async def _pg_heartbeat() -> bool:
                async with storage._conn() as conn:
                    res = await conn.execute(
                        """
                        UPDATE job_queue
                        SET claim_expires_at = $1
                        WHERE job_id = $2 AND worker_id = $3 AND status = $4
                        """,
                        expires_at,
                        job_id,
                        worker_id,
                        JobStatus.RUNNING.value,
                    )
                    return str(res).endswith(" 1")

            return bool(storage._run_coro(_pg_heartbeat()))

        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE job_queue
                SET claim_expires_at = ?
                WHERE job_id = ? AND worker_id = ? AND status = ?
                """,
                (
                    expires_at.isoformat(),
                    job_id,
                    worker_id,
                    JobStatus.RUNNING.value,
                ),
            )
            return cursor.rowcount > 0

    def reclaim_stalled_jobs(self, stall_timeout: int = 300) -> int:
        """Reclaim jobs that have been running past their claim expiration or stall_timeout.

        Finds jobs with status 'running' where `claim_expires_at` is in the past
        (or `claim_expires_at IS NULL` and `started_at < now - stall_timeout`).
        Jobs with remaining attempts (`attempts < max_attempts`) are reset to
        'pending'; jobs that have already exhausted `max_attempts` are marked
        'failed' so poison-pill crashes cannot loop forever.

        Returns the total number of stalled jobs processed (reclaimed or failed).
        """
        now_dt = datetime.now(UTC)
        cutoff_dt = now_dt - timedelta(seconds=stall_timeout)

        if self._use_postgres:
            from forensics.storage import get_storage

            storage: Any = get_storage()

            async def _pg_reclaim() -> int:
                stalled_cond = (
                    "status = $1 AND ("
                    "(claim_expires_at IS NOT NULL AND claim_expires_at < $2) OR "
                    "(claim_expires_at IS NULL AND started_at IS NOT NULL AND started_at < $3)"
                    ")"
                )
                async with storage._conn() as conn:
                    res_fail = await conn.execute(
                        f"""
                        UPDATE job_queue
                        SET status = $4, completed_at = $2, worker_id = NULL,
                            claim_expires_at = NULL, payload = $5,
                            error = COALESCE(error, 'Job stalled after exhausting max_attempts')
                        WHERE {stalled_cond} AND attempts >= max_attempts
                        """,
                        JobStatus.RUNNING.value,
                        now_dt,
                        cutoff_dt,
                        JobStatus.FAILED.value,
                        _CLEARED_PAYLOAD,
                    )
                    res_requeue = await conn.execute(
                        f"""
                        UPDATE job_queue
                        SET status = $4, worker_id = NULL, claim_expires_at = NULL
                        WHERE {stalled_cond} AND attempts < max_attempts
                        """,
                        JobStatus.RUNNING.value,
                        now_dt,
                        cutoff_dt,
                        JobStatus.PENDING.value,
                    )
                    return int(str(res_fail).split()[-1]) + int(str(res_requeue).split()[-1])

            return int(storage._run_coro(_pg_reclaim()))

        now = now_dt.isoformat()
        cutoff = cutoff_dt.isoformat()
        stalled_where = (
            "status = ? AND ("
            "(claim_expires_at IS NOT NULL AND claim_expires_at < ?) OR "
            "(claim_expires_at IS NULL AND started_at IS NOT NULL AND started_at < ?)"
            ")"
        )
        with self._conn() as conn:
            fail_cursor = conn.execute(
                f"""
                UPDATE job_queue
                SET status = ?, completed_at = ?, worker_id = NULL, claim_expires_at = NULL,
                    payload = ?,
                    error = COALESCE(error, 'Job stalled after exhausting max_attempts')
                WHERE {stalled_where} AND attempts >= max_attempts
                """,
                (
                    JobStatus.FAILED.value,
                    now,
                    _CLEARED_PAYLOAD,
                    JobStatus.RUNNING.value,
                    now,
                    cutoff,
                ),
            )
            requeue_cursor = conn.execute(
                f"""
                UPDATE job_queue
                SET status = ?, worker_id = NULL, claim_expires_at = NULL
                WHERE {stalled_where} AND attempts < max_attempts
                """,
                (
                    JobStatus.PENDING.value,
                    JobStatus.RUNNING.value,
                    now,
                    cutoff,
                ),
            )
            return fail_cursor.rowcount + requeue_cursor.rowcount


#: Process-wide queue, shared by the API, the CLI, and the worker.
_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    """Return the process-wide JobQueue, creating it on first use.

    Not at import time: constructing a JobQueue opens (and creates) the SQLite
    file, which as an import side effect broke test isolation for `Storage`.
    """
    global _queue
    if _queue is None:
        from forensics.config import get_operational_settings
        from forensics.storage import _should_use_postgres

        settings = get_operational_settings()
        db_path = None if _should_use_postgres() else settings.db_path
        _queue = JobQueue(db_path, job_ttl=settings.queue_job_ttl)
    return _queue


def reset_queue() -> None:
    """Drop the cached queue (used by tests)."""
    global _queue
    _queue = None


__all__ = ["Job", "JobQueue", "JobStatus", "get_queue", "reset_queue"]
