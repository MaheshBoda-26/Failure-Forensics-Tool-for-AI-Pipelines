# ADR 0011: Dataset Construction — 50 Documents with Fault Injection

## Status
Accepted

## Context
The analyzer's headline metrics (localization accuracy, category accuracy) require free ground truth. We get this by seeding failures.

## Decision
**50 documents total**, composition:

| Document Type | Count | Seeded Failures |
|---|---|---|
| Contract | 15 | 3 (no-date, no-party, ambiguous clause) |
| Invoice | 15 | 3 (mixed currency, missing total, malformed line items) |
| Report | 10 | 2 (missing section, contradictory findings) |
| Correspondence | 10 | 2 (ambiguous intent, missing context) |

Total seeded failures: **10** (exceeds brief's 8-10 minimum). Each failure category has ≥2 documents.

Failure injection methods:
- **Natural difficulty**: Hand-written/edge-case documents (no-date contract, mixed-currency invoice).
- **Synthetic fault injection**: Programmatic corruption of a clean document's step output (drop entity, swap label, truncate context) — the `faults.py` harness.

`labels.json` schema:
```json
{
  "schema_version": 1,
  "documents": [
    {
      "doc_id": "contract_001",
      "type": "contract",
      "source_path": "dataset/documents/contract_001.txt",
      "should_fail": true,
      "failures": [
        {"step": "extraction", "category": "extraction_hallucination", "details": "entity 'John Smith' not in source"}
      ]
    }
  ]
}
```

No real PII — all names, amounts, dates are synthetic.

## Rationale
- Known weaknesses #3: "We can get free ground truth" by deliberately seeding failures.
- Fault injection harness gives controlled, labeled data for analyzer scoring.
- 50 documents × 4 steps = 200 spans = sufficient for confusion matrix.

## Consequences
- Dataset creation is manual work (the long pole). Start Day 1.
- Synthetic faults ensure every category has clean ground truth.
- `labels.json` is the single source of truth for analyzer scoring.

## Verification
- `python -m forensics.cli validate-dataset` passes: counts, schema, ≥2/category
- `pytest tests/test_dataset.py` loads labels and asserts invariants
