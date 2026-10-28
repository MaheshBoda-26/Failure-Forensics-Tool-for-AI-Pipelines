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

---

# Landing page (2026-10-28)

Direction: Hallmark `Map / Diagram` (draw 9d8ac7), theme **Wayfind** dealt 3/3,
material only (2px ink keylines, mono figures, one ink band). Palette and faces are
the product's own so the page and the app read as one. Job: **try**; the action is
"Open the trace explorer", in the hero and at the close.

- [x] Masthead nav (centred wordmark over the links), not the wordmark-left bar
- [x] Hero: head, one sentence, two actions, the four-stage sign with the break marked
- [x] Before and after: a wrong output beside the same run's diagnosis
- [x] How it works: four stages, four checks, plus the backward-trace rule
- [x] The product: three real captures from this build
- [x] Use cases: four rows
- [x] Limits: capability, guarantee, limit
- [x] Runs on your machine: runtime, interface, storage, model, two commands
- [x] Close: the action beside one last screen; footer with links and colophon
- [x] Routing: `#/` is the landing, `#/overview` the workspace; "About" in the app sidebar

## Verification

- `npm run build` clean, `npm test` 11 passing
- `sloplint Landing.tsx landing.css --scope page` clean (0 FAIL / 0 REFLEX / 0 WARN)
- `sloplint` on the 13 workspace files under `--scope app` clean
- `inspect.mjs` landing at 1440 / 768 / 390: 0 FAIL, 3 WARN defended
  - heavy borders: the Wayfind material is 2px ink keylines, the page is a sign
  - below-fold: the hero is Settled and fills its screen by design
  - app-title-size: the inspector read the page as an app screen; it is a marketing page

## Notes

- The render tier of `sloplint --render` needs puppeteer, which is not installed here;
  the browser pass ran through `inspect.mjs` instead. Logged as observation 2.
- Screenshots are real captures of this build at 1440x900, not mockups. They stay
  legible as evidence on desktop; on a phone they read as thumbnails.
