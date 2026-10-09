"""OpenRouter client — the single LLM entry point for the whole project.

Exists so that prompt, raw response, token usage, provider routing, latency,
retries, caching, replay, and spend caps are captured in exactly one place
instead of leaking into every step. Handles the three-tier structured-output
fallback and both plain and reasoning-model response shapes.

Status: not implemented. See PROMPT.md C4 and provider_constraint_openrouter.
"""
