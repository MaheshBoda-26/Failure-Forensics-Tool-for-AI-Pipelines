# ADR 0000: Project Assumptions and Constraints

## Status
Accepted

## Context
This document records the assumptions made when generating the build plan for the Failure Forensics Tool. Per the prompt's working principle 1: "Do not stop to ask me questions; make a defensible choice, record it, and flag it."

## Assumptions

| # | Assumption | Value | Rationale |
|---|---|---|---|
| A1 | Team size / hours per day | Solo, 6 h/day | Typical solo student project |
| A2 | Budget | $30 OpenRouter credits | Matches the prompt example; $10 min top-up |
| A3 | Deadline | 14 calendar days | Per prompt brief |
| A4 | Grading | Ungraded portfolio piece | No external rubric; interview-ready artefact |
| A5 | Dev OS | macOS | User confirmed |
| A6 | Hosting | Render free tier + persistent disk (512 MB) | SQLite + JSON traces fit; no Docker needed |
| A7 | Judge model constraint | None (different family enforced via config) | Prevents self-preference bias; configurable |

## Alternatives Considered

| Assumption | Alternative | Why Rejected |
|---|---|---|
| A1 | 4 h/day | Would require cutting dataset to 30 docs and dropping Phase 4 polish |
| A2 | $15 | Too tight for judge self-consistency + 50-doc eval runs |
| A3 | 10 days | Insufficient for dataset + analyzer + UI + demo |
| A6 | Vercel + Supabase | Requires storage refactor; Render keeps current SQLite/JSON design |
| A7 | Same family for judge | Known weakness (self-preference bias); free to avoid on OpenRouter |

## Corrections

- **A6 (hosting) is half wrong.** Render does not offer persistent disks on the
  free tier: a disk requires a paid instance (`plan: starter`), and free services
  also spin down when idle, which stops a nightly eval. `render.yaml` therefore
  declares `plan: starter` with the disk, and `README.md` documents the free-plan
  variant (no disk, ephemeral `data/`, re-seed after each deploy).

## Consequences
- The plan assumes 84 total hours (14 × 6). If actual hours drop to 4/day (56h), the cut list activates immediately.
- $30 budget with $10 minimum top-up leaves $20 usable. At ~$0.05/run (cheap pipeline model), ~400 runs possible — sufficient for development + 2 full eval passes.
- No external grading means the "hours to seconds" claim must be honestly measured, not asserted.
- Render persistent disk (512 MB) is enough for SQLite + ~500 trace JSON files. If disk fills, the app degrades to read-only demo mode.

## Verification
- `grep -r "\[e\]" PROMPT.md` returns no matches in `<builder_context>`
- All downstream tasks reference these values explicitly
