"""Eval and regression engine.

Turns confirmed flags into eval cases, re-runs the accumulated dataset against
the current pipeline, and tracks whether known failures are still failing or
fixed. Defines the per-step-type metric and what "fixed" means for each.

Status: not implemented. See PROMPT.md C10.
"""
