"""Pipeline worker (spec 07).

Pulls jobs off the SQLite queue and runs the same `process_document` the API uses
in synchronous mode, so a document processed inline and one processed off the
queue produce identical records.

Notes on the concurrency:

* One worker claims a batch (`QUEUE_WORKER_CONCURRENCY`) per poll and processes
  the batch concurrently, so N workers give N times the concurrency in-flight.
* Signal handlers are best-effort. `loop.add_signal_handler` only works on the
  main thread of the main interpreter, and raises `NotImplementedError` on
  Windows; when it is unavailable the loop still exits on `stop()`, which is what
  the tests and an embedding caller use.
* A crashed worker leaves its claimed job `running` forever. There is no reaper;
  that is a documented gap, not an oversight — see `docs/limitations.md`.
"""

import asyncio
import logging
import signal
import threading
import time
import uuid
from typing import Any

from forensics.analyzer_simple import analyze_trace
from forensics.documents import has_dataset_document, store_document
from forensics.llm import using_llm_client
from forensics.models import DEFAULT_TENANT_ID, Document
from forensics.pipeline import Pipeline
from forensics.queue import Job, JobQueue
from forensics.storage import Storage
from forensics.tracing import trace_context

logger = logging.getLogger(__name__)

#: A long-running worker looks for expired jobs at most this often.
_CLEANUP_INTERVAL_SECONDS = 3600.0


async def process_document(
    pipeline: Pipeline,
    storage: Storage,
    *,
    doc_id: str,
    doc_text: str,
    expected_type: str | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Run the pipeline on one document and persist the trace and diagnosis.

    Shared by the API's synchronous mode and by the worker. The tenant is passed
    in and stamped on both records: a job enqueued by tenant A must not write a
    record that tenant B can read.
    """
    document = Document(content=doc_text)
    with trace_context(document_id=doc_id, tenant_id=tenant_id) as ctx:
        await pipeline.run(document, document_id=doc_id)
        trace = ctx.trace

    if trace is None:
        raise RuntimeError("pipeline produced no trace")

    # Persist an uploaded document so `POST /traces/{id}/diagnose` and the eval
    # loop can resolve it later. Without this every trace created through
    # `POST /pipeline/run` answered 404 on diagnose and was silently skipped by
    # the evaluator. Dataset documents are never copied or shadowed.
    if not has_dataset_document(doc_id):
        store_document(doc_id, doc_text)

    # Analyze *before* `save_trace`: finalize_trace redacts the trace in place
    # when PII_REDACTION_ENABLED is on, and an entity-grounding check run against
    # a redacted payload flags every real entity as a hallucination. The
    # diagnosis is scrubbed at its own write boundary (`create_diagnosis`).
    #
    # Off the event loop: the analyzer does O(N·M) fuzzy matching per entity and,
    # with JUDGE_ENABLED, blocks on a thread-pool future. Running it inline
    # stalled every other task in the worker's `asyncio.gather` batch.
    diagnosis = await asyncio.to_thread(analyze_trace, trace, doc_text, expected_type)
    diagnosis.tenant_id = tenant_id
    storage.save_trace(trace)
    storage.create_diagnosis(diagnosis)

    return {
        "trace_id": trace.trace_id,
        "diagnosis_id": diagnosis.diagnosis_id,
        "root_cause_step": diagnosis.root_cause_step,
        "status": trace.status.value,
    }


class PipelineWorker:
    """Claims and processes jobs until stopped."""

    def __init__(
        self,
        storage: Storage,
        queue: JobQueue,
        pipeline: Pipeline | None = None,
        worker_id: str | None = None,
    ) -> None:
        from forensics.config import get_settings

        settings = get_settings()
        self.storage = storage
        self.queue = queue
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.concurrency = settings.queue_worker_concurrency
        self.poll_interval = settings.queue_poll_interval
        self.job_ttl = settings.queue_job_ttl
        # Injected by tests; `run()` builds one from a real LLM client otherwise.
        self._pipeline = pipeline
        self._running = False

    # --- lifecycle ---

    def stop(self) -> None:
        """Ask `run()` to exit after the current batch."""
        self._running = False

    def _on_signal(self, *_: Any) -> None:
        logger.info("Shutdown signal received; finishing the current batch")
        self.stop()

    def install_signal_handlers(self) -> bool:
        """Register SIGTERM/SIGINT handlers if this thread can own them."""
        if threading.current_thread() is not threading.main_thread():
            return False
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self._on_signal)
        except (NotImplementedError, RuntimeError, ValueError):
            # No running loop, or a platform without signalfd support.
            return False
        return True

    # --- work ---

    async def run_once(self, pipeline: Pipeline) -> int:
        """Claim and process one batch. Returns how many jobs ran."""
        jobs = self.queue.dequeue(self.worker_id, self.concurrency)
        if not jobs:
            return 0
        await asyncio.gather(*(self._process_job(pipeline, job) for job in jobs))
        return len(jobs)

    async def _process_job(self, pipeline: Pipeline, job: Job) -> None:
        try:
            result = await process_document(
                pipeline,
                self.storage,
                doc_id=job.payload["doc_id"],
                doc_text=job.payload["doc_text"],
                expected_type=job.payload.get("expected_type"),
                tenant_id=job.tenant_id,
            )
        except Exception as error:
            logger.exception("Job %s failed", job.job_id)
            try:
                status = self.queue.fail(job.job_id, f"{type(error).__name__}: {error}")
            except Exception:
                # Bookkeeping must never take the worker down: the job stays
                # `running` and the loop keeps serving other jobs.
                logger.exception("Could not record the failure of job %s", job.job_id)
            else:
                logger.info("Job %s recorded as %s", job.job_id, status.value)
            return

        self.queue.complete(job.job_id, result)

    async def run(self) -> None:
        """Process jobs until `stop()` or a termination signal.

        `run()` owns the loop's lifetime: it sets the flag at the start, so a
        `stop()` issued before it is meaningless rather than a silent no-op that
        leaves the loop running forever.
        """
        self._running = True
        if not self.install_signal_handlers():
            logger.info("Signal handlers unavailable; stop() or SIGKILL only")

        if self._pipeline is not None:
            await self._loop(self._pipeline)
            return

        async with using_llm_client() as client:
            await self._loop(Pipeline(client=client))

    async def _loop(self, pipeline: Pipeline) -> None:
        logger.info(
            "Worker %s polling (concurrency=%d, interval=%.2fs)",
            self.worker_id,
            self.concurrency,
            self.poll_interval,
        )
        last_cleanup = time.monotonic()
        while self._running:
            processed = await self.run_once(pipeline)
            if processed == 0:
                await asyncio.sleep(self.poll_interval)
            if self.job_ttl and time.monotonic() - last_cleanup >= _CLEANUP_INTERVAL_SECONDS:
                removed = self.queue.cleanup(self.job_ttl)
                if removed:
                    logger.info("Cleaned up %d expired jobs", removed)
                last_cleanup = time.monotonic()


async def run_worker() -> None:
    """Entry point for the `forensics worker start` command."""
    from forensics.queue import get_queue

    worker = PipelineWorker(Storage(), get_queue())
    await worker.run()


__all__ = ["PipelineWorker", "process_document", "run_worker"]
