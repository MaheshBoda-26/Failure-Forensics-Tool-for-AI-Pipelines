"""OpenTelemetry export for forensic traces (C5).

Forensics spans are the source of truth. This module *mirrors* them into OTel so
they can be correlated with the rest of an operator's telemetry, without making
OTel a dependency of the analysis path.

Two independent paths exist deliberately:

* `export_trace_to_otel` - replay a finished trace (used by the CLI and tests).
* `ForensicsSpanProcessor` - a real `SpanProcessor`, for operators who configure
  the OTel SDK themselves.

Attribute keys follow the OTel GenAI semantic conventions where they exist and
`forensics.*` for our own fields. The keys are pinned by tests because they are
a contract with whatever collector eventually receives them.
"""

from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import SpanKind, Status, StatusCode

from forensics.models import Span, Trace

# GenAI semantic conventions (stable names) plus our own namespace.
GENAI_ATTRS: dict[str, str] = {
    "system": "gen_ai.system",
    "operation": "gen_ai.operation.name",
    "request_model": "gen_ai.request.model",
    "response_model": "gen_ai.response.model",
    "provider": "gen_ai.provider.name",
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "prompt": "gen_ai.prompt",
    "prompt_truncated": "gen_ai.prompt.truncated",
    "step_name": "forensics.step.name",
    "trace_id": "forensics.trace_id",
    "span_id": "forensics.span_id",
    "confidence": "forensics.confidence",
    "document_id": "forensics.document_id",
    "trace_status": "forensics.trace.status",
    "trace_total_tokens": "forensics.trace.total_tokens",
    "trace_total_cost_usd": "forensics.trace.total_cost_usd",
}

DEFAULT_TRACER_NAME = "forensics"
DEFAULT_MAX_PROMPT_CHARS = 4000

# Steps that make no model call. Reporting these as `chat` would be a lie.
_NON_LLM_STEPS = frozenset({"intake"})


def _is_llm_step(span: Span) -> bool:
    return span.step_name not in _NON_LLM_STEPS and span.model_id not in ("", "n/a")


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or len(value) <= limit:
        return value, False
    return value[:limit], True


def _span_attributes(span: Span, trace: Trace, max_prompt_chars: int) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        GENAI_ATTRS["step_name"]: span.step_name,
        GENAI_ATTRS["trace_id"]: trace.trace_id,
        GENAI_ATTRS["span_id"]: span.span_id,
        GENAI_ATTRS["document_id"]: trace.document_id,
        GENAI_ATTRS["trace_status"]: trace.status.value,
        GENAI_ATTRS["trace_total_tokens"]: int(trace.total_tokens),
        GENAI_ATTRS["trace_total_cost_usd"]: float(trace.total_cost_usd),
        GENAI_ATTRS["confidence"]: int(span.confidence),
    }

    prompt, truncated = _truncate(span.prompt, max_prompt_chars)
    attrs[GENAI_ATTRS["prompt"]] = prompt
    if truncated:
        attrs[GENAI_ATTRS["prompt_truncated"]] = True

    if _is_llm_step(span):
        attrs[GENAI_ATTRS["system"]] = "openrouter"
        attrs[GENAI_ATTRS["operation"]] = "chat"
        attrs[GENAI_ATTRS["request_model"]] = span.model_id
        attrs[GENAI_ATTRS["response_model"]] = span.model_id
        attrs[GENAI_ATTRS["provider"]] = span.provider
        attrs[GENAI_ATTRS["input_tokens"]] = int(span.prompt_tokens)
        attrs[GENAI_ATTRS["output_tokens"]] = int(span.completion_tokens)
    else:
        # A deterministic step: still an operation, just not a model call.
        attrs[GENAI_ATTRS["operation"]] = "pipeline.transform"

    return attrs


def _emit_span(
    tracer: otel_trace.Tracer,
    span: Span,
    trace: Trace,
    exporter: SpanExporter,
    max_prompt_chars: int,
) -> None:
    with tracer.start_as_current_span(
        f"forensics.{span.step_name}",
        kind=SpanKind.INTERNAL,
        attributes=_span_attributes(span, trace, max_prompt_chars),
        end_on_exit=False,
    ) as otel_span:
        if span.status == "failure":
            otel_span.set_status(Status(StatusCode.ERROR, span.error or "step failed"))
        elif span.status == "degraded":
            otel_span.set_status(Status(StatusCode.OK, "degraded"))
        otel_span.end()


def export_trace_to_otel(
    trace: Trace,
    exporter: SpanExporter | None = None,
    tracer_provider: TracerProvider | None = None,
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
) -> int:
    """Mirror a finished forensic trace into OTel.

    Returns the number of spans emitted. With no exporter this is a no-op, so
    callers never need to branch on whether OTel is configured.
    """
    if exporter is None:
        return 0

    provider = tracer_provider or _provider_with(exporter)
    tracer = provider.get_tracer(DEFAULT_TRACER_NAME)
    for span in trace.spans:
        _emit_span(tracer, span, trace, exporter, max_prompt_chars)
    return len(trace.spans)


def _provider_with(exporter: SpanExporter) -> TracerProvider:
    """A throwaway provider for one-shot exports (tests, CLI replay).

    Uses the SDK's own SimpleSpanProcessor rather than a hand-rolled one, so the
    export path is the same code path an operator gets in production.
    """
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


class ForensicsSpanProcessor:
    """A `SpanProcessor` that exports completed forensic spans to OTel.

    Construct it with the trace a step belongs to; `on_end` is then called by
    `finalize_trace` for each span so operators can keep using the standard OTel
    SDK wiring instead of calling `export_trace_to_otel` directly.
    """

    def __init__(
        self,
        trace: Trace,
        exporter: SpanExporter,
        max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
    ) -> None:
        self._trace = trace
        self._exporter = exporter
        self._max_prompt_chars = max_prompt_chars
        # One provider for the processor's lifetime: building one per span
        # would spin up a thread pool per span and never shut it down.
        self._tracer = _provider_with(exporter).get_tracer(DEFAULT_TRACER_NAME)

    def on_start(self, span: otel_trace.Span, parent_context: Any = None) -> None:
        return None

    def on_end(self, span: Span) -> None:
        _emit_span(self._tracer, span, self._trace, self._exporter, self._max_prompt_chars)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


_configured: tuple[TracerProvider, bool] | None = None


def configure_otel(
    exporter: SpanExporter | None = None,
    service_name: str = "failure-forensics",
) -> tuple[TracerProvider, bool]:
    """Configure the global OTel provider once.

    Returns `(provider, configured_now)`. Calling it repeatedly is safe and
    returns the same provider; OTel itself forbids replacing a set provider.
    """
    global _configured
    if _configured is not None:
        return _configured

    provider = TracerProvider()
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)
    _configured = (provider, exporter is not None)
    return _configured


__all__ = [
    "GENAI_ATTRS",
    "ForensicsSpanProcessor",
    "configure_otel",
    "export_trace_to_otel",
]
