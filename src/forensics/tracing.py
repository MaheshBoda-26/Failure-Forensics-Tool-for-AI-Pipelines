"""Tracing layer — span lifecycle and the `@traced_step` decorator.

Uses a custom span processor that writes JSON + SQLite. Instrumenting a new step
must cost one line. Also owns trace status computation (success / failure /
degraded) and how the self-reported confidence score is requested and stored.
"""

import contextvars
import inspect
import json
import os
import sqlite3
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from forensics.config import get_settings
from forensics.migrations import migrate
from forensics.models import DEFAULT_TENANT_ID, Span, Trace, TraceStatus
from forensics.otel_export import DEFAULT_MAX_PROMPT_CHARS, ForensicsSpanProcessor, configure_otel
from forensics.pii import create_redactor_from_settings

F = TypeVar("F", bound=Callable[..., Any])

# Context variables for current trace/span
_current_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar(
    "_current_trace", default=None
)
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "_current_span", default=None
)


@dataclass
class StepTelemetry:
    """Real LLM provenance for one step, published by the step itself.

    This exists because a span that records `model_id="fake-model"` with zero
    tokens cannot answer "what did this run cost" or "which model was wrong".
    """

    model_id: str
    provider: str
    prompt: str = ""
    raw_response: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    confidence: int | None = None
    cached: bool = False


# Telemetry published by a step for the decorator to attach to its span.
#
# The LLM client knows the model, tokens and cost; the decorator knows the
# span. Rather than threading a wrapper object through every step signature,
# the step publishes a side-channel record that `traced_step` consumes
# immediately after the call returns. ContextVar keeps this correct under
# concurrency: each task gets its own slot.
_pending_telemetry: contextvars.ContextVar[StepTelemetry | None] = contextvars.ContextVar(
    "_pending_telemetry", default=None
)


def record_telemetry(telemetry: StepTelemetry) -> None:
    """Publish telemetry for the step currently being traced."""
    _pending_telemetry.set(telemetry)


def _take_telemetry() -> StepTelemetry | None:
    """Consume pending telemetry so it cannot bleed into a later span."""
    telemetry = _pending_telemetry.get()
    _pending_telemetry.set(None)
    return telemetry


def compute_trace_status(spans: list[Span]) -> TraceStatus:
    """Derive trace status from the statuses of its spans (C5).

    Failure dominates degraded: a trace containing a failed step cannot be
    trusted even if the pipeline continued past it.
    """
    statuses = {span.status for span in spans}
    if "failure" in statuses:
        return TraceStatus.FAILURE
    if "degraded" in statuses:
        return TraceStatus.DEGRADED
    return TraceStatus.SUCCESS


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _default_traces_dir() -> Path:
    """Default traces directory, from settings rather than a literal."""
    try:
        from forensics.config import get_settings

        return get_settings().traces_dir
    except Exception:
        return Path("data/traces")


def _default_db_path() -> Path:
    """Default SQLite path, from settings rather than a literal."""
    try:
        from forensics.config import get_settings

        return get_settings().db_path
    except Exception:
        return Path("data/forensics.db")


class TraceContext:
    """Manages the current trace and span context."""

    def __init__(
        self,
        trace_id: str | None = None,
        document_id: str | None = None,
        traces_dir: Path | None = None,
        db_path: Path | None = None,
        tenant_id: str = DEFAULT_TENANT_ID,
    ):
        self.trace_id = trace_id or _new_id()
        self.document_id = document_id or _new_id()
        self.traces_dir = traces_dir or _default_traces_dir()
        self.db_path = db_path or _default_db_path()
        self.tenant_id = tenant_id
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._trace: Trace | None = None
        self._spans: list[Span] = []
        self._start_time = time.perf_counter()

    def __enter__(self) -> "TraceContext":
        self._trace = Trace(
            trace_id=self.trace_id,
            document_id=self.document_id,
            spans=[],
            status=TraceStatus.SUCCESS,
            tenant_id=self.tenant_id,
        )
        _current_trace.set(self._trace)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._trace:
            self._trace.completed_at = _utc_now()
            self._trace.total_latency_ms = int((time.perf_counter() - self._start_time) * 1000)
            if exc_type is not None:
                self._trace.status = TraceStatus.FAILURE
            else:
                # Derive from spans: `trace_context` forwards no exception, so a
                # failure the caller caught would otherwise leave this SUCCESS.
                self._trace.status = compute_trace_status(self._trace.spans)
        _current_trace.set(None)

    @property
    def trace(self) -> Trace | None:
        return self._trace


@contextmanager
def trace_context(
    trace_id: str | None = None,
    document_id: str | None = None,
    traces_dir: Path | None = None,
    db_path: Path | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> Any:
    """Context manager for a trace."""
    ctx = TraceContext(trace_id, document_id, traces_dir, db_path, tenant_id)
    entered = ctx.__enter__()
    try:
        yield entered
    except BaseException:
        # Forward the exception so the trace is marked FAILURE. Swallowing it
        # here would report success for a run that in fact blew up.
        ctx.__exit__(*sys.exc_info())
        raise
    else:
        ctx.__exit__(None, None, None)


def get_current_trace() -> Trace | None:
    """Get the currently active trace."""
    return _current_trace.get()


def get_current_span() -> Span | None:
    """Get the currently active span."""
    return _current_span.get()


def _serialize_payload(value: Any) -> str:
    """Serialize a step result to JSON that the analyzer can re-read.

    `json.dumps(result, default=str)` turns a Pydantic model into a JSON
    *string* holding its repr, so every downstream check that parses
    `output_json` as an object failed with "schema invalid" on real traces.
    Pydantic results are dumped structurally; anything else keeps the
    stringifying fallback.
    """
    if value is None:
        return "{}"
    if isinstance(value, BaseModel):
        return json.dumps(value.model_dump(mode="json"), default=str)
    return json.dumps(value, default=str)


def _safe_repr(value: Any, _depth: int = 0) -> str:
    """Render an object for a span payload without leaking secrets.

    A blind `json.dumps(..., default=str)` wrote `str(obj)` for anything JSON
    could not encode — and `str(Settings)` contains the plaintext API key, so
    a config or client object reaching a traced step leaked the secret into
    every trace file on disk. This renders a type-only placeholder instead.
    """
    if _depth > 2:
        return "<...>"
    # Known-safe scalar-ish types are fine to stringify.
    if isinstance(value, (int, float, bool)):
        return str(value)
    module = type(value).__module__
    qualname = type(value).__qualname__
    if module.startswith(("forensics", "pydantic", "httpx")) or module in (
        "builtins",
        "types",
    ):
        return f"<{qualname}>"
    return f"<{module}.{qualname}>"


def _span_payload(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build the serializable input payload for a span.

    `self` is dropped deliberately. On a method, args[0] is the pipeline object,
    which transitively holds the API key; serializing it would write a secret
    into every trace file on disk. Steps that want their instance state in the
    span must pass it explicitly as an argument.
    """
    positional = list(args[1:]) if args else []
    return {"args": positional, "kwargs": kwargs}


def traced_step(step_name: str) -> Callable[[F], F]:
    """Decorator to trace a pipeline step.

    Captures input, output, latency, and creates a span in the current trace.
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            trace = get_current_trace()
            if trace is None:
                # No active trace, run without tracing
                return func(*args, **kwargs)

            start_time = time.perf_counter()
            started_at = _utc_now()
            input_data = _span_payload(args, kwargs)

            def record(elapsed_ms: int, result: Any, error: str | None) -> None:
                status = "failure" if error else "success"
                output_json = _serialize_payload(result)

                # Consume whatever the step published. A step that raises after
                # a successful LLM call still reports failure, but the model and
                # token counts stay attached so the cost is not lost.
                telemetry = _take_telemetry()
                if telemetry is None:
                    model_id, provider = "n/a", "n/a"
                    prompt, raw_response = f"Step: {step_name}", output_json
                    prompt_tokens = completion_tokens = reasoning_tokens = 0
                    confidence: int | None = None
                else:
                    model_id, provider = telemetry.model_id, telemetry.provider
                    prompt = telemetry.prompt
                    raw_response = telemetry.raw_response or output_json
                    prompt_tokens = telemetry.prompt_tokens
                    completion_tokens = telemetry.completion_tokens
                    reasoning_tokens = telemetry.reasoning_tokens
                    confidence = telemetry.confidence

                span = Span(
                    span_id=_new_id(),
                    trace_id=trace.trace_id,
                    step_name=step_name,
                    input_json=json.dumps(input_data, default=_safe_repr),
                    output_json=output_json,
                    model_id=model_id,
                    provider=provider,
                    prompt=prompt,
                    raw_response=raw_response,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    reasoning_tokens=reasoning_tokens,
                    latency_ms=elapsed_ms,
                    status=status,  # type: ignore[arg-type]
                    error=error,
                    # Bracket the actual work: started_at is captured before the
                    # step runs, not when the span is written. Setting both here
                    # made every span report ~0 duration in its timestamps while
                    # latency_ms held the real value, which invalidated all
                    # time-to-detection / time-to-root-cause metrics and made
                    # `ORDER BY started_at` unreliable on DB round-trip.
                    started_at=started_at,
                    ended_at=_utc_now(),
                )
                if confidence is not None:
                    span.confidence = confidence
                trace.spans.append(span)
                _current_span.set(span)

            # Fast path: an async step is detected BEFORE running it, so the
            # span brackets the awaited work instead of the coroutine creation.
            if inspect.iscoroutinefunction(func):
                return _await_and_record(func(*args, **kwargs), trace, record, started_at)

            try:
                result = func(*args, **kwargs)
            except Exception as e:
                record(int((time.perf_counter() - start_time) * 1000), None, str(e))
                raise
            record(int((time.perf_counter() - start_time) * 1000), result, None)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator


async def _await_and_record(
    awaitable: Any,
    trace: Trace,
    record: Callable[[int, Any, str | None], None],
    started_at: datetime,
) -> Any:
    """Await an async traced step and record exactly one span for it.

    `started_at` is captured by the sync wrapper before the coroutine is created,
    so the recorded span brackets the awaited work.
    """
    start_time = time.perf_counter()
    try:
        result = await awaitable
    except Exception as e:
        record(int((time.perf_counter() - start_time) * 1000), None, str(e))
        raise
    record(int((time.perf_counter() - start_time) * 1000), result, None)
    return result


def _span_cost_usd(span: Span) -> float:
    """Cost of one span from config-driven price assumptions (C12).

    Prices come from config, so they are an ASSUMPTION, not an invoice. An
    unknown model prices at 0.0 rather than guessing upward.
    """
    from forensics.config import get_settings

    try:
        settings = get_settings()
    except Exception as exc:
        # Only a config problem lands here. Swallowing it would silently make
        # every trace report $0.00, which is worse than a loud failure: the
        # caller cannot tell real cost from a broken price table.
        raise RuntimeError(
            f"Cannot price span for model {span.model_id!r}: configuration unavailable"
        ) from exc

    known = [
        settings.pipeline_model,
        settings.pipeline_fallback_model,
        settings.judge_model,
        settings.cheap_model,
    ]
    model = next((m for m in known if m.model_id == span.model_id), None)
    if model is None:
        return 0.0

    prompt_price = model.price_in_per_1m / 1_000_000
    completion_price = model.price_out_per_1m / 1_000_000
    return (
        span.prompt_tokens * prompt_price
        + span.completion_tokens * completion_price
        + span.reasoning_tokens * completion_price
    )


def finalize_trace(
    trace: Trace,
    traces_dir: Path | None = None,
    db_path: Path | None = None,
) -> None:
    """Finish a trace: derive status, aggregate totals, persist to both stores.

    The JSON file is the source of truth for payloads; the SQLite index exists
    so analytics never has to parse 15+ JSON files. Both are written here so
    they cannot disagree about totals.

    Status is derived from the spans but never downgraded: `TraceContext.__exit__`
    already marked the trace FAILURE when an exception escaped, including one
    raised outside any traced step (where no span failed). Recomputing from
    spans alone silently turned those runs back into SUCCESS.

    If OTel is configured (via settings), the trace is also mirrored into
    OpenTelemetry so it can be correlated with an operator's other telemetry.
    OTel is strictly additive: an exporter failure must not lose the forensic
    record.

    If PII redaction is enabled, sensitive data is redacted from span payloads
    before persistence.
    """
    if trace.completed_at is None:
        trace.completed_at = _utc_now()

    derived = compute_trace_status(trace.spans)
    if trace.status is TraceStatus.FAILURE or derived is TraceStatus.FAILURE:
        trace.status = TraceStatus.FAILURE
    else:
        trace.status = derived
    trace.total_tokens = sum(
        span.prompt_tokens + span.completion_tokens + span.reasoning_tokens for span in trace.spans
    )
    trace.total_cost_usd = round(sum(_span_cost_usd(span) for span in trace.spans), 8)
    if not trace.total_latency_ms:
        trace.total_latency_ms = sum(span.latency_ms for span in trace.spans)

    # PII redaction (before persistence)
    redactor = create_redactor_from_settings()
    if redactor.enabled:
        for span in trace.spans:
            span.prompt, _ = redactor.redact(span.prompt)
            span.raw_response, _ = redactor.redact(span.raw_response)
            span.input_json, _ = redactor.redact(span.input_json)
            span.output_json, _ = redactor.redact(span.output_json)

    traces_dir = traces_dir or _default_traces_dir()
    write_trace_json(trace, traces_dir)

    if db_path is not None:
        conn = init_db(db_path)
        try:
            write_trace_to_db(trace, conn)
        finally:
            conn.close()

    # Mirror to OTel if configured
    try:
        _provider, configured, exporter = configure_otel()
        if configured and exporter is not None:
            # Use settings for max_prompt_chars
            settings = get_settings()
            max_prompt_chars = getattr(settings, "otel_max_prompt_chars", DEFAULT_MAX_PROMPT_CHARS)
            otel_processor = ForensicsSpanProcessor(trace, exporter, max_prompt_chars)
            for span in trace.spans:
                otel_processor.on_end(span)
    except Exception:
        # The JSON + SQLite writes above are the durable record. A telemetry
        # backend being unreachable must never fail a pipeline run.
        pass


def write_trace_json(trace: Trace, traces_dir: Path) -> Path:
    """Write trace to JSON file atomically (tmp + rename)."""
    traces_dir.mkdir(parents=True, exist_ok=True)
    file_path = traces_dir / f"{trace.trace_id}.json"

    # Atomic write: write to temp file then rename
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=traces_dir,
        prefix=f".{trace.trace_id}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        json.dump(trace.model_dump(mode="json"), tmp, indent=2, default=str)
        tmp_path = Path(tmp.name)

    # Atomic rename
    os.replace(tmp_path, file_path)
    return file_path


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database with traces and spans tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Traces table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS traces (
            trace_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            total_latency_ms INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            total_cost_usd REAL DEFAULT 0.0,
            pipeline_version TEXT,
            schema_version INTEGER DEFAULT 1,
            tenant_id TEXT NOT NULL DEFAULT 'default'
        )
    """
    )

    # Spans table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS spans (
            span_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            step_name TEXT NOT NULL,
            parent_span_id TEXT,
            model_id TEXT,
            provider TEXT,
            latency_ms INTEGER DEFAULT 0,
            status TEXT,
            error TEXT,
            started_at TEXT,
            ended_at TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
        )
    """
    )

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_document_id ON traces(document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status)")

    conn.commit()

    # Add tenant_id to a database written before multi-tenancy, and create the
    # auth tables. Both callers of `init_db` need this: `Storage()` creates the
    # full schema, but the CLI's `finalize_trace` path only ever comes through
    # here, and a trace written without a tenant would be invisible to its owner.
    migrate(conn)
    return conn


def write_trace_to_db(trace: Trace, conn: sqlite3.Connection) -> None:
    """Write trace and spans to SQLite."""
    conn.execute(
        """
        INSERT OR REPLACE INTO traces
        (trace_id, document_id, status, created_at, completed_at,
         total_latency_ms, total_tokens, total_cost_usd, pipeline_version, schema_version,
         tenant_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            trace.trace_id,
            trace.document_id,
            trace.status.value,
            trace.created_at.isoformat() if trace.created_at else None,
            trace.completed_at.isoformat() if trace.completed_at else None,
            trace.total_latency_ms,
            trace.total_tokens,
            trace.total_cost_usd,
            trace.pipeline_version,
            trace.schema_version,
            trace.tenant_id,
        ),
    )

    for span in trace.spans:
        conn.execute(
            """
            INSERT OR REPLACE INTO spans
            (span_id, trace_id, step_name, parent_span_id, model_id, provider,
             latency_ms, status, error, started_at, ended_at, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                span.span_id,
                span.trace_id,
                span.step_name,
                span.parent_span_id,
                span.model_id,
                span.provider,
                span.latency_ms,
                span.status,
                span.error,
                span.started_at.isoformat() if span.started_at else None,
                span.ended_at.isoformat() if span.ended_at else None,
                span.tenant_id,
            ),
        )

    conn.commit()


def load_trace_from_db(trace_id: str, conn: sqlite3.Connection) -> Trace | None:
    """Load trace from SQLite (index only, full payload in JSON)."""
    cursor = conn.execute("SELECT * FROM traces WHERE trace_id = ?", (trace_id,))
    row = cursor.fetchone()
    if not row:
        return None

    cols = [d[0] for d in cursor.description]
    trace_data = dict(zip(cols, row, strict=False))

    span_cursor = conn.execute(
        "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at", (trace_id,)
    )
    span_rows = span_cursor.fetchall()
    span_cols = [d[0] for d in span_cursor.description] if span_rows else []
    spans = [Span.model_validate(dict(zip(span_cols, r, strict=False))) for r in span_rows]

    trace_data["spans"] = spans
    return Trace.model_validate(trace_data)
