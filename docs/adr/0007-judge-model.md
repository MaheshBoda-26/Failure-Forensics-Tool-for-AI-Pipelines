# ADR 0007: Judge Model Strategy — Different Provider Family

## Status
Accepted

## Context
The LLM-as-judge can be biased toward its own family's outputs. OpenRouter makes family separation trivial.

## Decision
- Judge model and pipeline models are configured via **separate environment variables** (`JUDGE_MODEL` vs `PIPELINE_MODEL`).
- At startup, config validates that the judge's provider family differs from the pipeline's provider family. If they match, the app fails fast with a clear error.
- Judge model is pinned in config (exact model id + provider) and recorded on every diagnosis.
- Judge uses rubric-based structured output; deterministic checks run first.
- Judge self-consistency (2-3 samples on borderline scores) is budget-gated — cut first if needed.

## Rationale
- Self-preference bias is a known weakness (PROMPT.md known_weaknesses #2).
- OpenRouter makes this free: just pick a different provider for the judge.
- Making it a config assertion (not convention) prevents accidental misconfiguration.
- Pinning the judge model id on every diagnosis enables regression tracking: if the judge model changes, old diagnoses are incomparable.

## Consequences
- Two model budgets: pipeline (cheap, high volume) + judge (stronger, lower volume).
- Judge model upgrades silently change behavior — the pin in config is the only safeguard.
- If budget forces same family, the config assertion fails and the team must decide.

## Verification
- `pytest tests/test_config.py::test_judge_family_separation` asserts ValidationError on same family
- `pytest tests/test_analyzer.py` records judge_model_id on every diagnosis
