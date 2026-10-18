# Documentation

How to navigate this directory. Everything here describes the code in `src/` at
the same commit; if a statement and the code disagree, the code is the bug and
the statement is the report. The last full pass over these documents is recorded
in `tasks/todo.md` (Phase 7).

## Where to start

| Question | Read |
|---|---|
| What is this, and how do I run it? | [`../README.md`](../README.md) |
| What does it not do? | [`limitations.md`](limitations.md) |
| How do the pieces fit? | [`architecture.md`](architecture.md) |
| Why is it built this way? | [`adr/`](adr) |
| What was each feature supposed to do? | [`specs/`](specs) |
| What is left to do? | [`../plan/forensics-fix-and-finish.md`](../plan/forensics-fix-and-finish.md), [`../tasks/todo.md`](../tasks/todo.md) |

## Specs

Each spec is a feature contract, and each ships with the test file that proves
it. "Verified" means the named file exists and passes in `make test`.

| Spec | Feature | Test file (repo-relative) |
|---|---|---|
| [00](specs/00-execution-plan.md) | Execution plan (task IDs used throughout) | — |
| [01](specs/01-otel-export.md) | OpenTelemetry export | `tests/test_otel_export.py` |
| [02](specs/02-judge-llm.md) | Judge LLM (cross-family, structured) | `tests/test_judge_llm.py` |
| [03](specs/03-pii-redaction.md) | PII redaction at the persistence boundary | `tests/test_pii_redaction.py` |
| [04](specs/04-auth-multitenant.md) | API keys and tenant isolation | `tests/test_auth.py` |
| [05](specs/05-check-plugins.md) | Custom deterministic check plugins | `tests/test_check_plugins.py` |
| [06](specs/06-scheduled-eval.md) | Scheduled eval and regression alerts | `tests/test_scheduler.py` |
| [07](specs/07-worker-queue.md) | SQLite worker queue | `tests/test_queue.py` |

Specs 01 and 03 keep their original acceptance-criteria wording next to a
corrected description where the shipped behaviour is stricter or the sketch had a
bug; specs 04, 06 and 07 each end with an "Implementation notes" section listing
where the shipped code deliberately differs from the sketch and why.

## ADRs

Architecture decision records, one decision each: context, decision, rationale,
consequences, and the tests that verify it.

| ADR | Decision |
|---|---|
| [0000](adr/0000-assumptions.md) | Project assumptions and constraints (see its Corrections section) |
| [0001](adr/0001-custom-chain.md) | Custom step chain instead of a framework |
| [0002](adr/0002-llm-client.md) | LLM client with three-tier structured output |
| [0003](adr/0003-opentelemetry.md) | OpenTelemetry as a mirror, not a dependency |
| [0004](adr/0004-storage-split.md) | JSON payloads + SQLite index |
| [0005](adr/0005-fastapi.md) | FastAPI for the API surface |
| [0006](adr/0006-frontend.md) | React frontend |
| [0007](adr/0007-judge-model.md) | Judge from a different provider family |
| [0008](adr/0008-analyzer-algorithm.md) | Deterministic-first root cause localization |
| [0009](adr/0009-eval-metrics.md) | Eval metrics and trend tracking |
| [0010](adr/0010-test-strategy.md) | Test strategy: hermetic, no live calls by default |
| [0011](adr/0011-dataset-construction.md) | Dataset construction and seeded failures |
| [0012](adr/0012-cost-control.md) | Spend caps and response caching |
| [0013](adr/0013-auth-multitenant.md) | API keys with tenant-scoped storage |
| [0014](adr/0014-scheduled-eval-alerts.md) | Scheduled eval with webhook regression alerts |
| [0015](adr/0015-worker-queue.md) | SQLite worker queue for pipeline runs |
| [0016](adr/0016-dry-run-fixture-and-analysis-order.md) | Document-keyed DRY_RUN fixture; analyze before redaction |

## Other documents

| Document | Contents |
|---|---|
| [`architecture.md`](architecture.md) | Component map and data flow |
| [`c5-tracing-design.md`](c5-tracing-design.md) | Tracing design (the C5 requirement) |
| [`REVIEW-full-codebase.md`](REVIEW-full-codebase.md) | The full-codebase review that produced the fix-and-finish plan |
| [`../PLAN.md`](../PLAN.md) | The original two-day build plan |
| [`../PROMPT.md`](../PROMPT.md) | The master planning prompt that generated the specs |

## Verifying claims in these docs

```bash
make check                    # ruff + mypy strict + 390 tests, hermetic
scripts/smoke.sh http://127.0.0.1:8000     # the whole loop over HTTP against a running server
pytest -m live                # the paths that need a real model (uses quota)
forensics benchmark-analyzer  # fault-injection scorecard for the analyzer
```

Claims in `limitations.md` name the measurement behind them (a test file, a
command, or a specific behaviour). If a limitation turns out to be fixed, move it
to the "Resolved in this revision" table at the top of that file with the commit
that fixed it, rather than deleting the entry.
