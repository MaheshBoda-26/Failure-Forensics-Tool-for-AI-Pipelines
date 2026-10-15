# Frontend — React + Vite + TypeScript Trace Explorer

Built for the Failure Forensics Tool. Runs as a dev server (`make run-web`) and
is served by FastAPI in production (`frontend/dist`).

## Quickstart

```bash
# Install dependencies
npm install

# Start dev server (proxies API to FastAPI on :8000)
npm run dev

# Build for production
npm run build
```

## Architecture

```
frontend/
  src/
    api/         Typed client for FastAPI endpoints (client.ts, types.ts)
    components/  TraceDetailView, DAGGraphView, TokenDiffView, TraceSpine, AnalyticsView, HonestyMatrix
    pages/       TraceList, TraceDetail, Analytics
    main.tsx     Entry point
    App.tsx      Root component with routing
```

## Features

- **Trace List** — Table with trace ID, document, status, spans, latency
- **Trace Detail** — Color-coded spans (🟢 pass, 🟡 tainted, 🔴 fail)
- **DAG Graph** — Dynamic execution graph built from trace spans and diagnosis
- **Evidence Chain** — Failed checks with per-step scores
- **Token Diff** — Expected vs actual classification/entity grounding from real trace data
- **Flag/Confirm/Override** — Creates Flag in SQLite, triggers analyzer, shows diagnosis
- **Analytics** — Stage vulnerability map, regression table from real eval data

## Tech Stack

- React 19 + TypeScript
- Vite 6 (dev server + build)
- React Flow (DAG visualization)
- Lucide React (icons)
- CSS variables for theming

## API Contract

Frontend types in `src/api/types.ts` must match FastAPI Pydantic models in `src/forensics/models.py`:

- `FailureCategory` enum: `extraction_hallucination`, `misclassification`, `propagation_error`, `prompt_failure`, `context_loss`, `unknown`
- `Span` fields: `input_json`, `output_json`, `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `model_id`, `provider`
- `Trace` fields: `total_latency_ms`, `total_tokens`, `total_cost_usd`
- `Diagnosis.evidence_chain` is `string[]` (not objects)
- `Diagnosis.step_scores` is `StepScore[]` with `deterministic_checks`

## Commands

```bash
make run-web        # From project root: starts Vite + FastAPI
npm run dev         # From frontend/: just Vite dev server
npm run build       # Build to frontend/dist for FastAPI static serving
npm run lint        # ESLint + TypeScript check
```
