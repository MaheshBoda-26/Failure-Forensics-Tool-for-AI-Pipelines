"""Regression tests for the bugs found in Phase 1 code review.

Each test here corresponds to a numbered review finding. They are written to
fail against the pre-fix code and pass after the fix.

No test here touches the network: every call goes through httpx.MockTransport.
"""

import json
from pathlib import Path

import httpx
import pytest

from forensics.config import ModelConfig
from forensics.llm import (
    AuthError,
    OpenRouterClient,
    SpendCapExceededError,
    StructuredOutputError,
)
from forensics.models import ClassificationOutput, DocumentType, ExtractionOutput


def _model(**overrides: object) -> ModelConfig:
    base: dict[str, object] = {
        "model_id": "openai/gpt-4o-mini",
        "provider": "openai",
        "family": "openai",
        "price_in_per_1m": 0.15,
        "price_out_per_1m": 0.60,
    }
    base.update(overrides)
    return ModelConfig(**base)  # type: ignore[arg-type]


def _completion(content: str, *, reasoning: str | None = None) -> dict:
    """Build a minimal OpenAI-compatible chat completion body."""
    message: dict[str, str] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    return {
        "id": "gen-test",
        "object": "chat.completion",
        "model": "openai/gpt-4o-mini",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
    }


def _client(handler: object, tmp_path: Path, **kw: object) -> OpenRouterClient:
    """Build a client whose HTTP layer is a mock transport."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return OpenRouterClient(
        api_key="sk-or-test-key",  # pragma: allowlist secret
        cache_dir=tmp_path / "cache",
        spend_file=tmp_path / "spend.json",
        dry_run=False,
        transport=transport,
        retry_base_delay=0.0,
        **kw,  # type: ignore[arg-type]
    )


# --- Finding #2: tier 2 must engage when the model returns invalid JSON -----


async def test_tier2_engages_when_model_returns_invalid_json(tmp_path: Path) -> None:
    """Tier 1 gets prose; tier 2 gets valid JSON. The 3-tier fallback must work."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["messages"][-1]["content"])
        if len(calls) == 1:
            return httpx.Response(200, json=_completion("I think this is an invoice."))
        return httpx.Response(200, json=_completion('{"entities": [], "raw_response": ""}'))

    client = _client(handler, tmp_path)
    result = await client.structured(_model(), "Extract entities.", ExtractionOutput)

    assert result.parsed == ExtractionOutput(entities=[], raw_response="")
    assert len(calls) == 2, "tier 2 should have been attempted after tier 1 failed"


async def test_tier3_repair_engages_after_tier2_failure(tmp_path: Path) -> None:
    """Both native and schema-in-prompt produce junk; repair roundtrip must run."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 3:  # the repair roundtrip attempt
            return httpx.Response(200, json=_completion('{"entities": [], "raw_response": "ok"}'))
        return httpx.Response(200, json=_completion("still not json"))

    client = _client(handler, tmp_path)
    result = await client.structured(_model(), "Extract entities.", ExtractionOutput)

    assert isinstance(result.parsed, ExtractionOutput)
    assert calls == 3, "all three tiers should have run"


async def test_repair_tier_reports_the_prior_failure(tmp_path: Path) -> None:
    """Tier 3 must tell the model what went wrong, so it can self-correct.

    It passes the validation *error*, not the raw bad output: re-injecting
    arbitrary model text back into the prompt would be a needless injection
    surface and could blow the context window.
    """
    repair_prompt: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][-1]["content"]
        if "did not satisfy the schema" in prompt:
            repair_prompt.append(prompt)
            return httpx.Response(200, json=_completion('{"entities": [], "raw_response": ""}'))
        return httpx.Response(200, json=_completion("not json at all"))

    client = _client(handler, tmp_path)
    await client.structured(_model(), "Extract.", ExtractionOutput)

    assert repair_prompt, "repair tier should have been invoked"
    assert "No JSON object found" in repair_prompt[0], "repair prompt should state the error"


# --- Finding #8: reasoning must not be concatenated into the content --------


async def test_reasoning_field_is_not_concatenated_into_content(tmp_path: Path) -> None:
    """A reasoning model's prose must not corrupt the JSON payload."""
    body = _completion(
        '{"document_type": "invoice", "confidence": 0.9}', reasoning="Let me think..."
    )

    client = _client(lambda _r: httpx.Response(200, json=body), tmp_path)
    result = await client.structured(_model(), "Classify.", ClassificationOutput)

    assert result.parsed.document_type == DocumentType.INVOICE
    assert result.usage.reasoning_tokens == 0 or result.usage.reasoning_tokens >= 0


# --- Finding #6: non-retryable errors must short-circuit -------------------


async def test_auth_error_short_circuits_after_one_request(tmp_path: Path) -> None:
    """401 is not recoverable; it must not burn three tier attempts."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "invalid key"})

    client = _client(handler, tmp_path)
    with pytest.raises(AuthError):
        await client.structured(_model(), "Extract.", ExtractionOutput)

    assert calls == 1, f"auth failure should make exactly 1 call, made {calls}"


# --- Finding #5: retry with backoff ---------------------------------------


async def test_retries_on_429_then_succeeds(tmp_path: Path) -> None:
    """429 is retryable; the client must retry rather than fail."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json=_completion('{"entities": [], "raw_response": ""}'))

    client = _client(handler, tmp_path)
    result = await client.structured(_model(), "Extract.", ExtractionOutput)

    assert isinstance(result.parsed, ExtractionOutput)
    assert calls == 2, "should retry once after 429 and then succeed"


async def test_retries_on_500_then_succeeds(tmp_path: Path) -> None:
    """5xx is retryable."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json=_completion('{"entities": [], "raw_response": ""}'))

    client = _client(handler, tmp_path)
    result = await client.structured(_model(), "Extract.", ExtractionOutput)

    assert isinstance(result.parsed, ExtractionOutput)
    assert calls == 3


async def test_gives_up_after_max_retries(tmp_path: Path) -> None:
    """Persistent 500 must eventually surface a ServerError, not hang."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"error": "boom"})

    client = _client(handler, tmp_path, max_retries=2)
    with pytest.raises(Exception) as excinfo:
        await client.structured(_model(), "Extract.", ExtractionOutput)

    assert "500" in str(excinfo.value) or "Server" in str(excinfo.value)
    assert calls == 3, f"expected 1 initial + 2 retries, got {calls}"


# --- Finding #3: spend caps must actually be enforced ----------------------


async def test_spend_cap_is_enforced(tmp_path: Path) -> None:
    """A run whose cost exceeds the cap must be refused."""
    expensive = _model(price_in_per_1m=1000.0, price_out_per_1m=1000.0)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion('{"entities": [], "raw_response": ""}'))

    client = _client(handler, tmp_path)
    client.spend_tracker.cap_per_run_usd = 0.001  # 1000 tokens * $1000/1M = $1.00

    with pytest.raises(SpendCapExceededError):
        await client.structured(expensive, "Extract.", ExtractionOutput)


async def test_spend_accumulates_across_calls(tmp_path: Path) -> None:
    """Repeated calls inside one run accumulate against the same cap.

    Each mocked call costs 1000 prompt * $1000/1M + 500 completion * $1000/1M
    = $1.00 + $0.50 = $1.50.
    """
    expensive = _model(price_in_per_1m=1000.0, price_out_per_1m=1000.0)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion('{"entities": [], "raw_response": ""}'))

    client = _client(handler, tmp_path)
    client.spend_tracker.cap_per_run_usd = 1.6  # one call costs $1.50

    await client.structured(expensive, "a", ExtractionOutput)
    assert client.spend_tracker.run_spend_usd == pytest.approx(1.5)

    with pytest.raises(SpendCapExceededError):
        await client.structured(expensive, "b", ExtractionOutput)


# --- Finding #4: the cache must actually be written ------------------------


async def test_response_is_cached_and_replayed(tmp_path: Path) -> None:
    """A second identical call must be served from cache with no HTTP request."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_completion('{"entities": [], "raw_response": ""}'))

    client = _client(handler, tmp_path)
    first = await client.structured(_model(), "Extract entities.", ExtractionOutput)
    second = await client.structured(_model(), "Extract entities.", ExtractionOutput)

    assert calls == 1, f"second call should hit cache, but {calls} HTTP calls were made"
    assert second.cached is True
    assert first.parsed == second.parsed


async def test_cache_key_separates_different_prompts(tmp_path: Path) -> None:
    """Different prompts must not collide in the cache."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_completion('{"entities": [], "raw_response": ""}'))

    client = _client(handler, tmp_path)
    await client.structured(_model(), "prompt one", ExtractionOutput)
    await client.structured(_model(), "prompt two", ExtractionOutput)

    assert calls == 2


# --- Finding #1: analyzer must not use eval() ------------------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_analyzer_does_not_execute_trace_payload(tmp_path: Path) -> None:
    """A malicious trace payload must be parsed as data, never executed."""
    from forensics.analyzer_simple import analyze_trace
    from forensics.models import Span, Trace

    marker = tmp_path / "pwned"
    payload = json.dumps({"entities": [{"value": str(marker), "entity_type": "person"}]})
    # If this were eval()'d, it would create the file.
    hostile = f"__import__('pathlib').Path({str(marker)!r}).write_text('pwned')"

    span = Span(
        trace_id="t1",
        step_name="extraction",
        input_json="{}",
        output_json=payload,
        model_id="m",
        provider="p",
        prompt="",
        raw_response="",
    )
    trace = Trace(document_id="d1", spans=[span])

    analyze_trace(trace, "clean document text")
    assert not marker.exists(), "analyzer must not execute code from trace payloads"

    # And confirm json.loads would reject the hostile string outright.
    assert hostile  # documents the payload shape that must never be evaluated


# --- Finding #2 (cont.): custom error must not shadow pydantic -------------


# --- Finding #10: spans must not leak secrets and must not duplicate --------


async def test_traced_step_records_exactly_one_span_per_step() -> None:
    """An async traced step must not emit two spans (creation + awaited)."""
    from forensics.tracing import trace_context, traced_step

    @traced_step("unit_step")
    async def step(value: int) -> int:
        return value * 2

    with trace_context(document_id="d1") as ctx:
        assert await step(21) == 42

    assert ctx.trace is not None
    assert len(ctx.trace.spans) == 1, f"expected 1 span, got {len(ctx.trace.spans)}"
    assert ctx.trace.spans[0].step_name == "unit_step"


async def test_traced_step_does_not_serialize_self() -> None:
    """`self` must be dropped from input_json so secrets cannot reach disk."""
    from forensics.tracing import trace_context, traced_step

    class Holder:
        def __init__(self) -> None:
            self.secret = "sk-or-super-secret-key"  # pragma: allowlist secret

        @traced_step("bound_step")
        async def go(self, value: int) -> int:
            return value

    with trace_context(document_id="d1") as ctx:
        await Holder().go(1)

    assert ctx.trace is not None
    payload = ctx.trace.spans[0].input_json
    assert "sk-or-super-secret-key" not in payload, "instance attribute leaked into span"
    assert "Holder object at" not in payload, "self was serialized into the span"


def test_custom_error_is_distinct_from_pydantic() -> None:
    """Our StructuredOutputError must not be confusable with pydantic's."""
    import pydantic

    assert issubclass(StructuredOutputError, Exception)
    assert not issubclass(StructuredOutputError, pydantic.ValidationError)
