# C5. Tracing Design (as implemented)

Status: implemented in `src/forensics/tracing.py` and `src/forensics/otel_export.py`.
Pinned by `tests/test_tracing_phase2.py` and `tests/test_otel_export.py`.

## 5.1 Span lifecycle

1. `traced_step(name)` wraps a step. It drops `self` from the captured input
   (`_span_payload`) because the pipeline object transitively holds the API key.
2. The decorator resolves `inspect.iscoroutinefunction(func)` **before** calling
   it. Async steps are awaited through `_await_and_record`, so the span brackets
   the awaited work and exactly one span is recorded per step.
3. On completion (or exception) `record()` appends a `Span` and sets
   `_current_span`.

## 5.2 Attributes captured per span

| Field | Source | Notes |
|---|---|---|
| `model_id`, `provider` | `StructuredResponse` | `n/a` when the step made no model call |
| `prompt` | the exact prompt sent | truncated to 4000 chars on OTel export only |
| `raw_response` | `response.content` | falls back to the serialized output |
| `prompt_tokens`, `completion_tokens`, `reasoning_tokens` | `TokenUsage` | |
| `latency_ms`, `started_at`, `ended_at` | measured | |
| `status` | `"success"` / `"failure"` | |
| `confidence` | 1-5, default 3 | see 5.6 |

**Telemetry side-channel.** The LLM client publishes a `StepTelemetry` via
`record_telemetry()`; `traced_step` consumes it with `_take_telemetry()`, which
clears the slot so telemetry cannot bleed into the next span. This keeps the
step signatures unchanged while making traces auditable.

## 5.3 Errors and partial failures

- A step that raises records `status="failure"` with `error` set, then re-raises.
- Telemetry published before the raise is still attached, so a post-parse
  failure does not lose the model id or token counts (the cost was real).
- OTel export maps `failure` to `StatusCode.ERROR`. `degraded` is deliberately
  **not** an error, so a suspicious-but-completed step does not page anyone.

## 5.4 Trace status computation

`compute_trace_status(spans)` — failure dominates degraded:

```
failure present -> FAILURE
else degraded   -> DEGRADED
else            -> SUCCESS
```

`trace_context` forwards the exception to `__exit__`, so an uncaught error also
forces `FAILURE`. Crucially it does **not** rely on `exc_type` alone: a failure
the caller caught still leaves the trace marked `FAILURE`.

## 5.5 Persistence

`finalize_trace()` is the single write path. It derives status, aggregates
`total_tokens` and `total_cost_usd` from spans, writes JSON atomically
(tmp + `os.replace`), writes the SQLite index, and optionally mirrors to OTel.
`Storage.save_trace()` routes through it so a trace saved via the API carries the
same totals as one saved by the CLI.

Cost uses config-driven prices per 1M tokens. **These are assumptions, not an
invoice**; an unknown model prices at `0.0` rather than guessing upward.

## 5.6 Confidence score

Stored on `Span.confidence` (1-5, default 3, neutral).

Per `known_weaknesses_you_must_address` item 1, self-reported confidence is a
**weak signal**: it is collected and stored, but it is NOT validated against
known-good/known-bad labels and must not be treated as ground truth. No component
currently reads it for scoring. Validating its usefulness requires the eval
harness and a labelled set; until then it is a recorded prior only.

## 5.7 OpenTelemetry

Additive and optional. Forensic spans are the source of truth; a telemetry
backend being unreachable must never fail a pipeline run, so `_export_otel`
swallows exporter errors after the JSON + SQLite writes have already succeeded.

Two independent paths:

- `export_trace_to_otel(trace, exporter)` — replay a finished trace.
- `ForensicsSpanProcessor` — a real `SpanProcessor` for operators who wire the
  SDK themselves. It builds **one** provider for its lifetime, not one per span.

Attribute keys (pinned by `test_genai_attribute_keys_match_the_spec`):

```
gen_ai.system, gen_ai.operation.name, gen_ai.request.model,
gen_ai.response.model, gen_ai.provider.name,
gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, gen_ai.prompt,
forensics.step.name, forensics.trace_id, forensics.span_id,
forensics.confidence, forensics.document_id, forensics.trace.status,
forensics.trace.total_tokens, forensics.trace.total_cost_usd
```

Deterministic steps (`intake`) are labelled `gen_ai.operation.name="pipeline.transform"`
with no model or token attributes. Reporting them as `chat` would be a lie.

## 5.8 Known gaps

- `parent_span_id` is never set; all four steps are currently siblings.
- Latency in a dry run reflects no network time, so `latency_ms` is not a
  performance measurement unless `DRY_RUN=false`.
- No sampling. Every trace is written.
