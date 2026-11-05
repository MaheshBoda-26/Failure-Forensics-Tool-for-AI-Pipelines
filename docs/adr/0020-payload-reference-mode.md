# ADR 0020: Queue Payload Reference Mode

## Status
Accepted

## Context
`POST /pipeline/run` inlines `doc_text` into `job_queue.payload` so a worker can
process the job without reading the document from storage. This is the simplest
correct design, and payloads are cleared when a job reaches a terminal state.
But while a job is pending or running, raw document text sits unredacted in the
same SQLite file as the (redacted) traces — the exposure window is the job's
lifetime. `docs/limitations.md` §5 recorded this.

The queue is pipeline *input*: redacting the payload would change what the model
sees, so redaction is not a valid fix. The text also already exists on disk for
uploaded documents (`store_document`), which makes a reference-only payload
viable where a document is persisted.

## Decision
Add `QUEUE_PAYLOAD_MODE` (`inline` | `reference`, default `inline`).

- **`inline`** (default): `doc_text` is stored in the payload exactly as before,
  preserving current behaviour and support for documents that exist only in the
  dataset.
- **`reference`**: `POST /pipeline/run` stores only `{doc_id, expected_type}` —
  no `doc_text`. `Worker._process_job` resolves the text with
  `read_document(doc_id, tenant_id)` at claim time.
- Because resolution happens later, the endpoint validates it *synchronously* at
  enqueue time: a document that is neither a dataset document nor a persisted
  upload fails immediately with a clear error, so the caller learns before a
  worker claims the job.
- Uploaded text is redacted before it is persisted (when redaction is enabled),
  so reference mode never introduces a new plaintext copy.

## Rationale
- The default must not change: an upload on an ephemeral disk would disappear
  before the worker claims it, so `reference` cannot be the default.
- Enqueue-time validation preserves the synchronous error contract the inline
  path gives, instead of moving the failure to an asynchronous claim.
- No new storage or broker is needed; the document store already exists.

## Consequences
- A deployment that cannot accept even a short-lived plaintext window can opt
  into reference mode and keep raw text out of `job_queue` entirely.
- Reference mode depends on the document surviving until claim time; a
  document deleted between enqueue and claim fails the job with an actionable,
  non-retryable error.
- Neither mode is secure erasure: SQLite retains WAL and freelist pages, so a
  determined reader could recover plaintext from the file. The limitation says
  so explicitly.

## Verification
- `tests/test_queue.py`: enqueue in reference mode stores no document text;
  the worker processes the job end to end; a missing document fails terminally
  with an actionable error.
- `docs/limitations.md` §5 documents inline vs reference and the residual
  WAL/freelist caveat.
