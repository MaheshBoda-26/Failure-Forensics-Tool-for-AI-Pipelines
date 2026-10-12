# SPEC: Phase Execution Plan

## Recommended Order

```
Phase 1 (Week 1-2): Independent, High Impact
├── 01-otel-export.md        → 2h  (scaffold exists)
├── 02-judge-llm.md          → 4h  (new model, prompt eng)
└── 03-pii-redaction.md      → 3h  (pure regex)

Phase 2 (Week 2-3): Foundation
└── 04-auth-multitenant.md   → 6h  (schema migrations)

Phase 3 (Week 3-4): Extensibility
├── 05-check-plugins.md      → 3h  (refactor analyzer)
└── 06-scheduled-eval.md     → 3h  (APScheduler)

Phase 4 (Week 4-5): Scale
└── 07-worker-queue.md       → 5h  (concurrency, worker)

Total: ~26 hours over 4-5 weeks
```

## Dependency Graph

```
                    ┌─────────────────┐
                    │   Phase 1       │
                    │  (parallelizable)│
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │   OTel   │  │  Judge   │  │   PII    │
        └────┬─────┘  └────┬─────┘  └────┬─────┘
             │             │             │
             └─────────────┼─────────────┘
                           ▼
                    ┌─────────────────┐
                    │   Phase 2       │
                    │    Auth         │
                    │ (schema mig)    │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ Plugins  │  │ Scheduler│  │  Queue   │
        └──────────┘  └──────────┘  └──────────┘
```

## Quick Wins (Can Start Immediately)

| Spec | Why First |
|------|-----------|
| **OTel** | Scaffold 90% done; just wiring + optional dep |
| **PII** | Zero deps, pure regex, <1ms, catches real leaks |
| **Check Plugins** | Refactors existing code; enables custom checks without touching core |

## Blockers

| Spec | Blocker | Resolution |
|------|---------|------------|
| Auth | Schema migration | Write migration script first; test on copy |
| Queue | Auth (tenant_id) | Can implement with `tenant_id='default'` first |
| Scheduler | Auth (webhook) | Works without auth; webhook optional |

## Testing Strategy

Each spec includes acceptance criteria. Add integration tests:
```
tests/
├── test_otel_export.py
├── test_judge_llm.py
├── test_pii_redaction.py
├── test_auth.py
├── test_check_plugins.py
├── test_scheduler.py
└── test_queue.py
```

Run with: `pytest -m "not live"` (all fast, no API costs)

## Rollout Checklist

- [ ] Phase 1 specs reviewed and approved
- [ ] Phase 1 implemented + tested
- [ ] Migration script for Auth written + tested on copy
- [ ] Phase 2 (Auth) deployed to staging
- [ ] Phase 3-4 implemented with Auth enabled
- [ ] Load test worker queue with 100 concurrent jobs
- [ ] Documentation updated for each feature
