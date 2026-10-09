# ADR 0006: Frontend — React + Vite + React Flow (Primary) / Streamlit (Fallback)

## Status
Accepted

## Context
The trace explorer (Phase 4) needs a node graph with color-coded nodes, side-by-side diff view, and interactive flag/confirm/override.

## Decision
**Primary:** React 18 + Vite + TypeScript + React Flow.
**Fallback (Day 9 if behind):** Streamlit.

## Scoring

| Criterion | React + React Flow | Streamlit |
|---|---|---|
| Node graph quality | Full DAG layout, drag, zoom, clickable nodes | Basic graphviz, static |
| Diff view | Component-based side-by-side with highlighting | `st.dataframe` or raw text |
| Flag/confirm/override | Real client state, optimistic updates | Full rerun on interaction |
| Build time | ~2 days | ~4 hours |
| Portfolio signal | Strong (real SPA) | Weak (notebook-style) |

## Rationale
The prompt's own axes — node-graph quality, diff view, interaction effort — all favor React. The 2-day cost is acceptable for a 14-day project; Streamlit is the documented fallback.

## Consequences
- Phase 4 Day 9 is the decision point: if backend is behind, switch to Streamlit.
- FastAPI serves `frontend/dist` in production; Vite dev server in development.
- React Flow is the only heavy dep; the rest is standard Vite/React/TS.

## Verification
- `npm run build` in `frontend/` produces `dist/`
- `make run-ui` starts Vite dev server
- `make dev` serves `frontend/dist` at `/`
