"""Failure taxonomy — the operational spec as code.

Definitions, observable signatures, deterministic detectors, and confusable-
neighbor separation rules for: Extraction Hallucination, Misclassification,
Propagation Error, Prompt Failure, Context Loss, and Unknown/Other.

Propagation is the delicate one: a step whose output is locally faithful to a
bad input is a victim of upstream error, not the root cause.

Status: not implemented. See PROMPT.md D5.
"""
