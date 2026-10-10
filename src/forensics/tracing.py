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
import tempfile
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from forensics.models import Span, Trace, TraceStatus

F = TypeVar("F", bound=Callable[..., Any])

# Context variables for current trace/span
_current_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar(
    "_current_trace", default=None
)
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "_current_span", default=None
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class TraceContext:
    """Manages the current trace and span context."""

    def __init__(
        self,
        trace_id: str | None = None,
        document_id: str | None = None,
        traces_dir: Path | None = None,
        db_path: Path | None = None,
    ):
        self.trace_id = trace_id or _new_id()
        self.document_id = document_id or _new_id()
        self.traces_dir = traces_dir or Path("data/traces")
        self.db_path = db_path or Path("data/forensics.db")
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
        )
        _current_trace.set(self._trace)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._trace:
            self._trace.completed_at = _utc_now()
            self._trace.total_latency_ms = int((time.perf_counter() - self._start_time) * 1000)
            if exc_type is not None:
                self._trace.status = TraceStatus.FAILURE
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
) -> Any:
    """Context manager for a trace."""
    ctx = TraceContext(trace_id, document_id, traces_dir, db_path)
    try:
        yield ctx.__enter__()
    finally:
        ctx.__exit__(None, None, None)


def get_current_trace() -> Trace | None:
    """Get the currently active trace."""
    return _current_trace.get()


def get_current_span() -> Span | None:
    """Get the currently active span."""
    return _current_span.get()


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
            input_data = _span_payload(args, kwargs)

            def record(elapsed_ms: int, result: Any, error: str | None) -> None:
                status = "failure" if error else "success"
                output_json = json.dumps(result, default=str) if result is not None else "{}"
                span = Span(
                    span_id=_new_id(),
                    trace_id=trace.trace_id,
                    step_name=step_name,
                    input_json=json.dumps(input_data, default=str),
                    output_json=output_json,
                    model_id="fake-model",
                    provider="fake",
                    prompt=f"Step: {step_name}",
                    raw_response=output_json,
                    latency_ms=elapsed_ms,
                    status=status,  # type: ignore[arg-type]
                    error=error,
                    started_at=_utc_now(),
                    ended_at=_utc_now(),
                )
                trace.spans.append(span)
                _current_span.set(span)

            # Fast path: an async step is detected BEFORE running it, so the
            # span brackets the awaited work instead of the coroutine creation.
            if inspect.iscoroutinefunction(func):
                return _await_and_record(func(*args, **kwargs), trace, record, input_data)

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
    input_data: dict[str, Any],
) -> Any:
    """Await an async traced step and record exactly one span for it."""
    del input_data
    start_time = time.perf_counter()
    try:
        result = await awaitable
    except Exception as e:
        record(int((time.perf_counter() - start_time) * 1000), None, str(e))
        raise
    record(int((time.perf_counter() - start_time) * 1000), result, None)
    return result


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
            schema_version INTEGER DEFAULT 1
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
            FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
        )
    """
    )

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_document_id ON traces(document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status)")

    conn.commit()
    return conn


def write_trace_to_db(trace: Trace, conn: sqlite3.Connection) -> None:
    """Write trace and spans to SQLite."""
    conn.execute(
        """
        INSERT OR REPLACE INTO traces
        (trace_id, document_id, status, created_at, completed_at,
         total_latency_ms, total_tokens, total_cost_usd, pipeline_version, schema_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            trace.trace_id,
            trace.document_id,
            trace.status.value,
            trace.created_at.isoformat() if trace.created_at else "",
            trace.completed_at.isoformat() if trace.completed_at else "",
            trace.total_latency_ms,
            trace.total_tokens,
            trace.total_cost_usd,
            trace.pipeline_version,
            trace.schema_version,
        ),
    )

    for span in trace.spans:
        conn.execute(
            """
            INSERT OR REPLACE INTO spans
            (span_id, trace_id, step_name, parent_span_id, model_id, provider,
             latency_ms, status, error, started_at, ended_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                span.started_at.isoformat() if span.started_at else "",
                span.ended_at.isoformat() if span.ended_at else "",
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
