"""Tracing layer — span lifecycle and the `@traced_step` decorator.

Uses the OpenTelemetry SDK for span creation and context propagation, with a
custom span processor that writes our JSON + SQLite. Instrumenting a new step
must cost one line. Also owns trace status computation (success / failure /
degraded) and how the self-reported confidence score is requested and stored.

Status: not implemented. See PROMPT.md C5.
"""
