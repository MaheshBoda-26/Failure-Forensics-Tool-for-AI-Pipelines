"""SQLite-backed job queue (spec 07).

The queue lives in the same SQLite file as the traces and the relational tables,
in WAL mode, so a worker fleet needs no broker: claiming a job is a single
atomic `UPDATE ... RETURNING`, and two workers polling at the same instant cannot
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
from typing import Any

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
    """Create, claim, and finish jobs in one SQLite file."""

    def __init__(self, db_path: Path | None = None, job_ttl: int | None = None) -> None:
        if db_path is None:
            from forensics.config import get_settings

            db_path = get_settings().db_path
        self.db_path = db_path
        self.job_ttl = job_ttl
        self._init_schema()

    # --- plumbing ---

    def _init_schema(self) -> None:
        with self._conn() as conn:
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
    def _row_to_job(row: Any, cols: list[str]) -> Job:
        data = dict(zip(cols, row, strict=False))
        data["payload"] = json.loads(data["payload"] or "{}")
        data["result"] = json.loads(data["result"]) if data.get("result") else None
        # claim_expires_at may not exist in older databases
        if "claim_expires_at" not in data:
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

        job = Job(
            job_id=uuid.uuid4().hex[:16],
            tenant_id=tenant_id,
            payload=payload,
            status=JobStatus.PENDING,
            priority=priority,
            created_at=datetime.now(UTC).isoformat(),
            max_attempts=max_attempts,
        )
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
        """Atomically claim up to `concurrency` pending jobs, highest priority first.

        One statement per claim, so the row is claimed and returned from the same
        locked write: a second worker racing for the same job finds it already
        `running` and gets nothing.

        `stall_timeout` is the number of seconds after which a claimed job is
        considered stalled and eligible for reclamation. The `claim_expires_at`
        timestamp is set to `now + stall_timeout`.
        """
        claimed: list[Job] = []
        expires_at = datetime.now(UTC) + timedelta(seconds=stall_timeout)
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
                    datetime.now(UTC).isoformat(),
                    json.dumps(result),
                    _CLEARED_PAYLOAD,
                    job_id,
                ),
            )
            return cursor.rowcount > 0

    def fail(self, job_id: str, error: str) -> JobStatus:
        """Record a failure, requeueing while attempts remain.

        Returns the resulting status: `pending` when the job will be retried,
        `failed` once `max_attempts` is exhausted.
        """
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
                    datetime.now(UTC).isoformat(),
                    error,
                    _CLEARED_PAYLOAD,
                    job_id,
                ),
            )
            return JobStatus.FAILED

    # --- readers ---

    def get_job(self, job_id: str, tenant_id: str | None = None) -> Job | None:
        """Get one job, optionally scoped to a tenant."""
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
        with self._conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) FROM job_queue GROUP BY status").fetchall()
        totals = {status.value: 0 for status in JobStatus}
        for status, count in rows:
            totals[str(status)] = int(count)
        return totals

    # --- housekeeping ---

    def cleanup(self, ttl_seconds: int | None = None) -> int:
        """Delete finished jobs older than the TTL. Returns how many were removed.

        Only terminal jobs are eligible: a pending or running job is still work
        someone owns.
        """
        ttl = ttl_seconds if ttl_seconds is not None else self.job_ttl
        if ttl is None:
            raise ValueError("No TTL configured: pass ttl_seconds or construct with job_ttl")
        cutoff = (datetime.now(UTC) - timedelta(seconds=ttl)).isoformat()
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM job_queue WHERE status IN (?, ?) AND completed_at IS NOT NULL "
                "AND completed_at < ?",
                (JobStatus.COMPLETED.value, JobStatus.FAILED.value, cutoff),
            )
            return cursor.rowcount

    def heartbeat(self, job_id: str, worker_id: str, stall_timeout: int = 300) -> bool:
        """Extend the claim expiration for a job being processed.

        Returns True if the job was found and belongs to this worker, False otherwise.
        This should be called periodically by the worker while processing a long job.
        """
        expires_at = datetime.now(UTC) + timedelta(seconds=stall_timeout)
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
        """Reclaim jobs that have been running past their claim expiration.

        Finds jobs with status 'running' where claim_expires_at is in the past,
        resets them to 'pending' with worker_id=NULL, and clears the expiration.

        Returns the number of jobs reclaimed.
        """
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE job_queue
                SET status = ?, worker_id = NULL, claim_expires_at = NULL
                WHERE status = ? AND claim_expires_at IS NOT NULL AND claim_expires_at < ?
                """,
                (JobStatus.PENDING.value, JobStatus.RUNNING.value, now),
            )
            return cursor.rowcount


#: Process-wide queue, shared by the API, the CLI, and the worker.
_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    """Return the process-wide JobQueue, creating it on first use.

    Not at import time: constructing a JobQueue opens (and creates) the SQLite
    file, which as an import side effect broke test isolation for `Storage`.
    """
    global _queue
    if _queue is None:
        from forensics.config import get_settings

        settings = get_settings()
        _queue = JobQueue(settings.db_path, job_ttl=settings.queue_job_ttl)
    return _queue


def reset_queue() -> None:
    """Drop the cached queue (used by tests)."""
    global _queue
    _queue = None


__all__ = ["Job", "JobQueue", "JobStatus", "get_queue", "reset_queue"]
