# ADR 0001: Custom Chain vs LangChain

## Status
Accepted

## Context
The pipeline framework choice affects tracing clarity, testability, and debugging ergonomics.

## Decision
Use a **custom, thin typed chain**. No LangChain dependency.

## Rationale
- LangChain adds abstraction layers that obscure span boundaries — exactly what tracing needs to be explicit about.
- The four steps are simple function compositions; a custom chain is ~50 lines of obvious Python.
- Testability: pure functions with typed inputs/outputs are trivial to unit test without mocking framework internals.
- Tracing clarity: the `@traced_step` decorator wraps exactly one function; no hidden middleware.

## Consequences
- No pre-built components (prompt templates, output parsers) — we write our own.
- Adding a step is one function + one decorator line.
- Optional LangChain adapter mentioned only as a non-goal.

## Verification
- `grep -r "langchain" src/ --include="*.py"` returns nothing
- `make check` passes with zero LangChain deps
