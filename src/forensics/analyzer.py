"""Backward trace analyzer — root-cause localization.

Scores every step on local and global fidelity, then derives the root cause
from causal ordering. Deterministic checks run before any LLM call; the judge is
used only for what code cannot decide. Assembles the evidence chain from real
input/output pairs.

Status: not implemented. See PROMPT.md C7.
"""
