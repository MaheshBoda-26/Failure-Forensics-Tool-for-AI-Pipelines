# ADR 0004: Storage Split — JSON + SQLite

## Status
Accepted

## Context
Trace payloads can be large (full prompts, raw responses). We need both inspectability and queryability.

## Decision
- **Full span payloads** → per-trace JSON files in `data/traces/{trace_id}.json`
- **Index + relational tables** → SQLite at `data/forensics.db`

JSON files contain the complete trace: trace_id, timestamp, status, spans (with all attributes), final output.

SQLite tables:
- `traces` (trace_id PK, timestamp, status, pipeline_version, final_score)
- `spans` (span_id PK, trace_id FK, step_name, input_json, output_json, tokens, latency, confidence, model, provider)
- `flags` (flag_id PK, trace_id FK, user_id, timestamp, note)
- `diagnoses` (diagnosis_id PK, trace_id FK, root_cause_step, category, evidence_json, confidence, analyzer_version)
- `eval_cases` (case_id PK, trace_id FK, failing_step, bad_output, corrected_output, category, created_at)
- `eval_runs` (run_id PK, pipeline_version, prompt_versions_json, started_at, completed_at, passed_count, failed_count)
- `eval_results` (result_id PK, run_id FK, case_id FK, step_name, score, fixed BOOLEAN)

## Rationale
- JSON files: human-readable, diffable, portable, no schema migration needed.
- SQLite: fast queries for analytics, joins for flag→diagnosis→eval traceability, ACID for concurrent API writes.
- Consistency: write JSON first (atomic tmp+rename), then SQLite index. If JSON write fails, no index entry. If SQLite fails after JSON, a repair script can re-index.

## Consequences
- Two write paths must be kept in sync.
- Migration path: if scale demands Postgres, JSON files remain the source of truth; SQLite becomes a read replica.

## Verification
- `pytest tests/test_storage.py` — write trace → verify JSON + SQLite row count match
- `make check` passes with atomic write test
