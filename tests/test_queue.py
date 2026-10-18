"""Worker queue (spec 07).

The queue is tested against a real SQLite file — including a threaded claim test,
because "two workers cannot claim the same job" is a statement about SQLite's
locking behaviour, not about Python. The worker is driven through `run_once` with
a stand-in pipeline, so nothing here sleeps, and no test reaches a model.
"""

import asyncio
import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from forensics import api
from forensics import queue as queue_module
from forensics.cli import app as cli_app
from forensics.config import reset_settings
from forensics.models import DEFAULT_TENANT_ID, Document
from forensics.queue import Job, JobQueue, JobStatus
from forensics.storage import Storage
from forensics.tracing import get_current_trace
from forensics.worker import PipelineWorker

DOC_TEXT = "Invoice INV-001 from Acme Corp, dated 2024-01-15, total $10,000."


# ─── Helpers and fixtures ───────────────────────────────────────────────────


class FakePipeline:
    """Stands in for `Pipeline`: appends one span to the active trace.

    A span (rather than nothing) keeps the worker on the real code path: the
    trace is finalised from its spans, and the analyzer scores what it finds.
    """

    def __init__(
        self, client: object | None = None, on_run: Callable[[], None] | None = None
    ) -> None:
        # `client` is accepted and ignored so the fake can stand in for
        # `Pipeline(client=...)` at the call sites under test.
        self.client = client
        self.calls: list[str] = []
        self.document_ids: list[str] = []
        self._on_run = on_run

    async def run(self, document: Document, document_id: str = "") -> None:
        """Mirror `Pipeline.run`, which forwards the caller's document id.

        The signature has to match: `worker.process_document` passes
        `document_id=` explicitly, so a double that only accepts the document
        raises `TypeError` and every queued job "fails" for the wrong reason.
        """
        from forensics.models import Span

        self.calls.append(document.content)
        self.document_ids.append(document_id)
        trace = get_current_trace()
        assert trace is not None
        trace.spans.append(
            Span(
                trace_id=trace.trace_id,
                step_name="extraction",
                input_json="{}",
                output_json=json.dumps({"entities": [], "raw_response": "{}"}),
                model_id="test-model",
                provider="test",
                prompt="prompt",
                raw_response="{}",
            )
        )
        if self._on_run is not None:
            self._on_run()


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    db_path = tmp_path / "db.sqlite"
    return Storage(traces_dir=tmp_path / "traces", db_path=db_path)


@pytest.fixture
def queue(storage: Storage, monkeypatch: pytest.MonkeyPatch) -> JobQueue:
    """A queue on the same SQLite file as storage, wired into the module cache."""
    job_queue = JobQueue(storage.db_path, job_ttl=60)
    monkeypatch.setattr(queue_module, "_queue", job_queue)
    return job_queue


@pytest.fixture
def api_state(storage: Storage, monkeypatch: pytest.MonkeyPatch) -> Storage:
    monkeypatch.setattr(api, "_storage", storage)
    return storage


@pytest.fixture
def queue_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Turn the queue on with settings that keep tests fast."""
    monkeypatch.setenv("QUEUE_ENABLED", "true")
    monkeypatch.setenv("QUEUE_POLL_INTERVAL", "0.01")
    monkeypatch.setenv("QUEUE_WORKER_CONCURRENCY", "2")
    reset_settings()
    yield
    reset_settings()


def _payload(doc_id: str = "doc_001") -> dict[str, Any]:
    return {"doc_id": doc_id, "doc_text": DOC_TEXT, "expected_type": "invoice"}


# ─── Criterion 1: queue disabled -> synchronous run ─────────────────────────


def test_api_runs_inline_when_the_queue_is_disabled(
    api_state: Storage, queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    reset_settings()
    monkeypatch.setattr("forensics.pipeline.Pipeline", FakePipeline)
    monkeypatch.setattr("forensics.llm.using_llm_client", FakeLLMClient)

    response = TestClient(api.app).post(
        "/pipeline/run",
        json={"doc_id": "doc_001", "doc_text": DOC_TEXT, "expected_type": "invoice"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["trace_id"]
    assert body["diagnosis_id"]
    # Nothing was queued, and both records exist on the synchronous path.
    assert queue.list_jobs() == []
    assert api_state.get_trace(body["trace_id"]) is not None
    assert api_state.get_diagnosis(body["diagnosis_id"]) is not None


class FakeLLMClient:
    """Async context manager yielding a placeholder LLM client."""

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc: object) -> None:
        return None


# ─── Criterion 2: queue enabled -> 202 + job_id ─────────────────────────────


def test_api_queues_the_run_when_the_queue_is_enabled(
    api_state: Storage, queue: JobQueue, queue_env: None
) -> None:
    response = TestClient(api.app).post(
        "/pipeline/run",
        json={
            "doc_id": "doc_007",
            "doc_text": DOC_TEXT,
            "expected_type": "invoice",
            "priority": 3,
        },
    )

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert response.json()["status"] == "queued"

    job = queue.get_job(job_id)
    assert job is not None
    assert job.status is JobStatus.PENDING
    assert job.tenant_id == DEFAULT_TENANT_ID
    assert job.priority == 3
    assert job.payload["doc_id"] == "doc_007"
    assert job.payload["expected_type"] == "invoice"


def test_api_rejects_an_unknown_expected_type(
    api_state: Storage, queue: JobQueue, queue_env: None
) -> None:
    """Ground truth is constrained, so a typo cannot silently disable the check."""
    response = TestClient(api.app).post(
        "/pipeline/run",
        json={"doc_id": "d", "doc_text": DOC_TEXT, "expected_type": "not-a-type"},
    )

    assert response.status_code == 422


def test_api_rejects_an_empty_document(queue: JobQueue, queue_env: None) -> None:
    response = TestClient(api.app).post("/pipeline/run", json={"doc_id": "d", "doc_text": ""})

    assert response.status_code == 422


# ─── Criterion 3: the worker processes a job end to end ─────────────────────


def test_worker_processes_a_job_and_saves_trace_and_diagnosis(
    storage: Storage, queue: JobQueue
) -> None:
    job = queue.enqueue("acme", _payload("doc_001"))
    pipeline = FakePipeline()
    worker = PipelineWorker(storage, queue, pipeline=pipeline, worker_id="w1")

    processed = asyncio.run(worker.run_once(pipeline))

    assert processed == 1
    completed = queue.get_job(job.job_id)
    assert completed is not None
    assert completed.status is JobStatus.COMPLETED
    assert completed.error is None
    assert completed.attempts == 1
    assert completed.worker_id == "w1"
    assert completed.result is not None

    trace_id = completed.result["trace_id"]
    trace = storage.get_trace(trace_id)
    assert trace is not None
    assert trace.document_id == "doc_001"
    # The job's tenant decides who can read the records it produced.
    assert trace.tenant_id == "acme"
    assert storage.get_trace(trace_id, tenant_id="other") is None

    diagnosis = storage.get_diagnosis(completed.result["diagnosis_id"])
    assert diagnosis is not None
    assert diagnosis.tenant_id == "acme"
    assert pipeline.calls == [DOC_TEXT]


def test_worker_processes_a_whole_batch(
    storage: Storage, queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUEUE_WORKER_CONCURRENCY", "2")
    reset_settings()
    for index in range(5):
        queue.enqueue("acme", _payload(f"doc_{index}"))
    worker = PipelineWorker(storage, queue, pipeline=FakePipeline(), worker_id="w1")

    first = asyncio.run(worker.run_once(FakePipeline()))
    second = asyncio.run(worker.run_once(FakePipeline()))
    third = asyncio.run(worker.run_once(FakePipeline()))

    assert (first, second, third) == (2, 2, 1)
    assert queue.counts()["completed"] == 5
    assert queue.counts()["pending"] == 0


def test_worker_reports_nothing_to_do(storage: Storage, queue: JobQueue) -> None:
    worker = PipelineWorker(storage, queue, pipeline=FakePipeline())

    assert asyncio.run(worker.run_once(FakePipeline())) == 0


def test_worker_leaves_running_jobs_for_other_workers(storage: Storage, queue: JobQueue) -> None:
    """A batch that another worker already claimed is not processed twice."""
    queue.enqueue("acme", _payload())
    other = queue.dequeue("other-worker", 1)
    assert len(other) == 1
    worker = PipelineWorker(storage, queue, pipeline=FakePipeline())

    assert asyncio.run(worker.run_once(FakePipeline())) == 0


# ─── Criterion 4: GET /jobs/{id} ────────────────────────────────────────────


def test_get_job_returns_status_and_result(
    api_state: Storage, queue: JobQueue, storage: Storage
) -> None:
    job = queue.enqueue(DEFAULT_TENANT_ID, _payload())
    client = TestClient(api.app)

    pending = client.get(f"/jobs/{job.job_id}")
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"
    assert pending.json()["result"] is None

    worker = PipelineWorker(storage, queue, pipeline=FakePipeline(), worker_id="w1")
    asyncio.run(worker.run_once(FakePipeline()))

    completed = client.get(f"/jobs/{job.job_id}")
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["result"]["trace_id"]


def test_get_job_404s_for_an_unknown_id(api_state: Storage, queue: JobQueue) -> None:
    assert TestClient(api.app).get("/jobs/does-not-exist").status_code == 404


def test_get_job_404s_for_another_tenant(
    api_state: Storage,
    queue: JobQueue,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cross-tenant job is indistinguishable from a missing one."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_BCRYPT_ROUNDS", "4")
    reset_settings()

    from forensics.auth import AuthManager

    auth = AuthManager(storage, prefix="ffk_", rounds=4)
    acme = auth.create_tenant("acme")
    other = auth.create_tenant("other")
    _, acme_key = auth.create_api_key(acme.tenant_id)
    _, other_key = auth.create_api_key(other.tenant_id)
    monkeypatch.setattr(api, "_auth", auth)

    job = queue.enqueue(acme.tenant_id, _payload())
    client = TestClient(api.app)

    mine = client.get(f"/jobs/{job.job_id}", headers={"Authorization": f"Bearer {acme_key}"})
    theirs = client.get(f"/jobs/{job.job_id}", headers={"Authorization": f"Bearer {other_key}"})

    assert mine.status_code == 200
    assert theirs.status_code == 404


def test_list_jobs_filters_by_status(
    api_state: Storage,
    queue: JobQueue,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUEUE_WORKER_CONCURRENCY", "1")
    reset_settings()
    first = queue.enqueue(DEFAULT_TENANT_ID, _payload("doc_a"))
    queue.enqueue(DEFAULT_TENANT_ID, _payload("doc_b"))
    worker = PipelineWorker(storage, queue, pipeline=FakePipeline(), worker_id="w1")
    asyncio.run(worker.run_once(FakePipeline()))

    client = TestClient(api.app)
    completed = client.get("/jobs", params={"status": "completed"})

    assert completed.status_code == 200
    assert [j["job_id"] for j in completed.json()] == [first.job_id]
    assert len(client.get("/jobs").json()) == 2
    assert client.get("/jobs", params={"status": "nope"}).status_code == 422


# ─── Criterion 5: retry, then fail ──────────────────────────────────────────


def test_failure_is_retried_until_max_attempts_then_fails(
    storage: Storage, queue: JobQueue
) -> None:
    job = queue.enqueue("acme", _payload(), max_attempts=3)

    statuses = []
    for _ in range(3):
        claimed = queue.dequeue("w1", 1)
        assert len(claimed) == 1
        statuses.append(queue.fail(job.job_id, "boom").value)

    assert statuses == ["pending", "pending", "failed"]
    final = queue.get_job(job.job_id)
    assert final is not None
    assert final.status is JobStatus.FAILED
    assert final.error == "boom"
    assert final.attempts == 3
    assert final.completed_at is not None
    # A terminal job is not claimable again.
    assert queue.dequeue("w2", 1) == []


def test_retry_keeps_the_payload_but_a_terminal_failure_does_not(
    storage: Storage, queue: JobQueue
) -> None:
    """Spec 03 promises the unredacted original never rests on disk.

    A retry still needs the document text; a finished job does not, so the queue
    stops storing it.
    """
    job = queue.enqueue("acme", _payload(), max_attempts=1)
    queue.dequeue("w1", 1)

    assert queue.fail(job.job_id, "boom") is JobStatus.FAILED

    assert queue.get_job(job.job_id).payload == {}  # type: ignore[union-attr]


def test_a_failing_job_does_not_stop_the_worker(storage: Storage, queue: JobQueue) -> None:
    """One bad job must not take the batch (or the process) down."""
    broken = queue.enqueue("acme", {"doc_id": "missing-text"}, max_attempts=1)
    good = queue.enqueue("acme", _payload("doc_ok"))
    worker = PipelineWorker(storage, queue, pipeline=FakePipeline(), worker_id="w1")

    processed = asyncio.run(worker.run_once(FakePipeline()))

    assert processed == 2
    assert queue.get_job(broken.job_id).status is JobStatus.FAILED  # type: ignore[union-attr]
    error = queue.get_job(broken.job_id).error  # type: ignore[union-attr]
    assert error is not None and "KeyError" in error
    assert queue.get_job(good.job_id).status is JobStatus.COMPLETED  # type: ignore[union-attr]


def test_a_broken_worker_pipeline_records_the_failure(storage: Storage, queue: JobQueue) -> None:
    class Exploding(FakePipeline):
        async def run(self, document: Document, document_id: str = "") -> None:
            raise RuntimeError("model provider is down")

    job = queue.enqueue("acme", _payload(), max_attempts=1)
    worker = PipelineWorker(storage, queue, pipeline=Exploding(), worker_id="w1")

    asyncio.run(worker.run_once(Exploding()))

    failed = queue.get_job(job.job_id)
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error is not None and "RuntimeError" in failed.error


def test_worker_survives_a_failure_in_its_own_bookkeeping(
    storage: Storage, queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If recording the failure fails too, the loop must still continue."""
    queue.enqueue("acme", {"doc_id": "missing-text"}, max_attempts=1)

    def explode(*_: Any, **__: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(queue, "fail", explode)
    worker = PipelineWorker(storage, queue, pipeline=FakePipeline(), worker_id="w1")

    assert asyncio.run(worker.run_once(FakePipeline())) == 1


# ─── Criterion 6: multiple workers claim distinct jobs ─────────────────────


def test_two_workers_claim_distinct_jobs(storage: Storage, queue: JobQueue) -> None:
    for index in range(5):
        queue.enqueue("acme", _payload(f"doc_{index}"))

    first = queue.dequeue("w1", 3)
    second = queue.dequeue("w2", 3)

    claimed = [job.job_id for job in first] + [job.job_id for job in second]
    assert len(claimed) == 5
    assert len(set(claimed)) == 5
    assert all(job.status is JobStatus.RUNNING for job in first + second)
    assert {job.worker_id for job in first} == {"w1"}
    assert {job.worker_id for job in second} == {"w2"}


def test_dequeue_is_atomic_under_threads(storage: Storage, queue: JobQueue) -> None:
    """20 jobs, 4 threads racing: every job is claimed exactly once."""
    total = 20
    for index in range(total):
        queue.enqueue("acme", _payload(f"doc_{index}"))

    claimed: list[str] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def worker(worker_id: str) -> None:
        try:
            while True:
                jobs = queue.dequeue(worker_id, 1)
                if not jobs:
                    return
                with lock:
                    claimed.extend(job.job_id for job in jobs)
        except BaseException as error:
            # Recorded and asserted below; a thread cannot assert usefully.
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(f"w{index}",)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(claimed) == total
    assert len(set(claimed)) == total


# ─── Criterion 7: `forensics worker start` runs until stopped ───────────────


def test_worker_loop_exits_when_stopped(storage: Storage, queue: JobQueue) -> None:
    """The loop processes its job and then exits, on a real `run()`."""
    job = queue.enqueue("acme", _payload())
    stopping = FakePipeline()
    worker = PipelineWorker(storage, queue, pipeline=stopping, worker_id="w1")
    # Stop from inside the job, so the loop's next check sees the flag.
    stopping._on_run = worker.stop

    asyncio.run(worker.run())

    assert queue.get_job(job.job_id).status is JobStatus.COMPLETED  # type: ignore[union-attr]
    assert stopping.calls == [DOC_TEXT]


def test_worker_loop_exits_when_stopped_while_idle(
    storage: Storage, queue: JobQueue, queue_env: None
) -> None:
    """An idle loop must not spin or hang once told to stop."""
    worker = PipelineWorker(storage, queue, pipeline=FakePipeline(), worker_id="w1")

    async def go() -> None:
        task = asyncio.create_task(worker.run())
        # Let the first (empty) poll happen before stopping.
        await asyncio.sleep(0.05)
        worker.stop()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(go())

    assert worker._running is False


def test_stop_is_idempotent(storage: Storage, queue: JobQueue) -> None:
    worker = PipelineWorker(storage, queue, pipeline=FakePipeline())

    worker.stop()
    worker.stop()

    assert asyncio.run(worker.run_once(FakePipeline())) == 0


def test_signal_handler_marks_the_worker_for_shutdown(storage: Storage, queue: JobQueue) -> None:
    worker = PipelineWorker(storage, queue, pipeline=FakePipeline())
    worker._running = True

    worker._on_signal(15, None)

    assert worker._running is False


def test_signal_handlers_are_not_installed_off_the_main_thread(
    storage: Storage, queue: JobQueue
) -> None:
    """`loop.add_signal_handler` from a worker thread raises; degrade, don't crash."""
    worker = PipelineWorker(storage, queue, pipeline=FakePipeline())
    installed: list[bool] = []

    def install() -> None:
        installed.append(worker.install_signal_handlers())

    thread = threading.Thread(target=install)
    thread.start()
    thread.join(timeout=10)

    assert installed == [False]


def test_cli_worker_start_runs_the_worker(queue: JobQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wiring only: the loop itself is covered above."""
    monkeypatch.setattr("forensics.cli.validate_startup", lambda: None)
    started: list[bool] = []

    async def fake_run(self: PipelineWorker) -> None:
        started.append(True)

    monkeypatch.setattr(PipelineWorker, "run", fake_run)

    result = CliRunner().invoke(cli_app, ["worker", "start"])

    assert result.exit_code == 0, result.output
    assert started == [True]
    assert "Worker starting" in result.output


# ─── Criterion 8: TTL cleanup ───────────────────────────────────────────────


def test_cleanup_deletes_only_old_finished_jobs(storage: Storage, queue: JobQueue) -> None:
    finished = queue.enqueue("acme", _payload("finished"))
    queue.dequeue("w1", 1)
    queue.complete(finished.job_id, {"trace_id": "t1"})

    claimed = queue.enqueue("acme", _payload("claimed"))
    still_pending = queue.enqueue("acme", _payload("still_pending"))
    # FIFO on created_at, so this claims `claimed` and leaves `still_pending`.
    assert [job.job_id for job in queue.dequeue("w2", 1)] == [claimed.job_id]

    _backdate(storage.db_path, finished.job_id, hours=2)

    removed = queue.cleanup(ttl_seconds=3600)

    assert removed == 1
    assert queue.get_job(finished.job_id) is None
    assert queue.get_job(claimed.job_id) is not None
    assert queue.get_job(still_pending.job_id) is not None


def _backdate(db_path: Path, job_id: str, *, hours: float) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE job_queue SET completed_at = ? WHERE job_id = ?",
            ((datetime.now(UTC) - timedelta(hours=hours)).isoformat(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_cleanup_requires_a_ttl(storage: Storage) -> None:
    without_ttl = JobQueue(storage.db_path)

    with pytest.raises(ValueError, match="No TTL configured"):
        without_ttl.cleanup()


# ─── Queue mechanics worth pinning ─────────────────────────────────────────


def test_queue_is_wal_mode(storage: Storage, queue: JobQueue) -> None:
    """WAL is what lets producers write while a worker reads."""
    conn = sqlite3.connect(storage.db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()

    assert mode.lower() == "wal"


def test_jobs_are_claimed_highest_priority_first(storage: Storage, queue: JobQueue) -> None:
    low = queue.enqueue("acme", _payload("low"), priority=0)
    high = queue.enqueue("acme", _payload("high"), priority=10)
    middle = queue.enqueue("acme", _payload("middle"), priority=5)

    claimed = [job.job_id for job in queue.dequeue("w1", 3)]

    assert claimed == [high.job_id, middle.job_id, low.job_id]


def test_claiming_increments_attempts_and_stamps_the_worker(
    storage: Storage, queue: JobQueue
) -> None:
    job = queue.enqueue("acme", _payload())

    claimed = queue.dequeue("worker-7", 1)[0]

    assert claimed.job_id == job.job_id
    assert claimed.attempts == 1
    assert claimed.worker_id == "worker-7"
    assert claimed.started_at is not None


def test_enqueue_validates_its_inputs(queue: JobQueue) -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        queue.enqueue("", _payload())
    with pytest.raises(ValueError, match="max_attempts"):
        queue.enqueue("acme", _payload(), max_attempts=0)


def test_complete_returns_false_for_an_unknown_job(queue: JobQueue) -> None:
    assert queue.complete("nope", {}) is False


def test_job_payload_of_a_completed_job_is_readable_json(queue: JobQueue) -> None:
    """The row is still valid JSON after clearing, so readers never crash."""
    job = queue.enqueue("acme", _payload())
    queue.dequeue("w1", 1)
    queue.complete(job.job_id, {})

    conn = sqlite3.connect(queue.db_path)
    try:
        stored = conn.execute(
            "SELECT payload FROM job_queue WHERE job_id = ?", (job.job_id,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert json.loads(stored) == {}


def test_counts_reports_every_status(queue: JobQueue) -> None:
    done = queue.enqueue("acme", _payload("done_doc"))
    queue.enqueue("acme", _payload("pending_doc"))
    # FIFO on created_at: this claims `done` and leaves `pending_doc` queued.
    queue.dequeue("w1", 1)
    queue.complete(done.job_id, {})

    counts = queue.counts()

    assert counts == {"pending": 1, "running": 0, "completed": 1, "failed": 0}


# ─── CLI ───────────────────────────────────────────────────────────────────


def test_cli_enqueue_prints_a_job_id(queue: JobQueue, tmp_path: Path) -> None:
    document = tmp_path / "invoice_007.txt"
    document.write_text(DOC_TEXT)

    result = CliRunner().invoke(
        cli_app, ["queue", "enqueue", "--file", str(document), "--expected", "invoice"]
    )

    assert result.exit_code == 0, result.output
    job_id = result.stdout.strip().splitlines()[-1]
    job = queue.get_job(job_id)
    assert job is not None
    assert job.payload["doc_id"] == "invoice_007"  # defaults to the file stem
    assert job.payload["expected_type"] == "invoice"


def test_cli_enqueue_rejects_an_unknown_expected_type(queue: JobQueue, tmp_path: Path) -> None:
    document = tmp_path / "doc.txt"
    document.write_text(DOC_TEXT)

    result = CliRunner().invoke(
        cli_app, ["queue", "enqueue", "--file", str(document), "--expected", "nope"]
    )

    assert result.exit_code == 1
    assert "Unknown --expected" in result.output
    assert queue.list_jobs() == []


def test_cli_queue_status_shows_the_result(queue: JobQueue, storage: Storage) -> None:
    job = queue.enqueue("acme", _payload())
    worker = PipelineWorker(storage, queue, pipeline=FakePipeline(), worker_id="w1")
    asyncio.run(worker.run_once(FakePipeline()))

    result = CliRunner().invoke(cli_app, ["queue", "status", "--job-id", job.job_id])

    assert result.exit_code == 0, result.output
    assert "completed" in result.output
    assert "trace_id" in result.output


def test_cli_queue_status_fails_for_an_unknown_job(queue: JobQueue) -> None:
    result = CliRunner().invoke(cli_app, ["queue", "status", "--job-id", "nope"])

    assert result.exit_code == 1
    assert "No job with id" in result.output


def test_cli_queue_list_filters(queue: JobQueue) -> None:
    done = queue.enqueue("acme", _payload("done_doc"))
    pending = queue.enqueue("acme", _payload("pending_doc"))
    # FIFO on created_at: claims `done`, leaving `pending_doc` queued.
    queue.dequeue("w1", 1)
    queue.complete(done.job_id, {})

    listed = CliRunner().invoke(cli_app, ["queue", "list", "--status", "pending"])
    completed = CliRunner().invoke(cli_app, ["queue", "list", "--status", "completed"])

    assert listed.exit_code == 0, listed.output
    assert pending.job_id in listed.stdout
    assert done.job_id not in listed.stdout
    assert completed.exit_code == 0, completed.output
    assert done.job_id in completed.stdout


def test_cli_queue_list_rejects_an_unknown_status(queue: JobQueue) -> None:
    result = CliRunner().invoke(cli_app, ["queue", "list", "--status", "nope"])

    assert result.exit_code == 1
    assert "Unknown --status" in result.output


def test_cli_queue_list_reports_an_empty_queue(queue: JobQueue) -> None:
    result = CliRunner().invoke(cli_app, ["queue", "list"])

    assert result.exit_code == 0
    assert "No jobs match" in result.output


# ─── process_document (the shared path) ────────────────────────────────────


def test_process_document_stamps_both_records_with_the_tenant(storage: Storage) -> None:
    from forensics.worker import process_document

    async def go() -> dict[str, Any]:
        return await process_document(
            FakePipeline(),
            storage,
            doc_id="doc_001",
            doc_text=DOC_TEXT,
            expected_type="invoice",
            tenant_id="acme",
        )

    result = asyncio.run(go())

    assert storage.get_trace(result["trace_id"], tenant_id="acme") is not None
    assert storage.get_trace(result["trace_id"], tenant_id="other") is None
    assert storage.get_diagnosis(result["diagnosis_id"], tenant_id="acme") is not None
    assert result["status"] in {"success", "degraded", "failure"}


def test_job_model_lists_every_status() -> None:
    """The API's `?status=` filter is validated against this enum."""
    assert [status.value for status in JobStatus] == [
        "pending",
        "running",
        "completed",
        "failed",
    ]
    assert "status" in Job.model_fields
