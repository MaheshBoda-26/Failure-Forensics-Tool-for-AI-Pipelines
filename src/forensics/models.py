"""Pydantic domain models.

Covers Document, IntakeOutput, ExtractionOutput, ClassificationOutput,
SummaryOutput, Span, Trace, StepScore, Diagnosis, Flag, EvalCase, EvalRun,
EvalResult. Every record carries pipeline_version, prompt_version, and
schema_version; Span and Diagnosis additionally carry model id and pinned
provider routing, since OpenRouter makes the exact upstream model part of a
result's identity.

Status: not implemented. See PROMPT.md C2.
"""
