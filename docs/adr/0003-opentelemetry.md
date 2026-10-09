# ADR 0003: OpenTelemetry Usage

## Status
Accepted

## Context
The brief requires OpenTelemetry for tracing. We need spans with context propagation but no collector/backend.

## Decision
Use the OTel SDK for span creation and context propagation. Implement a custom `SpanProcessor`/`SpanExporter` that writes:
1. Full span payloads as per-trace JSON files (git-friendly, inspectable)
2. Index and relational data to SQLite

Use OTel GenAI semantic convention attribute names where they exist (VERIFY BEFORE USE: check current attribute names in `opentelemetry-semantic-conventions`).

Do not require an OTel Collector or backend. All export is local.

## Rationale
- OTel gives us context propagation across async boundaries for free.
- Custom exporter keeps the data format exactly what we need (JSON + SQLite).
- No infrastructure beyond the laptop/Render.

## Consequences
- We own the span attribute naming; OTel conventions are advisory.
- No distributed tracing across services (single-process app).
- Span lifecycle: `start` → `set_attributes` → `end` (with error recording).

## Verification
- `pytest tests/test_tracing.py` asserts spans have required attributes
- `make check` passes with OTel SDK but no collector
