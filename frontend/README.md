# Frontend — Phase 4 (Day 9)

Not scaffolded yet. Deliberate: the prompt schedules the trace explorer for
Days 9-11, and installing npm packages before the tracing layer exists buys
nothing.

## Decision (PROMPT.md decision #6)

**Primary: React + Vite + TypeScript + React Flow.**
Scored against the prompt's own axes — node-graph quality (React Flow gives a
real DAG layout and clickable nodes, which the green/yellow/red trace view
needs), diff view (needs side-by-side structured diff, so real components), and
flag/confirm/override interaction (needs real client state). It also produces
the stronger portfolio artifact.

**Fallback: Streamlit.** Switch on Day 9 if behind. Roughly four hours of work
instead of two days; weaker graph and diff view, no custom layout. Acceptable
for demonstrating the concept, weaker as an interview piece.

## Layout when built

```
frontend/
  src/
    api/         typed client for the FastAPI endpoints
    components/  TraceGraph, TraceNode, DiffView, FlagPanel, ConfirmPanel
    pages/       TraceList, TraceDetail, Analytics
    state/       trace + diagnosis state
```

FastAPI serves the built `frontend/dist` in production, so the Render deploy is
a single service. See `Makefile` targets `run-ui` and `dev`.
