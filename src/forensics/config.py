"""Settings loaded from env, plus startup validation.

Loads model ids, prices, paths, and caps from `.env` / environment. Must fail
fast at startup on an unknown model slug, an unset API key, or a judge model
that shares a provider family with the pipeline models.

Status: not implemented. See PROMPT.md C1 and provider_constraint_openrouter.
"""
