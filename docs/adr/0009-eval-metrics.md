# ADR 0009: Eval Scoring Per Step Type

## Status
Accepted

## Context
"Fixed" must be precisely defined per step type for regression tracking to work.

## Decision

| Step | Metric | "Fixed" Definition |
|---|---|---|
| Intake | N/A (pass-through) | Output equals input |
| Extraction | Entity-set F1 (value + type) | F1 >= 0.95 vs human-corrected entities |
| Classification | Exact label match | Predicted label == human label |
| Summarization | Judge score (1-5) + deterministic checks | Judge >= 4 AND no entity hallucination (grounded check) |

Eval case schema:
```python
class EvalCase(BaseModel):
    case_id: str
    trace_id: str
    failing_step: str
    bad_output: BaseModel
    corrected_output: BaseModel
    category: FailureCategory
    created_at: datetime
```

Eval run records: pipeline_version, per-step prompt_versions (content hash), started_at, completed_at, passed_count, failed_count.

## Rationale
- Extraction: set-based F1 handles missing/extra entities; type matters.
- Classification: exact match — no partial credit for "invoice" vs "receipt".
- Summarization: judge + grounded check catches hallucinated entities that judge might miss.

## Consequences
- Human correction required for every eval case (the feedback loop).
- Prompt version hashes enable "was this specific prompt the problem?" queries.
- Trend line: "known issues resolved over time" = cases that flipped from failed to fixed.

## Verification
- `pytest tests/test_eval.py` — one test per step type asserting metric and "fixed" threshold
- `make eval` produces trend JSON
