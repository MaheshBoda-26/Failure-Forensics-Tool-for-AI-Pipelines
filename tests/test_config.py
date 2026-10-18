"""Tests for config module."""

from pathlib import Path

import pytest

from forensics.config import Settings, reset_settings, validate_startup

#: A non-default key prefix, so evidence that the setting bound at all.
NON_DEFAULT_PREFIX = "test_"


def _make_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Build a complete test environment."""
    base = {
        "NVIDIA_API_KEY": "nvapi-test-key",  # pragma: allowlist secret
        "PIPELINE_MODEL_ID": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "PIPELINE_MODEL_PROVIDER": "nvidia",
        "PIPELINE_MODEL_FAMILY": "nvidia",
        "PIPELINE_MODEL_PRICE_IN": "0.15",
        "PIPELINE_MODEL_PRICE_OUT": "0.60",
        "PIPELINE_FALLBACK_MODEL_ID": "meta/llama-3.3-70b-instruct",
        "PIPELINE_FALLBACK_MODEL_PROVIDER": "meta",
        "PIPELINE_FALLBACK_MODEL_FAMILY": "meta",
        "PIPELINE_FALLBACK_MODEL_PRICE_IN": "0.25",
        "PIPELINE_FALLBACK_MODEL_PRICE_OUT": "1.25",
        "JUDGE_MODEL_ID": "google/gemma-3-27b-it",
        "JUDGE_MODEL_PROVIDER": "google",
        "JUDGE_MODEL_FAMILY": "google",
        "JUDGE_MODEL_PRICE_IN": "3.50",
        "JUDGE_MODEL_PRICE_OUT": "10.50",
        "CHEAP_MODEL_ID": "nvidia/nemotron-4-mini-vl-8b",
        "CHEAP_MODEL_PROVIDER": "nvidia",
        "CHEAP_MODEL_FAMILY": "nvidia",
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
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)  # We'll set it explicitly
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")  # pragma: allowlist secret

    reset_settings()
    # Use _env_file=None to ignore .env file
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # The key is a SecretStr; compare via get_secret_value().
    key = settings.api_key.get_secret_value()
    assert key == "nvapi-test-key"  # pragma: allowlist secret
    assert settings.pipeline_model.model_id == "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    assert settings.pipeline_model.family == "nvidia"
    assert settings.pipeline_model.price_in_per_1m == 0.15
    assert settings.judge_model.family == "google"
    assert settings.cheap_model.family == "nvidia"


def test_judge_family_separation_fails(monkeypatch):
    """Judge model from same family as pipeline should fail."""
    env = _make_env(
        {
            "JUDGE_MODEL_ID": "nvidia/nemotron-4-mini-vl-8b",
            "JUDGE_MODEL_PROVIDER": "nvidia",
            "JUDGE_MODEL_FAMILY": "nvidia",
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
            "JUDGE_MODEL_ID": "nvidia/llama-3.3-nemotron-super-49b-v1.5",  # Duplicate!
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
    del env["NVIDIA_API_KEY"]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    reset_settings()
    with pytest.raises(ValueError, match=r"NVIDIA_API_KEY must be set"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_empty_api_key_fails(monkeypatch):
    """Empty API key should fail."""
    env = _make_env({"NVIDIA_API_KEY": ""})
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    with pytest.raises(ValueError, match=r"NVIDIA_API_KEY must be set"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_invalid_api_key_format_fails(monkeypatch):
    """Invalid API key format should fail at startup validation."""
    env = _make_env({"NVIDIA_API_KEY": "invalid-key"})
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    Settings(_env_file=None)  # type: ignore[call-arg]  # Load succeeds
    with pytest.raises(ValueError, match=r"does not look like a valid NVIDIA NIM key"):
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


def test_otel_settings_come_from_flat_env_vars(monkeypatch):
    """Spec 01 documents `OTEL_EXPORTER=...` and friends.

    pydantic-settings fills a nested model only from a *nested* env shape
    (`OTEL__EXPORTER`) and drops env vars that match no field, so before these
    flat bindings existed every `OTEL_*` variable was silently ignored and the
    whole OTel feature was unreachable by configuration.
    """
    env = _make_env(
        {
            "OTEL_EXPORTER": "console",
            "OTEL_ENDPOINT": "http://collector:4318/v1/traces",
            "OTEL_SERVICE_NAME": "my-service",
            "OTEL_SAMPLING_RATE": "0.25",
        }
    )
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.otel.exporter == "console"
    assert settings.otel.endpoint == "http://collector:4318/v1/traces"
    assert settings.otel.service_name == "my-service"
    assert settings.otel.sampling_rate == 0.25


def test_otel_settings_also_come_from_nested_env_vars(monkeypatch):
    """`OTEL__EXPORTER` is the spelling pydantic-settings parses natively.

    It used to be silently ignored: without `env_nested_delimiter="__"` the
    nested shape never became the `otel` dict, so `build_otel_settings` filled
    `otel` from the (empty) flat fields and the nested values vanished. Both
    spellings must now work.
    """
    env = _make_env(
        {
            "OTEL__EXPORTER": "otlp",
            "OTEL__SERVICE_NAME": "nested-service",
        }
    )
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.otel.exporter == "otlp"
    assert settings.otel.service_name == "nested-service"


def test_nested_otel_env_wins_over_flat(monkeypatch):
    """Precedence is deterministic: the explicit nested model is used as-is."""
    env = _make_env({"OTEL_EXPORTER": "console", "OTEL__EXPORTER": "otlp"})
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.otel.exporter == "otlp"


def test_otel_defaults_export_nothing(monkeypatch):
    for k, v in _make_env().items():
        monkeypatch.setenv(k, v)

    reset_settings()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.otel.exporter == "none"
    assert settings.otel.service_name == "failure-forensics"
    assert settings.otel.sampling_rate == 1.0


def test_otel_rejects_an_unknown_exporter(monkeypatch):
    env = _make_env({"OTEL_EXPORTER": "jaeger"})
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_otel_rejects_an_out_of_range_sampling_rate(monkeypatch):
    env = _make_env({"OTEL_SAMPLING_RATE": "1.5"})
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_every_documented_feature_env_var_binds_to_a_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`.env.example` and `render.yaml` advertise these names; they must bind.

    `SPEND_FILE` did not: the spend-ledger path was hardcoded, so a deployment
    that put its database on a persistent disk still reset the global spend cap
    on every redeploy.
    """
    env = _make_env(
        {
            "SPEND_FILE": str(tmp_path / "spend.json"),
            "AUTH_ENABLED": "true",
            "AUTH_API_KEY_PREFIX": NON_DEFAULT_PREFIX,
            "AUTH_BCRYPT_ROUNDS": "5",
            "CHECK_PLUGINS": "tests.plugin_samples:no_shouting",
            "JUDGE_ENABLED": "true",
            "JUDGE_FORCE": "true",
            "PII_REDACTION_ENABLED": "true",
            "PII_ENTITY_TYPES": "email,phone",
            "PII_REDACTION_CHAR": "#",
            "EVAL_SCHEDULE": "0 2 * * *",
            "EVAL_SCHEDULE_TIMEZONE": "Europe/Berlin",
            "SCHEDULER_DB_PATH": str(tmp_path / "scheduler.db"),
            "ALERT_WEBHOOK_URL": "https://hooks.example.test/alerts",
            "ALERT_WEBHOOK_SECRET": "shared-secret",  # pragma: allowlist secret
            "ALERT_REGRESSION_THRESHOLD": "0.25",
            "ALERT_ON_FIXED": "false",
            "QUEUE_ENABLED": "true",
            "QUEUE_WORKER_CONCURRENCY": "3",
            "QUEUE_POLL_INTERVAL": "0.5",
            "QUEUE_JOB_TTL": "60",
        }
    )
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.spend_file == tmp_path / "spend.json"
    assert settings.auth_enabled is True
    assert settings.auth_api_key_prefix == NON_DEFAULT_PREFIX
    assert settings.auth_bcrypt_rounds == 5
    assert settings.check_plugins == "tests.plugin_samples:no_shouting"
    assert settings.judge_enabled is True
    assert settings.judge_force is True
    assert settings.pii_redaction_enabled is True
    assert settings.pii_entity_types == "email,phone"
    assert settings.pii_redaction_char == "#"
    assert settings.eval_schedule == "0 2 * * *"
    assert settings.eval_schedule_timezone == "Europe/Berlin"
    assert settings.scheduler_db_path == tmp_path / "scheduler.db"
    assert settings.alert_webhook_url == "https://hooks.example.test/alerts"
    assert settings.alert_webhook_secret is not None
    assert settings.alert_webhook_secret.get_secret_value() == "shared-secret"
    assert settings.alert_regression_threshold == 0.25
    assert settings.alert_on_fixed is False
    assert settings.queue_enabled is True
    assert settings.queue_worker_concurrency == 3
    assert settings.queue_poll_interval == 0.5
    assert settings.queue_job_ttl == 60


def test_blank_alert_secret_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ALERT_WEBHOOK_SECRET=` must not sign alerts with an empty key."""
    env = _make_env({"ALERT_WEBHOOK_SECRET": "", "ALERT_WEBHOOK_URL": ""})
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.alert_webhook_secret is None
    assert settings.alert_webhook_url is None


# ─── Per-model output budget and reasoning flag ─────────────────────────────
#
# Both env vars were documented in `.env.example` and read by nothing: the
# `MAX_TOKENS` value was dropped before the model builder ran (no field bound
# it), and `IS_REASONING` was never read anywhere at all.


def test_model_max_tokens_binds_for_every_role(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _make_env(
        {
            "PIPELINE_MODEL_MAX_TOKENS": "8192",
            "PIPELINE_FALLBACK_MODEL_MAX_TOKENS": "1024",
            "JUDGE_MODEL_MAX_TOKENS": "2048",
            "CHEAP_MODEL_MAX_TOKENS": "512",
        }
    )
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.pipeline_model.max_tokens == 8192
    assert settings.pipeline_fallback_model.max_tokens == 1024
    assert settings.judge_model.max_tokens == 2048
    assert settings.cheap_model.max_tokens == 512


def test_model_is_reasoning_binds_and_reaches_the_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _make_env(
        {
            "PIPELINE_MODEL_IS_REASONING": "true",
            "PIPELINE_FALLBACK_MODEL_IS_REASONING": "false",
            "CHEAP_MODEL_IS_REASONING": "true",
        }
    )
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_settings()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.pipeline_model.is_reasoning is True
    assert settings.pipeline_fallback_model.is_reasoning is False
    assert settings.cheap_model.is_reasoning is True
    assert settings.judge_model.is_reasoning is False  # undocumented default


def test_model_knob_defaults_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A config that never sets the knobs keeps the historical 4096/False."""
    for k, v in _make_env().items():
        monkeypatch.setenv(k, v)

    reset_settings()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.pipeline_model.max_tokens == 4096
    assert settings.pipeline_model.is_reasoning is False
    assert settings.judge_model.max_tokens == 4096
    assert settings.judge_model.is_reasoning is False
