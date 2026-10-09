# ADR 0010: Test Strategy — Record/Replay + Golden Traces

## Status
Accepted

## Context
LLM outputs vary. Tests must be deterministic, fast, and free.

## Decision
Three-tier test pyramid:

1. **Unit tests** (fast, no network): Pure functions — config, models, taxonomy, deterministic checks, fault injection. `pytest -m "not live"`
2. **Contract tests** (fast, no network): Pydantic schema round-trips, API request/response shapes, JSON trace format. Golden fixtures in `tests/fixtures/`.
3. **Golden-trace tests** (fast, replayed): Full pipeline runs against recorded LLM responses. Cache key = hash(model, provider, prompt_version, input). `pytest tests/test_golden_traces.py`
4. **Live smoke tests** (slow, costs money): One real OpenRouter call per model per CI run. Gated behind `@pytest.mark.live`. `pytest -m "live"`

Record/replay:
- `LLMClient` has `replay_mode: bool`. When True, it reads from the cache (JSON files in `data/cache/`) instead of calling OpenRouter.
- Cache populated by running with `replay_mode=False` once (or via `make seed`).
- Cache key includes model, provider routing, prompt content hash, and input hash.

Coverage target: 80% on non-LLM code; 100% on config, models, taxonomy, deterministic checks.

## Rationale
- No flaky tests. Replay is byte-identical.
- Live tests are opt-in and budget-aware.
- Golden traces double as documentation of expected behavior.

## Consequences
- Cache directory must be gitignored but committed for CI (or regenerated).
- `replay_mode` is set via env var `FORENSICS_REPLAY=1`.
- Any change to prompts or models invalidates relevant cache entries.

## Verification
- `make test` (no live) completes in <30s, all pass
- `make test-live` runs 4 live calls (one per pipeline model), costs <$0.10
- `pytest --cov=forensics --cov-fail-under=80` on non-LLM modules
