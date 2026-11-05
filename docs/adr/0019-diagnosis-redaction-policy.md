# ADR 0019: Re-Diagnosis Under PII Redaction Is Stored-or-Explicit

## Status
Accepted

## Context
The write path analyzes the *unredacted* trace and then redacts it at the
persistence boundary. `POST /traces/{trace_id}/diagnose` instead loads the
persisted trace and re-runs the deterministic checks. With
`PII_REDACTION_ENABLED=true`, those persisted spans are redacted by design:
an extracted email, phone, or IP is `█` in the span while `doc_text` is not.
`entity_grounding` then flags the correctly extracted entity as a hallucination —
a fabricated failure produced by the tool itself.

The obvious alternative, persisting an unredacted copy "for analysis", reopens
the leak that the redaction guarantee exists to close.

## Decision
`POST /traces/{trace_id}/diagnose` follows a **stored-or-explicit** contract when
redaction is enabled:

1. If a stored diagnosis exists, return it (200) with
   `X-Diagnosis-Source: stored`. The write-time verdict is trustworthy because it
   was produced against the unredacted trace.
2. If no stored diagnosis exists and the caller does not opt in, return `409`
   naming the cause and the two ways forward (confirm the stored diagnosis, or
   re-run with `?allow_redacted=true`).
3. `?allow_redacted=true` (write scope) forces a fresh analysis, sets
   `X-Diagnosis-Source: redacted-recompute`, and populates
   `Diagnosis.degraded_reason` so the UI can show a banner.
4. With redaction off, behaviour is unchanged: a fresh analysis, no header
   contract.

`Diagnosis.degraded_reason` is optional and additive, so existing records and
clients are unaffected. `TraceDetailView` renders an inline notice when it is
present.

## Rationale
- Returning the stored verdict is both cheaper and *more* correct than
  recomputing against degraded evidence.
- A 409 with an explicit opt-in keeps the degraded path available for debugging
  without letting it masquerade as a normal diagnosis.
- Never persisting an unredacted second copy keeps the redaction guarantee
  singular and auditable.

## Consequences
- Re-diagnosis can no longer fabricate a hallucination under redaction.
- Callers that relied on a silent recompute must handle 409 or pass
  `allow_redacted=true`; the UI already does the former by fetching the stored
  diagnosis.
- A trace that was redacted *before* this contract existed and has no stored
  diagnosis requires the explicit opt-in, which is the intended signal that the
  evidence is degraded.

## Verification
- `tests/test_rediagnose.py`: the stored path returns the identical diagnosis and
  header; the forced path returns `degraded_reason` and `redacted-recompute`; the
  no-redaction path is unchanged.
- `frontend` component test asserts the degraded banner renders.
