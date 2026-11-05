# ADR 0018: Archive Prompt Templates by Content Hash

## Status
Accepted

## Context
Every span records the fully rendered prompt and a `prompt_version` hash, but the
*template* that produced the prompt lived only as inline strings in `llm.py`. A
trace could therefore answer "what was sent to the model?" but not "which
template revision produced this prompt, and what else does that revision say?".
`docs/limitations.md` §9 captured the gap: fixtures and replay key on prompt
identity, so the identity must be retrievable.

Prompt text is special: changing a character changes `prompt_version`, which is
part of a trace's identity, and invalidates the offline fixtures. The archive had
to be added without changing a byte of prompt wording.

## Decision
- Extract the five prompt builders from `llm.py` into `src/forensics/prompts.py`
  as a **pure move**. A test hashes the rendered prompts and asserts byte
  equality before and after the move.
- Add `PromptTemplate` (`models.py`): `name`, `version` (SHA-256 of
  `template_text`), `template_text`, `created_at`, plus the standard tenant
  columns.
- Add `save_prompt_template`, `get_prompt_template`, and
  `list_prompt_templates` to `StorageBase`, implemented in both SQLite and
  PostgreSQL, with a migration for the new `prompt_templates` table.
- Each `Span` records `prompt_template` (the template *name*, e.g.
  `extraction-v1`). The full text is stored once per (name, content hash) row,
  upserted idempotently by `save_trace`, rather than duplicated on every span.
- Expose `GET /prompt-templates` and `GET /prompt-templates/{name}/{version}`
  (read scope), plus `forensics prompts list` and `forensics prompts show`.

## Rationale
- Content hashing makes the version self-verifying: the same template text
  cannot appear under two versions, and a version cannot silently change.
- One row per version keeps traces small while preserving exact replay.
- A pure move with a byte-equality test is the only safe way to refactor prompts
  whose hashes are already persisted in committed fixtures.

## Consequences
- `prompt_version` stays on every `VersionedModel`; the archive is additive.
- The `prompt_templates` table is tenant-scoped like every other table, so a
  tenant sees only its own archived templates.
- Prompt wording is now frozen behind a test. Any prompt change becomes its own
  phase with fixture regeneration, as the working agreement requires.
- Templates are static text only; secrets and per-run data never live there.

## Verification
- `tests/test_prompt_templates.py`: a template row exists after a `DRY_RUN`
  trace; the stored hash matches the rendered text; an unknown version is 404;
  tenant scoping holds.
- `tests/test_schema_parity.py` covers the new table in both backends.
- `forensics prompts list` after a demo shows the expected template rows.
