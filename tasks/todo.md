# Frontend redesign — Failure Forensics

Direction: `framed-sheet` (dealt, seed 1638682588). Palette kept from the project
(teal / cinnabar / ochre on a tinted frame); type reduced to IBM Plex Sans + Mono.
Question the overview answers: **what broke, and where?**
Signature element: the four-stage causal spine, first break in cinnabar, downstream tainted.

## Work

- [x] Design system: tokens, shell, panels, tables, pills, responsive rules (`src/index.css`)
- [x] Shell: top bar, sidebar, mobile menu, API-key popover (`components/Header.tsx`)
- [x] Shared helpers: formatting, status pill, metric, empty state (`lib/trace.ts`, `components/ui.tsx`)
- [x] Overview: KPIs, latest-failure spine, failures by stage, recent traces, guarantees
- [x] Explorer: list, filters, detail, diagnosis, stage payload (`TraceExplorer.tsx`, `TraceDetailView.tsx`, `TraceSpine.tsx`)
- [x] Execution graph + output comparison (`GraphPage.tsx`, `DAGGraphView.tsx`, `TokenDiffView.tsx`)
- [x] Analytics: KPIs, stage bars, correlation matrix, eval + model tables, spend/latency
- [x] Compact guarantees table (`HonestyMatrix.tsx`)
- [x] Hash routing with the selected trace in the URL
- [x] Build, tests, README

## Verification

- `npm run build` clean (tsc + vite)
- `npm test` 11 passing; `pytest tests/test_storage_eval_api.py tests/test_api_static_serving.py` 54 passing
- `sloplint frontend/src frontend/index.html --scope app --genre modern-minimal` → clean, 0 FAIL / 0 REFLEX / 0 WARN
- `inspect.mjs --app` on overview, traces, trace detail, graph, analytics at 1440 / 768 / 390 → 0 FAIL, 0 WARN on every route

## Review

**What changed.** Every screen was rebuilt on one shell: tinted frame, 48px top bar,
sidebar (collapses to a menu under 1023px), white sheet inset 8px with a 1px edge and
16px corners. Chrome carries no colour; the accent only marks data and state. Type is
one family plus one mono, sizes 12–28px, spacing on a 4px scale, so columns line up
across panels and rows.

**Density.** Tables and lists before cards. The overview leads with four metrics in one
bordered strip, then the causal spine, then two half-width panels. Analytics uses bars
and a step × category matrix rather than a wall of KPI cards.

**Copy.** Cut to labels and facts. Every number on screen comes from the API; nothing
invented. The guarantees table states the limit beside each promise.

**States.** Loading (spinner in the refresh control), empty (what belongs here plus the
one action), error (API-offline dot in the top bar, inline notices for failed actions),
and full. Under 900px the explorer becomes one pane: list, then detail with a back link.

**Gaps left.** The API returns no ground-truth label per document, so the output
comparison shows stored checks and says so. A trace with no spans (a fixture row) still
appears in the list and opens an empty detail; that is a data problem, not a layout one.
