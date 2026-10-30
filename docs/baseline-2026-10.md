# Failure Forensics — Baseline (2026-10)

Captured at the start of the remaining-work implementation so every later phase
can prove it changed nothing. Commands must stay green throughout: `make check`,
`cd frontend && npm test && npm run build`, `make test-postgres` (with a server),
and `DRY_RUN=true python -m forensics.cli demo`.

## Command outputs

### `make check` (lint + typecheck + test)

```
568 passed, 13 skipped, 1 deselected, 4 warnings in 8.28s
```
Ruff lint and mypy strict both pass; all 58 files formatted.

### `pytest --collect-only -q | tail -1`

Collected per-file counts (final file shown): `tests/test_tracing_phase2.py: 24`

### `cd frontend && npm test`

```
 Test Files  1 passed (1)
      Tests  14 passed (14)
```

### `DRY_RUN=true python -m forensics.cli demo` summary

```
SUMMARY: 13/15 classifications correct
Seeded failures in dataset: 5
Traces saved: 15
Traces with a diagnosed root cause: 5
Seeded-failure root-cause step accuracy: 5/5, category accuracy: 5/5
```

### `DRY_RUN=true python -m forensics.cli benchmark-analyzer` scorecard

```
Step-Localization Accuracy: 100.0%
Category Accuracy:           100.0%
Total Faults Tested:         166
Unscoreable (skipped):       23 (baseline lacked the fault's precondition)

hallucinated_entity       extraction         extraction_hallucination  27       100.0%   100.0%
malformed_date            extraction         prompt_failure            27       100.0%   100.0%
invalid_label             classification     prompt_failure            27       100.0%   100.0%
wrong_label               classification     misclassification         27       100.0%   100.0%
schema_violation          classification     prompt_failure            27       100.0%   100.0%
context_loss              summarization      context_loss               4       100.0%   100.0%
propagation_error         summarization      propagation_error         27       100.0%   100.0%
```

## Resolved-limitation assertions (regression fence)

Implemented in `tests/test_limitations_contract.py`. One assertion per resolved
limitation that is cheap to express:

- `GET /runtime` exposes `pii_redaction_enabled`, `rate_limit_enabled`, `queue_enabled`;
- `Queue` clears payload on terminal state (`queue.py` `_CLEARED_PAYLOAD`);
- `finalize_trace` redacts when `PII_REDACTION_ENABLED=true`;
- `check_plugins` loader raises on a bad spec;
- `AUTH_KEY_TTL_DAYS` is read by `validate_api_key`.

This is a regression fence, not new behaviour.
