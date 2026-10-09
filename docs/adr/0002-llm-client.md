# ADR 0002: LLM Access Layer

## Status
Accepted

## Context
OpenRouter has uneven per-model feature support (structured output, reasoning fields, per-request pricing). A single wrapper normalizes this in one place.

## Decision
Implement one `LLMClient` class over the OpenRouter `/chat/completions` endpoint (OpenAI-compatible). It handles:
- Provider routing pinning (`provider` parameter)
- Three-tier structured output: native → prompt + parse → repair round-trip
- Both plain and reasoning-model response shapes
- Token accounting (including reasoning tokens)
- Retries/backoff with clear error classification (retriable vs fatal)
- Response cache keyed by hash(model, provider, prompt_version, input)
- Spend caps (per-run and global)

## Rationale
- Every pipeline step, the analyzer, and the eval engine go through this single entry point.
- OpenRouter's per-model quirks are normalized once, not leaked into every caller.
- The cache makes record/replay deterministic and cost-free for tests.

## Consequences
- No direct `httpx` or OpenAI SDK calls outside this module.
- Model capabilities (structured output support) are discovered at startup and cached.
- Models that cannot conform are rejected at config time, not mid-run.

## Verification
- `src/forensics/llm.py` exports exactly one public class: `LLMClient`
- `pytest tests/test_llm.py -v` passes: structured(), cache hit/miss, retriable vs fatal errors
