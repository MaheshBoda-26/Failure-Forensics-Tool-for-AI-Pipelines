# SPEC: OTel Export Integration

## Summary
Wire the existing `otel_export.py` scaffold into production. Enable OTLP/HTTP and console exporters via environment variables. Zero-cost default (disabled).

## Config (`.env`)
```bash
OTEL_EXPORTER=none|otlp|console     # default: none
OTEL_ENDPOINT=http://localhost:4318/v1/traces  # OTLP/HTTP
OTEL_SERVICE_NAME=failure-forensics
OTEL_SAMPLING_RATE=1.0              # 1.0 = always export
```

## Changes

### `src/forensics/config.py`
Add `OTelSettings` nested model:
```python
class OTelSettings(BaseModel):
    exporter: Literal["none", "otlp", "console"] = "none"
    endpoint: str = "http://localhost:4318/v1/traces"
    service_name: str = "failure-forensics"
    sampling_rate: float = Field(default=1.0, ge=0.0, le=1.0)
```
Expose via `Settings.otel: OTelSettings` (loaded from env with `OTEL_` prefix).

### `src/forensics/otel_export.py`
- Add `ConsoleSpanExporter` import and branch in `configure_otel`
- Add `OTLPSpanExporter` (requires `opentelemetry-exporter-otlp` — add to `pyproject.toml` optional dep `otel`)
- `configure_otel` reads `get_settings().otel` and configures global provider once

### `src/forensics/tracing.py`
- In `trace_context.__enter__`: call `configure_otel()` if not already configured
- In `finalize_trace`: if OTel configured, create `ForensicsSpanProcessor` and call `on_end` for each span
- Pass `max_prompt_chars` from settings

### `src/forensics/cli.py`
- `ping` command: show OTel status (exporter, endpoint, configured)

## Dependencies
```toml
[project.optional-dependencies]
otel = ["opentelemetry-exporter-otlp>=1.20", "opentelemetry-sdk>=1.20"]
```

## Acceptance Criteria
1. `OTEL_EXPORTER=none` (default): no OTel imports at runtime, no spans exported
2. `OTEL_EXPORTER=console`: spans printed to stdout on trace completion
3. `OTEL_EXPORTER=otlp OTEL_ENDPOINT=http://jaeger:4318/v1/traces`: spans appear in Jaeger
4. `OTEL_SAMPLING_RATE=0.1`: ~10% of traces exported (head-based)
5. No performance impact when disabled

## Effort
- **Files**: 3 modified, 1 new optional dep
- **Time**: ~2 hours
- **Risk**: Low (scaffold exists, only wiring needed)
