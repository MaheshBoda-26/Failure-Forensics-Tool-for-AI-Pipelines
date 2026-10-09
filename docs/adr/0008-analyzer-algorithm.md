# ADR 0008: Analyzer Algorithm — Local + Global Fidelity

## Status
Accepted

## Context
The naive "walk back and stop at first quality drop" incorrectly blames a step that faithfully transforms bad input (Propagation victim). We need precise root-cause localization.

## Decision
Score **every step** on two dimensions:

1. **Local fidelity**: Is this step's output a faithful transformation of *its own input*? (LLM followed instructions, no hallucination relative to input)
2. **Global fidelity**: Is this step's output correct relative to the *original source document*? (Entities grounded, classification correct, summary accurate)

Algorithm:
```
For each span in reverse order:
  local_score = judge_local_fidelity(span.input, span.output, span.prompt)
  global_score = judge_global_fidelity(source_doc, span.output)

  If local_score < THRESHOLD and global_score < THRESHOLD:
    This step is the root cause (local failure propagated)
  Elif local_score >= THRESHOLD and global_score < THRESHOLD:
    This step is a Propagation victim — continue upstream
  Else:
    Continue upstream
```

The first step (from the end) with BOTH local and global failure is the root cause.

## Rationale
- Distinguishes "step N was wrong" (root cause) from "step N was right but input was garbage" (victim).
- The Propagation category in the taxonomy is precisely this: locally faithful, globally wrong.
- Scoring all steps produces a fidelity profile for the whole trace, not just a single culprit.

## Consequences
- Two judge calls per step (local + global) — mitigated by deterministic checks first.
- THRESHOLD is calibrated on labeled data; default 0.6 (VERIFY BEFORE USE).
- Evidence chain cites the specific local/global scores and the input/output pairs.

## Verification
- `pytest tests/test_analyzer.py::test_propagation_victim_not_blamed` — fault-injected trace where step 2 is correct but step 1 dropped an entity; analyzer blames step 1, not step 2
- `pytest tests/test_analyzer.py::test_fidelity_profile_all_steps_scored`
