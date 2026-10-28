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
    lib/trace.ts Step order, status rules, number/date formatting
    components/
      Header.tsx        Top bar, sidebar, mobile menu, API-key popover
      Overview.tsx      KPIs, latest-failure spine, failures by stage, recent traces
      TraceExplorer.tsx List + filters + detail pane (single pane under 900px)
      TraceDetailView.tsx  Diagnosis, evidence, per-stage checks, stage payload
      TraceSpine.tsx    Four-stage execution spine
      GraphPage.tsx     Stage graph + output comparison
      DAGGraphView.tsx  Stage cards and the first break
      TokenDiffView.tsx Pass criteria against observed output
      AnalyticsView.tsx Stage bars, correlation matrix, evals, models, spend
      HonestyMatrix.tsx Guarantees and limits table
      ui.tsx            Status pill, metric, section head, empty state, mark
    main.tsx     Entry point
    App.tsx      Root component: data loading, hash routing, URL state
```

## Design

One shell for every screen: a tinted frame holding the top bar and sidebar, and a
white sheet inset from the right and bottom edges. Chrome is neutral; colour is
reserved for data and status (teal for a passing check, cinnabar for the first
break and its downstream, ochre for degraded). Type is IBM Plex Sans for text and
IBM Plex Mono for IDs, payloads and tabular figures. Spacing runs on a 4px scale.

Routes are hash-based and hold their state: `#/` (landing page), `#/overview`,
`#/traces`, `#/traces/<trace_id>`, `#/graph`, `#/analytics`.

## Features

- **Overview** — Pass rate, failure rate, mean time to root cause, open regressions, the latest failure's four-stage spine with the first break marked, failures by stage, recent traces, guarantees
- **Trace List** — Searchable, filterable (all / failures / passing), newest first
- **Trace Detail** — Colour-coded spine (pass, root cause, downstream tainted), evidence chain, per-stage checks, raw stage payload
- **DAG Graph** — Stage cards with the failing link and downstream marked, zoom, selected-stage inspector
- **Output comparison** — Pass criteria against the observed output for the failing stage
- **Flag / Confirm / Override** — Writes a flag, triggers the analyzer, records a manual category
- **Analytics** — Stage vulnerability bars, step × category correlation matrix, eval regressions, model table, spend and latency

## Tech Stack

- React 19 + TypeScript
- Vite 6 (dev server + build)
- Lucide React (icons)
- Plain CSS with tokens in `src/index.css`

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
