# ADR 0012: Cost Control — Per-Run + Global Caps + Cache

## Status
Accepted

## Context
$30 budget on OpenRouter with heterogeneous per-model and per-request pricing.

## Decision
Enforce caps in `LLMClient`, not provider-side:
- **Per-run cap**: `SPEND_CAP_PER_RUN_USD=0.50` (default)
- **Global cap**: `SPEND_CAP_GLOBAL_USD=20.00` (default)
- **Response cache**: Keyed by hash(model, provider, prompt_version, input). Hits are free, logged.
- **Dry-run mode**: `DRY_RUN=true` — returns canned responses, zero network, zero cost.
- **Price table**: All prices in `.env` (VERIFY BEFORE USE). Client reads them at startup.

Cost accounting:
- Input tokens × price_in + output tokens × price_out + reasoning tokens × price_out (if model emits reasoning)
- Per-request-priced models: use fixed cost from price table
- Every API response logs: model, provider, tokens (in/out/reasoning), latency, cache_hit, estimated_cost

Audit log: `data/costs.jsonl` — one line per call, queryable for "how much did this eval run cost?"

## Rationale
- Provider-side caps don't exist on OpenRouter; we must enforce our own.
- Cache is what makes replay deterministic AND cheap.
- Dry-run mode enables full pipeline testing without spending.
- Price table in config (not code) adapts to OpenRouter price changes without redeploy.

## Consequences
- If per-run cap hit mid-pipeline, the run aborts with `PipelineError(cost_exceeded)`.
- Global cap blocks new runs until reset (manual or daily window).
- Price assumptions flagged in `.env.example` — must be verified before trusting totals.

## Verification
- `pytest tests/test_llm.py::test_per_run_cap` — mock price, assert abort at threshold
- `pytest tests/test_llm.py::test_global_cap` — sequential calls, assert block
- `pytest tests/test_llm.py::test_cache_hit_zero_cost` — second identical call costs $0
- `make eval` in replay mode costs $0 and completes
