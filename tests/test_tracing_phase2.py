"""Phase 2 regression tests: spans must carry real telemetry (C5, C12).

Every test here failed before Phase 2. They pin behaviour a forensics tool
depends on: a trace recording `model_id="fake-model"` with zero tokens and zero
cost cannot support post-mortem cost or accuracy analysis.
"""

import contextlib
import json
import sqlite3
import types
from pathlib import Path

import httpx
import pytest

from forensics.models import Span, Trace, TraceStatus
from forensics.tracing import (
    StepTelemetry,
    compute_trace_status,
    finalize_trace,
    record_telemetry,
    trace_context,
    traced_step,
)


def _span(name: str, status: str = "success") -> Span:
    return Span(
        trace_id="t",
        step_name=name,
        input_json="{}",
        output_json="{}",
        model_id="m",
        provider="p",
        prompt="",
        raw_response="",
        status=status,  # type: ignore[arg-type]
    )


# --- Trace status derivation (C5) ----------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["success"] * 4, TraceStatus.SUCCESS),
        (["success", "failure", "success", "success"], TraceStatus.FAILURE),
        (["success", "degraded", "success", "success"], TraceStatus.DEGRADED),
        (["success", "failure", "degraded", "success"], TraceStatus.FAILURE),
        ([], TraceStatus.SUCCESS),
    ],
)
def test_trace_status_derived_from_span_statuses(
    statuses: list[str], expected: TraceStatus
) -> None:
    spans = [_span(f"s{i}", s) for i, s in enumerate(statuses)]
    assert compute_trace_status(spans) is expected


async def test_failed_span_marks_trace_failure() -> None:
    with trace_context(document_id="d1") as ctx:

        @traced_step("boom")
        async def boom() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError):
            await boom()

    assert ctx.trace is not None
    assert ctx.trace.status is TraceStatus.FAILURE
    assert ctx.trace.spans[0].status == "failure"


async def test_caught_failure_still_degrades_trace() -> None:
    """`trace_context` forwards no exception, so status cannot rely on exc_type."""
    with trace_context(document_id="d1") as ctx:

        @traced_step("boom")
        async def boom() -> None:
            raise ValueError("kaboom")

        with contextlib.suppress(ValueError):
            await boom()

    assert ctx.trace is not None
    assert ctx.trace.status is TraceStatus.FAILURE, "caught failure left trace SUCCESS"


async def test_all_successful_spans_leave_trace_success() -> None:
    with trace_context(document_id="d1") as ctx:

        @traced_step("fine")
        async def fine() -> str:
            return "ok"

        await fine()

    assert ctx.trace is not None
    assert ctx.trace.status is TraceStatus.SUCCESS


async def test_spans_inherit_the_trace_tenant(tmp_path: Path) -> None:
    """A span belongs to its trace's tenant, not the `default` fallback.

    Spans were built without `tenant_id`, so a tenant-A trace persisted spans
    stamped `default` — in the JSON payload and in the `spans` table — which
    contradicts the trace that owns them.
    """
    with trace_context(document_id="d1", tenant_id="tenant_a") as ctx:

        @traced_step("step")
        async def step() -> str:
            return "ok"

        await step()

    assert ctx.trace is not None
    trace = ctx.trace
    assert trace.tenant_id == "tenant_a"
    assert [span.tenant_id for span in trace.spans] == ["tenant_a"]

    db_path = tmp_path / "index.sqlite"
    finalize_trace(trace, traces_dir=tmp_path / "traces", db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        persisted = conn.execute("SELECT tenant_id FROM spans").fetchall()
    assert persisted == [("tenant_a",)], "the spans table disagrees with its trace"


async def test_step_output_is_serialized_as_json_object() -> None:
    """A Pydantic step result must land in `output_json` as a JSON object.

    `json.dumps(result, default=str)` stringified the model into a JSON
    *string*, so every analyzer check that re-read `output_json` as an object
    raised and reported "schema invalid". On real traces that made every LLM
    step look broken and the analyzer always blamed the first one.
    """
    from forensics.models import ExtractionOutput

    output = ExtractionOutput(entities=[])
    with trace_context(document_id="d1") as ctx:

        @traced_step("extraction")
        async def extraction() -> ExtractionOutput:
            return output

        await extraction()

    assert ctx.trace is not None
    span = ctx.trace.spans[0]
    assert json.loads(span.output_json)["entities"] == []


# --- Real telemetry reaches the span (C5 / C12) --------------------------


async def test_step_telemetry_lands_on_the_span() -> None:
    @traced_step("llm_step")
    async def llm_step() -> str:
        record_telemetry(
            StepTelemetry(
                model_id="openai/gpt-4o-mini",
                provider="openai",
                prompt="the real prompt text",
                raw_response='{"ok": true}',
                prompt_tokens=1000,
                completion_tokens=500,
                confidence=4,
                cached=False,
            )
        )
        return "done"

    with trace_context(document_id="d1") as ctx:
        await llm_step()

    assert ctx.trace is not None
    span = ctx.trace.spans[0]
    assert span.model_id == "openai/gpt-4o-mini"
    assert span.provider == "openai"
    assert span.prompt == "the real prompt text"
    assert span.raw_response == '{"ok": true}'
    assert span.prompt_tokens == 1000
    assert span.completion_tokens == 500
    assert span.confidence == 4


async def test_telemetry_is_scoped_to_its_own_step() -> None:
    """A step's telemetry must not bleed into the next span."""

    @traced_step("llm_step")
    async def llm_step() -> str:
        record_telemetry(StepTelemetry(model_id="m1", provider="p", prompt="p1", prompt_tokens=999))
        return "a"

    @traced_step("plain_step")
    async def plain_step() -> str:
        return "b"

    with trace_context(document_id="d1") as ctx:
        await llm_step()
        await plain_step()

    assert ctx.trace is not None
    assert ctx.trace.spans[0].prompt_tokens == 999
    assert ctx.trace.spans[1].prompt_tokens == 0, "telemetry leaked into the next span"


async def test_failed_step_does_not_record_telemetry_as_success() -> None:
    """Telemetry present but the step raised: the span must still read failure."""

    @traced_step("llm_step")
    async def llm_step() -> None:
        record_telemetry(StepTelemetry(model_id="m1", provider="p", prompt="p1"))
        raise RuntimeError("post-parse failure")

    with trace_context(document_id="d1") as ctx, pytest.raises(RuntimeError):
        await llm_step()

    assert ctx.trace is not None
    span = ctx.trace.spans[0]
    assert span.status == "failure"
    assert span.error == "post-parse failure"


# --- Totals aggregation + persistence (C12 / C6) -------------------------


def test_unknown_model_prices_at_zero_rather_than_guessing() -> None:
    """A model absent from config must not produce an invented cost."""
    trace = Trace(document_id="d1")
    span = _span("a")
    span.trace_id = trace.trace_id
    span.model_id = "some/unknown-model"
    span.prompt_tokens = 1_000_000
    trace.spans.append(span)

    finalize_trace(trace, traces_dir=Path("/tmp/forensics-test-traces"))

    assert trace.total_cost_usd == 0.0


def test_config_failure_is_loud_not_a_silent_zero_cost() -> None:
    """A broken price table must not look like a free run."""
    import forensics.tracing as tracing_mod
    from forensics import config as config_mod

    original = config_mod.get_settings

    def broken() -> object:
        raise ValueError("no NVIDIA_API_KEY")

    config_mod.get_settings = broken  # type: ignore[assignment]
    try:
        trace = Trace(document_id="d1")
        span = _span("a")
        span.trace_id = trace.trace_id
        trace.spans.append(span)

        with pytest.raises(RuntimeError, match="Cannot price span"):
            finalize_trace(trace, traces_dir=Path("/tmp/forensics-test-traces"))
    finally:
        config_mod.get_settings = original  # type: ignore[assignment]
    assert tracing_mod is not None


def test_trace_totals_aggregate_span_tokens() -> None:
    trace = Trace(
        document_id="d1",
        spans=[
            _span("a"),
            _span("b"),
        ],
    )
    trace.spans[0].prompt_tokens = 1000
    trace.spans[0].completion_tokens = 100
    trace.spans[1].prompt_tokens = 500
    trace.spans[1].completion_tokens = 50

    finalize_trace(trace)

    assert trace.total_tokens == 1650


def test_finalize_trace_writes_json_and_sqlite(tmp_path: Path) -> None:
    """One call must write both stores consistently (C6)."""
    trace = Trace(document_id="d1")
    span = _span("a")
    span.trace_id = trace.trace_id
    trace.spans.append(span)

    trace_dir = tmp_path / "traces"
    db_path = tmp_path / "db.sqlite"

    finalize_trace(trace, traces_dir=trace_dir, db_path=db_path)

    written = json.loads((trace_dir / f"{trace.trace_id}.json").read_text())
    assert written["trace_id"] == trace.trace_id
    assert len(written["spans"]) == 1

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT trace_id, total_tokens FROM traces").fetchall()
    assert rows == [(trace.trace_id, 0)]
    assert conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0] == 1
    conn.close()


def test_span_trace_id_must_match_its_trace(tmp_path: Path) -> None:
    """The SQLite FK is enforced: a mislabelled span is rejected, not stored."""
    trace = Trace(document_id="d1", spans=[_span("a")])  # span.trace_id == "t"
    with pytest.raises(sqlite3.IntegrityError):
        finalize_trace(trace, traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")


def test_a_failed_json_write_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temp file is cleaned up when the rename fails.

    `write_trace_json` writes `.<trace_id>.tmp` then renames it. If the write
    fails halfway, that partial file used to stay in `traces_dir`, which is read
    by humans and scanned for `*.json`.
    """
    import forensics.tracing as tracing_mod

    def refuses(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    # `tracing.os` is used only for `replace`, so swapping the reference cannot
    # disturb the real `os` module (or pytest's own file handling).
    monkeypatch.setattr(tracing_mod, "os", types.SimpleNamespace(replace=refuses))

    traces_dir = tmp_path / "traces"
    with pytest.raises(OSError):
        tracing_mod.write_trace_json(Trace(document_id="d1"), traces_dir)

    assert list(traces_dir.iterdir()) == [], "a partial .tmp file was left behind"


def test_finalize_trace_sets_completion_fields() -> None:
    trace = Trace(document_id="d1", spans=[_span("a")])
    assert trace.completed_at is None

    finalize_trace(trace)

    assert trace.completed_at is not None
    assert trace.total_latency_ms >= 0


# --- Confidence is a stored prior, not ground truth (weakness #1) ---------


def test_confidence_defaults_to_neutral_and_is_bounded() -> None:
    assert _span("a").confidence == 3


def test_confidence_rejects_out_of_range() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Span(
            trace_id="t",
            step_name="a",
            input_json="{}",
            output_json="{}",
            model_id="m",
            provider="p",
            prompt="",
            raw_response="",
            confidence=9,
        )


# --- End-to-end: real LLM telemetry reaches the trace --------------------


def _mock_handler(step: str, content: str) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        messages = body.get("messages", [])
        system = messages[0]["content"] if messages else ""
        # Route on the step named in the system prompt, not on loose keywords:
        # the classification prompt legitimately mentions "entities".
        if step in system:
            return httpx.Response(
                200,
                json={
                    "id": "gen-1",
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
                },
            )
        raise AssertionError(f"unexpected step reached the transport: {system[:80]}")

    return handler


async def test_pipeline_trace_records_real_llm_telemetry(tmp_path: Path) -> None:
    """The whole point of Phase 2: a finished run must be auditable."""
    import httpx

    from forensics.llm import LLMClient, NIMClient
    from forensics.models import Document
    from forensics.pipeline import Pipeline

    router = NIMClient(
        api_key="nvapi-test",  # pragma: allowlist secret - fake key for MockTransport
        transport=httpx.MockTransport(
            _mock_handler(
                "extraction",
                '{"entities":[{"value":"500.00","entity_type":"MONEY",'
                '"start_offset":18,"end_offset":24}]}',
            )
        ),
        cache_dir=tmp_path / "cache",
        spend_file=tmp_path / "spend.json",
    )
    client = LLMClient.__new__(LLMClient)
    client.nim = router
    pipeline = Pipeline(client=client)
    doc = Document(content="Invoice 001\nTotal: 500.00 USD\nDue: 2024-03-01")

    with trace_context(document_id="inv001") as ctx:
        intake = await pipeline.intake(doc)
        await pipeline.extraction(intake)

    assert ctx.trace is not None
    extraction = next(s for s in ctx.trace.spans if s.step_name == "extraction")
    # Read the expectation from settings rather than hardcoding a slug: this
    # assertion broke on every .env model change without testing anything.
    from forensics.config import get_settings

    _settings = get_settings()
    assert extraction.model_id == _settings.pipeline_model.model_id
    assert extraction.provider == _settings.pipeline_model.provider
    assert extraction.prompt_tokens == 1000
    assert extraction.completion_tokens == 100
    assert len(extraction.prompt) > 100, "span recorded a placeholder prompt, not the real one"

    # Intake is deterministic: it must NOT claim an LLM was involved.
    intake_span = next(s for s in ctx.trace.spans if s.step_name == "intake")
    assert intake_span.model_id == "n/a"
    assert intake_span.prompt_tokens == 0

    await router.close()


async def test_trace_status_becomes_failure_when_a_step_raises(tmp_path: Path) -> None:
    import httpx

    from forensics.llm import LLMClient, NIMClient
    from forensics.models import Document
    from forensics.pipeline import Pipeline

    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "upstream down"})

    router = NIMClient(
        api_key="nvapi-test",  # pragma: allowlist secret - fake key for MockTransport
        transport=httpx.MockTransport(boom),
        cache_dir=tmp_path / "cache",
        spend_file=tmp_path / "spend.json",
        max_retries=1,
        retry_base_delay=0.0,
    )
    client = LLMClient.__new__(LLMClient)
    client.nim = router
    pipeline = Pipeline(client=client)
    doc = Document(content="Invoice 001\nTotal: 500.00 USD")

    with trace_context(document_id="inv002") as ctx:
        intake = await pipeline.intake(doc)
        with pytest.raises(Exception):  # noqa: B017 - upstream failure class varies
            await pipeline.extraction(intake)

    assert ctx.trace is not None
    assert ctx.trace.status is TraceStatus.FAILURE
    extraction = next(s for s in ctx.trace.spans if s.step_name == "extraction")
    assert extraction.status == "failure"
    assert extraction.error

    await router.close()
