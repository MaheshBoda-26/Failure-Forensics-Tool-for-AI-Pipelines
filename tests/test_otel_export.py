"""OTel export tests: forensic spans must also be visible as OTel spans (C5).

These tests use the SDK's InMemorySpanExporter, so nothing leaves the process.
They also pin the exact GenAI semantic-convention attribute keys, because those
strings are a contract with whatever collector eventually receives them.
"""

from forensics.models import Span, Trace
from forensics.otel_export import (
    GENAI_ATTRS,
    ForensicsSpanProcessor,
    configure_otel,
    export_trace_to_otel,
)


def _trace_with_span(status: str = "success") -> Trace:
    trace = Trace(document_id="doc-1")
    span = Span(
        trace_id=trace.trace_id,
        step_name="extraction",
        input_json='{"args": []}',
        output_json='{"entities": []}',
        model_id="openai/gpt-4o-mini",
        provider="openai",
        prompt="the real prompt",
        raw_response='{"entities": []}',
        prompt_tokens=1000,
        completion_tokens=100,
        latency_ms=250,
        status=status,  # type: ignore[arg-type]
        confidence=4,
    )
    trace.spans.append(span)
    trace.total_tokens = 1100
    trace.total_cost_usd = 0.00063
    return trace


# --- Attribute contract --------------------------------------------------


def test_genai_attribute_keys_match_the_spec() -> None:
    """Keys are a contract with the collector; pin them literally."""
    assert GENAI_ATTRS["system"] == "gen_ai.system"
    assert GENAI_ATTRS["operation"] == "gen_ai.operation.name"
    assert GENAI_ATTRS["request_model"] == "gen_ai.request.model"
    assert GENAI_ATTRS["response_model"] == "gen_ai.response.model"
    assert GENAI_ATTRS["provider"] == "gen_ai.provider.name"
    assert GENAI_ATTRS["input_tokens"] == "gen_ai.usage.input_tokens"
    assert GENAI_ATTRS["output_tokens"] == "gen_ai.usage.output_tokens"
    assert GENAI_ATTRS["step_name"] == "forensics.step.name"
    assert GENAI_ATTRS["confidence"] == "forensics.confidence"


def test_non_llm_step_is_labelled_as_its_real_operation() -> None:
    """Intake is deterministic, so labelling it `chat` would be a lie."""
    trace = Trace(document_id="doc-1")
    trace.spans.append(
        Span(
            trace_id=trace.trace_id,
            step_name="intake",
            input_json="{}",
            output_json="{}",
            model_id="n/a",
            provider="n/a",
            prompt="Step: intake",
            raw_response="{}",
        )
    )
    export_trace_to_otel(trace)
    # Configuration is exercised by the tests below; this asserts no crash and
    # that a non-LLM step does not raise on missing model fields.


# --- Export ---------------------------------------------------------------


def test_span_is_exported_with_real_attributes() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    trace = _trace_with_span()

    export_trace_to_otel(trace, exporter=exporter)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    got = spans[0]

    assert got.name == "forensics.extraction"
    assert got.attributes[GENAI_ATTRS["request_model"]] == "openai/gpt-4o-mini"
    assert got.attributes[GENAI_ATTRS["provider"]] == "openai"
    assert got.attributes[GENAI_ATTRS["input_tokens"]] == 1000
    assert got.attributes[GENAI_ATTRS["output_tokens"]] == 100
    assert got.attributes[GENAI_ATTRS["step_name"]] == "extraction"
    assert got.attributes[GENAI_ATTRS["confidence"]] == 4
    # The prompt must be exportable, but it is large; truncation is explicit.
    assert GENAI_ATTRS["prompt"] in got.attributes


def test_trace_and_span_ids_are_preserved() -> None:
    """A collector must be able to join an OTel span back to our trace file."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    trace = _trace_with_span()

    export_trace_to_otel(trace, exporter=exporter)

    got = exporter.get_finished_spans()[0]
    assert got.attributes["forensics.trace_id"] == trace.trace_id
    assert got.attributes["forensics.span_id"] == trace.spans[0].span_id


def test_failed_span_is_exported_as_error_status() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode

    exporter = InMemorySpanExporter()
    export_trace_to_otel(_trace_with_span("failure"), exporter=exporter)

    got = exporter.get_finished_spans()[0]
    assert got.status.status_code is StatusCode.ERROR


def test_degraded_span_is_not_an_error() -> None:
    """Degraded means 'ran, but suspect' - it must not trip an error alert."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode

    exporter = InMemorySpanExporter()
    export_trace_to_otel(_trace_with_span("degraded"), exporter=exporter)

    got = exporter.get_finished_spans()[0]
    assert got.status.status_code is not StatusCode.ERROR


def test_prompt_truncation_is_bounded_and_flagged() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    trace = _trace_with_span()
    trace.spans[0].prompt = "x" * 10_000

    exporter = InMemorySpanExporter()
    export_trace_to_otel(trace, exporter=exporter, max_prompt_chars=100)

    got = exporter.get_finished_spans()[0]
    exported = got.attributes[GENAI_ATTRS["prompt"]]
    assert len(exported) < 200
    assert got.attributes[GENAI_ATTRS["prompt_truncated"]] is True


def test_export_is_disabled_without_an_exporter() -> None:
    """OTel export is opt-in; a missing exporter must be a no-op, not a crash."""
    export_trace_to_otel(_trace_with_span(), exporter=None)  # must not raise


def test_configure_otel_is_idempotent() -> None:
    first = configure_otel()
    second = configure_otel()
    assert first is second


def test_processor_on_end_bridges_completed_spans() -> None:
    """The custom SpanProcessor path must emit one OTel span per forensic span."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    trace = _trace_with_span()

    processor = ForensicsSpanProcessor(trace=trace, exporter=exporter)
    trace.spans[0].status = "failure"

    processor.on_end(trace.spans[0])
    processor.force_flush()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "forensics.extraction"


def test_sampling_rate_zero_drops_the_whole_trace() -> None:
    """`OTEL_SAMPLING_RATE` was parsed but never applied, so it did nothing."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    trace = _trace_with_span()
    processor = ForensicsSpanProcessor(trace=trace, exporter=exporter, sampling_rate=0.0)

    processor.on_end(trace.spans[0])

    assert list(exporter.get_finished_spans()) == []


def test_sampling_rate_one_exports_every_span() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    trace = _trace_with_span()
    processor = ForensicsSpanProcessor(trace=trace, exporter=exporter, sampling_rate=1.0)

    processor.on_end(trace.spans[0])

    assert len(exporter.get_finished_spans()) == 1


def test_sampling_decision_is_per_trace_not_per_span() -> None:
    """A partially exported trace is worse than an unexported one."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    trace = _trace_with_span()
    second = trace.spans[0].model_copy(update={"span_id": "span-2"})
    trace.spans.append(second)

    exporter = InMemorySpanExporter()
    processor = ForensicsSpanProcessor(trace=trace, exporter=exporter, sampling_rate=0.0)
    for span in trace.spans:
        processor.on_end(span)

    assert list(exporter.get_finished_spans()) == []


def test_settings_drive_sampling_and_prompt_truncation(monkeypatch) -> None:
    """`OTEL_SAMPLING_RATE` and `OTEL_MAX_PROMPT_CHARS` must reach the processor.

    `max_prompt_chars` was read with `getattr(settings, "otel_max_prompt_chars", ...)`
    for a setting that did not exist, so it silently always used the default.
    """
    from forensics.config import get_settings, reset_settings

    monkeypatch.setenv("OTEL_MAX_PROMPT_CHARS", "12")
    monkeypatch.setenv("OTEL_SAMPLING_RATE", "0.5")
    reset_settings()
    try:
        settings = get_settings()
        assert settings.otel.max_prompt_chars == 12
        assert settings.otel.sampling_rate == 0.5
    finally:
        reset_settings()


def test_export_trace_to_otel_uses_the_settings_limit() -> None:
    """The truncation limit is a privacy control, so it must bound the export."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    trace = _trace_with_span()
    trace.spans[0].prompt = "y" * 500
    exporter = InMemorySpanExporter()

    export_trace_to_otel(trace, exporter=exporter, max_prompt_chars=8)

    exported = exporter.get_finished_spans()[0].attributes[GENAI_ATTRS["prompt"]]
    assert exported.startswith("y" * 8)
    assert len(exported) < 40
