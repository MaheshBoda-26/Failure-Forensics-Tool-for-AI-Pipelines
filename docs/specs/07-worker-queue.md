# SPEC: Worker Queue (SQLite-backed)

## Summary
Move pipeline execution off API process. API enqueues jobs; workers process them. SQLite-backed (WAL mode), zero Redis. Same DB as storage.

## Config (`.env`)
```bash
QUEUE_ENABLED=true|false              # default: false
QUEUE_WORKER_CONCURRENCY=2            # parallel workers per process
QUEUE_POLL_INTERVAL=1.0               # seconds between polls
QUEUE_JOB_TTL=86400                   # job retention (seconds)
```

## Schema (SQLite)
```sql
CREATE TABLE job_queue (
    job_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    payload TEXT NOT NULL,          -- JSON: {doc_id, doc_text, expected_type, ...}
    status TEXT NOT NULL,           -- pending, running, completed, failed
    priority INTEGER DEFAULT 0,     -- higher = first
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    result TEXT,                    -- JSON result
    error TEXT,
    worker_id TEXT,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3
);
CREATE INDEX idx_job_queue_status ON job_queue(status, priority DESC, created_at);
CREATE INDEX idx_job_queue_tenant ON job_queue(tenant_id);
```

## New Modules

### `src/forensics/queue.py`
```python
"""SQLite-backed job queue with WAL mode."""

import sqlite3
import json
import uuid
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional
from pathlib import Path


@dataclass
class Job:
    job_id: str
    tenant_id: str
    payload: dict
    status: str
    priority: int
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    result: Optional[dict]
    error: Optional[str]
    worker_id: Optional[str]
    attempts: int
    max_attempts: int


class JobQueue:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
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
                    max_attempts INTEGER DEFAULT 3
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_queue_status ON job_queue(status, priority DESC, created_at)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_queue_tenant ON job_queue(tenant_id)")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def enqueue(self, tenant_id: str, payload: dict, priority: int = 0) -> Job:
        job_id = uuid.uuid4().hex[:16]
        now = datetime.now(UTC).isoformat()
        job = Job(
            job_id=job_id,
            tenant_id=tenant_id,
            payload=payload,
            status="pending",
            priority=priority,
            created_at=now,
            started_at=None,
            completed_at=None,
            result=None,
            error=None,
            worker_id=None,
            attempts=0,
            max_attempts=3,
        )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO job_queue (job_id, tenant_id, payload, status, priority, created_at, max_attempts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    job.job_id,
                    job.tenant_id,
                    json.dumps(job.payload),
                    job.status,
                    job.priority,
                    job.created_at,
                    job.max_attempts,
                ),
            )
        return job

    def dequeue(self, worker_id: str, concurrency: int = 1) -> list[Job]:
        """Atomically claim up to `concurrency` pending jobs."""
        jobs = []
        with self._conn() as conn:
            for _ in range(concurrency):
                cursor = conn.execute(
                    """
                    UPDATE job_queue
                    SET status = 'running', started_at = ?, worker_id = ?, attempts = attempts + 1
                    WHERE job_id = (
                        SELECT job_id FROM job_queue
                        WHERE status = 'pending'
                        ORDER BY priority DESC, created_at
                        LIMIT 1
                    )
                    RETURNING *
                """,
                    (datetime.now(UTC).isoformat(), worker_id),
                )
                row = cursor.fetchone()
                if not row:
                    break
                cols = [d[0] for d in cursor.description]
                job = Job(**dict(zip(cols, row, strict=False)))
                job.payload = json.loads(job.payload)
                jobs.append(job)
        return jobs

    def complete(self, job_id: str, result: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE job_queue
                SET status = 'completed', completed_at = ?, result = ?
                WHERE job_id = ?
            """,
                (datetime.now(UTC).isoformat(), json.dumps(result), job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        with self._conn() as conn:
            # Check if retry
            cursor = conn.execute(
                "SELECT attempts, max_attempts FROM job_queue WHERE job_id = ?", (job_id,)
            )
            row = cursor.fetchone()
            if row and row[0] < row[1]:
                conn.execute(
                    """
                    UPDATE job_queue
                    SET status = 'pending', error = ?, worker_id = NULL
                    WHERE job_id = ?
                """,
                    (error, job_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE job_queue
                    SET status = 'failed', completed_at = ?, error = ?
                    WHERE job_id = ?
                """,
                    (datetime.now(UTC).isoformat(), error, job_id),
                )

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._conn() as conn:
            cursor = conn.execute("SELECT * FROM job_queue WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            job = Job(**dict(zip(cols, row, strict=False)))
            job.payload = json.loads(job.payload)
            if job.result:
                job.result = json.loads(job.result)
            return job

    def list_jobs(self, tenant_id: str, status: str | None = None, limit: int = 100) -> list[Job]:
        with self._conn() as conn:
            if status:
                cursor = conn.execute(
                    "SELECT * FROM job_queue WHERE tenant_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?",
                    (tenant_id, status, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM job_queue WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                    (tenant_id, limit),
                )
            jobs = []
            for row in cursor.fetchall():
                cols = [d[0] for d in cursor.description]
                job = Job(**dict(zip(cols, row, strict=False)))
                job.payload = json.loads(job.payload)
                if job.result:
                    job.result = json.loads(job.result)
                jobs.append(job)
            return jobs
```

### `src/forensics/worker.py` — New
```python
"""Pipeline worker process."""

import asyncio
import signal
import uuid
from forensics.queue import JobQueue, Job
from forensics.storage import Storage
from forensics.pipeline import Pipeline
from forensics.llm import using_llm_client
from forensics.models import Document
from forensics.tracing import trace_context
from forensics.config import get_settings


class PipelineWorker:
    def __init__(self, storage: Storage, queue: JobQueue, worker_id: str | None = None):
        self.storage = storage
        self.queue = queue
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.concurrency = get_settings().queue_worker_concurrency
        self.poll_interval = get_settings().queue_poll_interval
        self._running = False

    async def run(self) -> None:
        self._running = True
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown)

        async with using_llm_client() as client:
            pipeline = Pipeline(client=client)
            while self._running:
                jobs = self.queue.dequeue(self.worker_id, self.concurrency)
                if not jobs:
                    await asyncio.sleep(self.poll_interval)
                    continue

                await asyncio.gather(*[self._process_job(pipeline, job) for job in jobs])

    def _shutdown(self) -> None:
        self._running = False

    async def _process_job(self, pipeline: Pipeline, job: Job) -> None:
        try:
            payload = job.payload
            doc_id = payload["doc_id"]
            doc_text = payload["doc_text"]
            expected_type = payload.get("expected_type")

            document = Document(content=doc_text)
            with trace_context(document_id=doc_id) as ctx:
                await pipeline.run(document)
                if ctx.trace:
                    self.storage.save_trace(ctx.trace)
                    from forensics.analyzer_simple import analyze_trace

                    diag = analyze_trace(ctx.trace, doc_text, expected_type)
                    self.storage.create_diagnosis(diag)

            self.queue.complete(job.job_id, {"trace_id": ctx.trace.trace_id if ctx.trace else None})

        except Exception as e:
            self.queue.fail(job.job_id, str(e))


async def run_worker():
    storage = Storage()
    queue = JobQueue(get_settings().db_path)
    worker = PipelineWorker(storage, queue)
    await worker.run()
```

### `src/forensics/api.py`
```python
from forensics.queue import JobQueue, Job

queue = JobQueue(get_settings().db_path)


@app.post("/pipeline/run", response_model=Job)
def enqueue_pipeline(request: PipelineRunRequest, tenant: Tenant = Depends(get_current_tenant)):
    """Enqueue a document for async pipeline processing."""
    payload = {
        "doc_id": request.doc_id,
        "doc_text": request.doc_text,
        "expected_type": request.expected_type,
    }
    job = queue.enqueue(tenant.tenant_id, payload, priority=request.priority)
    return job


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, tenant: Tenant = Depends(get_current_tenant)):
    job = queue.get_job(job_id)
    if not job or job.tenant_id != tenant.tenant_id:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/jobs", response_model=list[Job])
def list_jobs(tenant: Tenant = Depends(get_current_tenant), status: str | None = None):
    return queue.list_jobs(tenant.tenant_id, status=status)
```

### `src/forensics/cli.py`
```bash
forensics worker start          # run worker (foreground)
forensics queue enqueue --doc-id X --file path.txt --expected invoice
forensics queue status --job-id X
forensics queue list --status pending
```

## Acceptance Criteria
1. `QUEUE_ENABLED=false`: API runs pipeline synchronously (current behavior)
2. `QUEUE_ENABLED=true`: `POST /pipeline/run` returns `job_id` immediately (202)
3. Worker picks up job, processes pipeline, saves trace + diagnosis
4. `GET /jobs/{id}` returns job with result (trace_id) or error
5. Retry on failure (max 3 attempts), then status=failed
6. Multiple workers can run (each claims distinct jobs via atomic UPDATE)
7. `forensics worker start` runs until SIGTERM
8. Job TTL cleanup (optional): delete completed/failed > 24h

## Effort
- **Files**: 2 new (`queue.py`, `worker.py`), 2 modified
- **Time**: ~5 hours
- **Risk**: Medium (concurrency, async worker, atomic claims)

## Migration Path
1. Deploy queue schema (backward compatible)
2. Enable `QUEUE_ENABLED=true` with workers running
3. API returns 202 + job_id; clients poll `/jobs/{id}`
4. Later: add callback/webhook on job completion
