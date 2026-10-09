"""Tests for config module."""

import pytest

from forensics.config import Settings, reset_settings, validate_startup


def _make_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Build a complete test environment."""
    base = {
        "OPENROUTER_API_KEY": "sk-or-test-key",  # pragma: allowlist secret
        "PIPELINE_MODEL_ID": "openai/gpt-4o-mini",
        "PIPELINE_MODEL_PROVIDER": "openai",
        "PIPELINE_MODEL_FAMILY": "openai",
        "PIPELINE_MODEL_PRICE_IN": "0.15",
        "PIPELINE_MODEL_PRICE_OUT": "0.60",
        "PIPELINE_FALLBACK_MODEL_ID": "anthropic/claude-3-haiku",
        "PIPELINE_FALLBACK_MODEL_PROVIDER": "anthropic",
        "PIPELINE_FALLBACK_MODEL_FAMILY": "anthropic",
        "PIPELINE_FALLBACK_MODEL_PRICE_IN": "0.25",
        "PIPELINE_FALLBACK_MODEL_PRICE_OUT": "1.25",
        "JUDGE_MODEL_ID": "google/gemini-1.5-pro",
        "JUDGE_MODEL_PROVIDER": "google",
        "JUDGE_MODEL_FAMILY": "google",
        "JUDGE_MODEL_PRICE_IN": "3.50",
        "JUDGE_MODEL_PRICE_OUT": "10.50",
        "CHEAP_MODEL_ID": "openai/gpt-3.5-turbo",
        "CHEAP_MODEL_PROVIDER": "openai",
        "CHEAP_MODEL_FAMILY": "openai",
        "CHEAP_MODEL_PRICE_IN": "0.50",
        "CHEAP_MODEL_PRICE_OUT": "1.50",
    }
    if overrides:
        base.update(overrides)
    return base


def test_settings_loads_from_env(monkeypatch):
    """Settings loads correctly from environment variables."""
    for k, v in _make_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)  # We'll set it explicitly
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")  # pragma: allowlist secret

    reset_settings()
    # Use _env_file=None to ignore .env file
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.openrouter_api_key == "sk-or-test-key"  # pragma: allowlist secret
    assert settings.pipeline_model.model_id == "openai/gpt-4o-mini"
    assert settings.pipeline_model.family == "openai"
    assert settings.pipeline_model.price_in_per_1m == 0.15
    assert settings.judge_model.family == "google"
    assert settings.cheap_model.family == "openai"


def test_judge_family_separation_fails(monkeypatch):
    """Judge model from same family as pipeline should fail."""
    env = _make_env(
        {
            "JUDGE_MODEL_ID": "openai/gpt-4o",
            "JUDGE_MODEL_PROVIDER": "openai",
            "JUDGE_MODEL_FAMILY": "openai",
        }
    )
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    with pytest.raises(ValueError, match=r"Judge model family.*matches pipeline family"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_duplicate_model_ids_fails(monkeypatch):
    """Duplicate model IDs across pipeline/judge/cheap should fail."""
    env = _make_env(
        {
            "JUDGE_MODEL_ID": "openai/gpt-4o-mini",  # Duplicate!
            "JUDGE_MODEL_PROVIDER": "google",
            "JUDGE_MODEL_FAMILY": "google",
        }
    )
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    with pytest.raises(ValueError, match=r"Duplicate model IDs detected"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_missing_api_key_fails(monkeypatch):
    """Missing API key should fail."""
    env = _make_env()
    del env["OPENROUTER_API_KEY"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    reset_settings()
    with pytest.raises(ValueError, match=r"Field required"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_empty_api_key_fails(monkeypatch):
    """Empty API key should fail."""
    env = _make_env({"OPENROUTER_API_KEY": ""})
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    with pytest.raises(ValueError, match=r"OPENROUTER_API_KEY must be set"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_invalid_api_key_format_fails(monkeypatch):
    """Invalid API key format should fail at startup validation."""
    env = _make_env({"OPENROUTER_API_KEY": "invalid-key"})
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    Settings(_env_file=None)  # type: ignore[call-arg]  # Load succeeds
    with pytest.raises(ValueError, match=r"does not look like a valid OpenRouter key"):
        validate_startup()  # But startup validation fails


def test_missing_model_field_fails(monkeypatch):
    """Missing required model field should fail."""
    env = _make_env()
    del env["PIPELINE_MODEL_ID"]  # Missing required field
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    with pytest.raises(ValueError, match=r"pipeline_model.model_id is empty"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_validate_startup_success(monkeypatch):
    """Valid config passes startup validation."""
    for k, v in _make_env().items():
        monkeypatch.setenv(k, v)

    reset_settings()
    validate_startup()  # Should not raise
