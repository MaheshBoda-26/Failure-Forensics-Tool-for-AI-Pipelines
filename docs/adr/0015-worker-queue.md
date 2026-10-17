# ADR 0015: SQLite Worker Queue for Pipeline Runs

## Status
Accepted

## Context
`POST /pipeline/run-demo` runs 15 documents through a four-step LLM pipeline
inside the request. That is minutes of wall clock on a single HTTP connection,
and it holds the API process for the duration of every document. The deployment
target is one small web service, so Celery/RQ/Redis is a second service and a
second failure mode for a workload that is, at demo scale, a handful of jobs.

## Decision
- **The queue is a table in the same SQLite file** as traces and eval data. No
  broker, no second database, no extra service.
- **Claims are atomic**: one `UPDATE … RETURNING` selects the highest-priority
  pending job, marks it running, stamps the worker and increments `attempts`, and
  returns the row. A racing worker's subquery finds it already running.
- **`busy_timeout=5000` on every connection**, plus WAL. Without the timeout the
  second writer raises `database is locked` instead of waiting, which would make
  multi-worker operation false in practice even with an atomic claim.
- **One `process_document` serves every path** — inline (`QUEUE_ENABLED=false`),
  queued (worker), and the CLI. A document processed inline and one processed off
  the queue produce identical records, and both stamp the job's `tenant_id` on
  the trace *and* the diagnosis.
- **A job's payload is cleared when it reaches a terminal state.** The payload
  holds the raw document text; spec 03 promises the unredacted original never
  rests on disk, and a 24-hour retention window would break that for every queued
  job. Retries keep it; `completed`/`failed` do not.
- **`run_once(pipeline)` is the seam.** `run()` owns the LLM client lifecycle and
  the loop; `run_once` claims and processes exactly one batch. Signal handlers are
  best-effort (main thread only, absent on Windows), so the loop is always
  stoppable via `stop()`.
- **`expected_type` is constrained to `DocumentType`** at the API boundary: it is
  ground truth for the analyzer's label check, and an arbitrary string would be
  accepted and then never match.

## Rationale
- A queue that needs an orchestrator to be useful is not worth adding to a
  single-service demo; a queue that is *safe* with two processes (atomic claims,
  busy timeout, retries) is worth the table.
- Clearing the payload on completion gets the privacy property (bounded exposure)
  without corrupting the pipeline's input, which redaction-in-place would.
- Failing a job leaves the loop running: a bookkeeping failure (`queue.fail`
  itself raising) is caught and logged, because a worker that dies on a database
  hiccup loses every subsequent job.

## Consequences
- One writer at a time (SQLite), so throughput is bounded by write frequency
  rather than CPU (`docs/limitations.md` §12).
- No reaper: a worker killed mid-job leaves that job `running` forever (§2).
- No per-tenant fairness or dead-letter queue (§17).
- Jobs are JSON payloads in a SQL column, so a large document is copied into the
  queue; `doc_id`-only payloads are the alternative if that is unacceptable.

## Verification
- `tests/test_queue.py` — 43 tests over all eight spec-07 criteria, including
  priority ordering, distinct claims from two workers, 20 jobs claimed exactly
  once by 4 racing threads, retry-then-fail at `max_attempts`, payload clearing on
  both terminal states, TTL cleanup that leaves pending/running jobs alone, and
  the SIGTERM path.
- Live check: a real `forensics worker start` process claimed a job enqueued
  through `POST /pipeline/run`, wrote the trace and diagnosis, exited on SIGTERM,
  and the API then served the resulting trace and diagnosis.
