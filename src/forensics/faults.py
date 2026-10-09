"""Fault-injection harness — deterministic ground truth.

Corrupts a specific step's output in a controlled way (drop an entity, swap the
label, truncate context) so the analyzer can be scored objectively: step-
localization accuracy, category accuracy, evidence quality.

Status: not implemented. See PROMPT.md D6.
"""
