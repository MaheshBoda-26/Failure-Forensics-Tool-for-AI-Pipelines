# Failure Forensics Tool — Known Limitations (v0)

This document honestly enumerates the current limitations of the Failure Forensics Tool as of Phase 6 (v0). These are not bugs; they are deliberate scope cuts or known gaps with documented mitigations.

---

## 1. No Judge LLM (Semantic Evaluation)

**Limitation**: The analyzer uses only deterministic checks (entity grounding, label enum, date regex, context preservation, propagation, prompt schema). It cannot detect semantic failures like "the summary captures the wrong sentiment" or "the extraction misses a nuanced entity."

**Impact**: Purely structural/syntactic failures are caught; semantic quality is not assessed.

**Mitigation**:
- Deterministic checks cover the most common failure modes in extraction/classification pipelines
- Evidence chains enable human review to catch semantic issues
- Judge LLM integration is the #1 priority for v1 (see `PROMPT.md` Phase 3)

---

## 2. No Propagation Analysis (Global Fidelity = Local Fidelity)

**Limitation**: The analyzer treats each step independently. It does not model how an error in step N propagates to and compounds in step N+1. The `propagation` check only detects terminology mismatches between classification and summarization.

**Impact**: Cascading failures may be under-reported. A hallucinated entity in extraction that causes misclassification AND bad summarization is counted as three separate root causes rather than one cascading failure.

**Mitigation**:
- The `propagation` check catches the most common case: classification/summarization terminology mismatch
- Evidence chains explicitly cite the upstream span, enabling manual tracing
- Full propagation graph analysis is planned for v1

---

## 3. Dry-Run Only in CI (No Live LLM Validation)

**Limitation**: All automated tests run with `DRY_RUN=true` and a fake LLM that returns canned responses. The real OpenRouter integration, structured output tiers, retry logic, and spend tracking are only exercised manually.

**Impact**:
- Real model behavior (hallucination patterns, refusal styles, structured output compliance) is untested in CI
- Cost accounting and spend caps are only validated against mock responses
- Cache hit/miss behavior with real model outputs untested

**Mitigation**:
- LLM client unit tests use `httpx.MockTransport` with realistic response shapes
- Structured output tier logic is tested with Pydantic validation errors
- Live validation script: `python -m forensics.cli demo` (requires API key)

---

## 4. Small Demo Dataset (15 Documents)

**Limitation**: The seeded dataset contains only 15 documents (3 per type × 5 types) with exactly 5 seeded failures (1 per type + 1 mixed).

**Impact**:
- Statistical significance of analytics is negligible
- Failure type frequency, step frequency, and time-to-root-cause are not representative
- Eval loop trend tracking has insufficient data points

**Mitigation**:
- Dataset generation script (`seed` command) is stubbed for Phase 1 expansion to 50 documents
- Schema supports arbitrary document count
- Analytics endpoint gracefully handles small samples

---

## 5. Fake LLM in Tests Only

**Limitation**: The `FakeLLM` in `fake_llm.py` returns identical canned responses for all documents. It does not simulate model variability, refusal behavior, or structured output failures.

**Impact**:
- Tests verify structure, not semantic correctness
- Retry logic on 429/5xx is tested with mock transport, not real model behavior
- Cache key differentiation by prompt content not fully exercised

**Mitigation**:
- MockTransport tests use document-specific response payloads
- Real model integration tested manually via `demo` and `run` commands

---

## 6. No React Frontend (Streamlit Only)

**Limitation**: The trace explorer is built with Streamlit, not a custom React/React Flow application.

**Impact**:
- Limited customization of graph visualization (node layout, edge routing, animations)
- No real-time updates (WebSocket) for live trace streaming
- Less polished UX compared to a custom React Flow implementation

**Mitigation**:
- Streamlit provides sufficient interactivity for demo purposes
- Plotly + NetworkX graph is interactive (zoom, pan, hover tooltips)
- React/React Flow frontend deferred to v1 (see `PLAN.md` Phase 4)

---

## 7. No OTel Collector Integration

**Limitation**: The OTel export uses a custom `ForensicsSpanProcessor` with `SimpleSpanProcessor` + `ConsoleSpanExporter` (or in-memory exporter). No OTel Collector is deployed.

**Impact**:
- Spans not visible in standard observability platforms (Jaeger, Zipkin, Grafana Tempo)
- No distributed trace context propagation across services
- No sampling, batching, or export resilience

**Mitigation**:
- `ForensicsSpanProcessor` implements the standard `SpanProcessor` interface
- Swapping to `BatchSpanProcessor` + OTLP exporter is a one-line change
- Stdout exporter works for local demo/debugging

---

## 8. No Multi-Tenant / Auth

**Limitation**: Single-user, no authentication, no tenant isolation. All traces, flags, diagnoses, eval cases share the same SQLite database.

**Impact**:
- Not suitable for production multi-user deployment
- No RBAC, audit logging, or data isolation

**Mitigation**:
- Single-user scope matches demo/portfolio scope
- SQLite + file-based JSON is portable and inspectable
- Multi-tenancy planned for v1+ with PostgreSQL backend

---

## 9. No Scheduled Eval / Alerting

**Limitation**: Eval runs are manual (`eval-run` command). No cron, no webhook triggers, no Slack/email alerts on regression.

**Impact**:
- Regression detection requires manual intervention
- No continuous monitoring of failure trends

**Mitigation**:
- `eval-run` CLI command is idempotent and scriptable
- Can be wrapped in cron: `0 2 * * * /path/to/forensics eval-run`
- GitHub Actions workflow can run eval on PR merge

---

## 10. No Custom Check Plugins

**Limitation**: The 6 deterministic checks are hardcoded in `analyzer_simple.py`. No plugin system for domain-specific checks.

**Impact**:
- Cannot add checks like "PII detection", "citation verification", "format compliance" without code changes
- Not extensible for domain-specific pipelines

**Mitigation**:
- Check functions are isolated and follow a consistent signature
- Plugin architecture planned for v1 (`checks/` directory with entry points)

---

## 11. Single-Process SQLite

**Limitation**: SQLite with WAL mode handles concurrent reads but single writer. Not suitable for high-throughput or multi-process deployments.

**Impact**:
- CLI, API, and Streamlit cannot all write concurrently
- Not horizontally scalable

**Mitigation**:
- Demo scope is single-process
- PostgreSQL migration path documented for v1+
- Read-heavy workload (traces written once, read many times)

---

## 12. No Prompt Versioning in Traces

**Limitation**: Traces store `prompt_version` string but the actual prompt templates are not versioned in storage. Changing a prompt template doesn't create a new version automatically.

**Impact**:
- Cannot reproduce exact prompt that produced a trace
- A/B testing of prompts requires manual version management

**Mitigation**:
- `prompt_version` field exists in `Trace` and `Span` models
- CLI `demo` command uses fixed prompt versions
- Full prompt content + hash versioning planned for v1

---

## 13. No PII Redaction

**Limitation**: Document text and span I/O are stored verbatim in JSON and SQLite. No automatic PII detection/redaction.

**Impact**:
- Sensitive data in documents ends up in trace files and database
- Not compliant with GDPR/CCPA for production use

**Mitigation**:
- Demo dataset contains no real PII
- `analyzer_simple.py` never logs document content
- PII redaction pipeline planned for v1+

---

## 14. No Cost Estimation Before Request

**Limitation**: Spend caps are enforced after the HTTP response (actual token counts). No pre-flight estimation to reject requests likely to exceed caps.

**Impact**:
- A single request can exceed the per-run cap before being rejected
- Global cap may be exceeded by one request

**Mitigation**:
- Caps are generous for demo ($0.50/run, $20 global)
- Post-request accounting prevents repeated overages
- Pre-flight estimation (using `max_tokens` × price) planned for v1

---

## 15. No Horizontal Scaling

**Limitation**: All components (pipeline, API, Streamlit, eval) run in a single Python process. No worker pool, no message queue, no horizontal scaling.

**Impact**:
- Throughput limited to ~1 document/second (LLM latency bound)
- No parallel eval case execution
- No load balancing

**Mitigation**:
- Demo scope is single-threaded
- `Pipeline` class is stateless and trivially parallelizable
- Celery/RQ integration planned for v1+

---

## Summary

| # | Limitation | Severity | v1 Target |
|---|------------|----------|-----------|
| 1 | No Judge LLM | High | ✅ Phase 3 |
| 2 | No Propagation Analysis | High | ✅ Phase 3 |
| 3 | Dry-Run Only in CI | Medium | Phase 1+ |
| 4 | Small Demo Dataset | Low | Phase 1 |
| 5 | Fake LLM in Tests | Medium | Phase 1+ |
| 6 | Streamlit Only | Low | Phase 4 |
| 7 | No OTel Collector | Low | Phase 2+ |
| 8 | No Multi-Tenant/Auth | Low | Phase 1+ |
| 9 | No Scheduled Eval/Alerting | Medium | Phase 5 |
| 10 | No Custom Check Plugins | Medium | Phase 3+ |
| 11 | Single-Process SQLite | Low | Phase 1+ |
| 12 | No Prompt Versioning | Low | Phase 3+ |
| 13 | No PII Redaction | Medium | Phase 1+ |
| 14 | No Pre-Flight Cost Estimation | Medium | Phase 1+ |
| 15 | No Horizontal Scaling | Low | Phase 1+ |

---

**Philosophy**: This tool is a *forensics* tool, not a *monitoring* tool. Its job is to answer "which step broke and why?" after a failure occurs — not to prevent failures in real-time. The deterministic analyzer provides fast, explainable, auditable root-cause localization without the cost, latency, and opacity of a judge LLM. The limitations above are explicit trade-offs to ship a working, auditable system in 2 days, not omissions.
