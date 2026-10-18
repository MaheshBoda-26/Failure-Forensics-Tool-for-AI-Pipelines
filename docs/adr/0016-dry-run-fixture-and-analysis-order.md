# ADR 0016: Document-Keyed DRY_RUN Fixture and Analysis Before Redaction

## Status
Accepted

## Context

Two guarantees turned out to be false, and both were about ordering — what runs
before what.

**1. `DRY_RUN` did not reproduce the seeded failures.** `docs/limitations.md` §7
and the README both state that `DRY_RUN=true` reproduces the five seeded failures
in `dataset/labels.json`. The fixture instead picked a document type from
keywords in the *prompt*. The classification prompt carries only extracted
entities — no document text, no filename — so it could never name the type, and
no dataset document contains its own filename stem. Every document defaulted to
`contract`: the seeded `contract_001` misclassification disappeared, twelve
clean documents acquired a misclassification they do not have, and
`report_001` produced `propagation_error` instead of its declared
`prompt_failure` (its canned summary used contract terminology, which is a
propagation signal, and a validated `SummaryOutput` could not represent the
schema violation the label describes). Measured: 3/15 classifications correct,
0/5 seeded `(step, category)` pairs reproduced.

**2. The analyzer ran after PII redaction.** `save_trace` calls `finalize_trace`,
which redacts `span.output_json` in place when `PII_REDACTION_ENABLED=true`. Every
call site then ran `analyze_trace` on the already-redacted trace while
`doc_text` stayed unredacted, so an extracted email, phone, or IP was `███` in
the span and plaintext in the document — and `check_entity_grounding` flagged
every correctly extracted entity as an `extraction_hallucination`.

## Decision

- **Thread `document_id` explicitly.** `Pipeline.run(document, document_id)` →
  each `LLMClient` step → `NIMClient.run_with_fallback` → `structured`. It is a
  parameter, not a context variable or client attribute, so a concurrent run
  cannot inherit another run's id. It never enters a prompt, so a real model call
  is unaffected.
- **The fixture is keyed on the document id.** The dataset id prefix
  (`contract_`, `invoice_`, …) is the ground truth the fixture can actually see;
  the old prompt-keyword heuristic survives only as the fallback for an id with
  no prefix.
- **`report_001` is built with `SummaryOutput.model_construct`** to emit the
  schema violation its label describes (`summary` has `min_length=1`, so a
  validated model could never represent it). The payload serializes as ordinary
  JSON, so the analyzer reports a schema failure rather than a serializer error.
- **Analyze first, then persist.** `analyze_trace` runs against the unredacted
  in-memory trace; `save_trace` redacts on write. The resulting evidence can quote
  the document, so the diagnosis is redacted at its own write boundary
  (`Storage.create_diagnosis` → `redact_diagnosis`), keeping the promise that the
  unredacted original never reaches disk.

## Consequences

- `DRY_RUN` now reproduces all 15 labels: 5 seeded failures with their declared
  step and category, 10 clean documents passing. `forensics demo` reports 13/15
  classifications (the two misses are the seeded misclassifications, by design),
  and seeded-failure step/category accuracy is 5/5 and 5/5.
- The fixture is still hand-written canned output. It validates the **analyzer
  and the eval loop**, not model behaviour; a representative accuracy figure
  still requires a real API key. `limitations.md` §7 says so.
- The fixture recognizes dataset-shaped ids (`<type>_NNN`) plus the keyword
  fallback; an arbitrary production `doc_id` gets the fallback.
- `GET`-time re-diagnosis (`POST /traces/{id}/diagnose`) reads the *persisted*
  trace, which is redacted by design. Under `PII_REDACTION_ENABLED=true` that
  endpoint therefore still sees redacted payloads. The write path is fixed; this
  read path is a genuine limit, recorded in `limitations.md`.
- Diagnosis evidence is redacted before it is written, so the eval loop and the
  UI see `█` in evidence strings when redaction is on. That is the intended
  trade: the boundary is disk, not the in-memory verdict.
