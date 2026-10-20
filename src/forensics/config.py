"""Settings loaded from env, plus startup validation.

Loads model ids, prices, paths, and caps from `.env` / environment. Must fail
fast at startup on an unknown model slug, an unset API key, or a judge model
that shares a provider family with the pipeline models.
"""

import ipaddress
import warnings
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseModel):
    """Configuration for a single model."""

    model_id: str = Field(..., description="NIM model slug (e.g., 'nvidia/nemotron-4-mini-vl-8b')")
    provider: str = Field(
        ...,
        description="Model author/vendor prefix of the slug (e.g. 'nvidia', 'meta', 'google')",
    )
    family: str = Field(
        ...,
        description="Model family for bias separation (e.g., 'nvidia', 'meta', 'google')",
    )
    is_reasoning: bool = Field(
        default=False,
        description=(
            "Whether this model uses hidden reasoning tokens that consume the same "
            "output budget. Reasoning models (e.g., nemotron-3-super, o1, o3) often "
            "fail native structured output because they spend the entire budget on "
            "hidden reasoning. When true, the client skips Tier 1 native structured "
            "output and goes straight to Tier 2 (schema in prompt)."
        ),
    )
    price_in_per_1m: float = Field(..., ge=0, description="Input price per 1M tokens (USD)")
    price_out_per_1m: float = Field(..., ge=0, description="Output price per 1M tokens (USD)")
    supports_structured: bool = Field(
        default=True, description="Whether model supports native structured output"
    )
    max_tokens: int = Field(default=4096, gt=0, description="Max output tokens")


class OTelSettings(BaseModel):
    """OpenTelemetry export configuration."""

    exporter: Literal["none", "otlp", "console"] = Field(
        default="none", description="Exporter type: none, otlp (OTLP/HTTP), or console"
    )
    endpoint: str = Field(
        default="http://localhost:4318/v1/traces", description="OTLP/HTTP endpoint"
    )
    service_name: str = Field(default="failure-forensics", description="OTel service name")
    sampling_rate: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Head-based sampling rate"
    )
    max_prompt_chars: int = Field(
        default=4000,
        gt=0,
        description=(
            "Prompt characters exported per span. Truncation is a privacy control: "
            "exported spans leave the process and reach a third-party collector, "
            "while the local trace files are what PII redaction covers. Mirrors "
            "`otel_export.DEFAULT_MAX_PROMPT_CHARS`."
        ),
    )


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Without this, `OTEL__EXPORTER` is not parsed as the nested `otel` model
        # at all: the flat `OTEL_EXPORTER` fields below exist to bind the names
        # spec 01 documents, and `build_otel_settings` assembles `otel` from
        # them. Declaring the delimiter makes the nested spelling work too,
        # instead of being silently ignored.
        env_nested_delimiter="__",
        extra="ignore",
    )

    # --- Secrets ---
    # SecretStr so the key is masked in repr()/logs/tracebacks instead of being
    # printed in plaintext by any accidental repr of Settings.
    api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="NVIDIA_API_KEY",
        description="NVIDIA NIM API key (sent as a bearer token)",
    )
    api_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        validation_alias="NVIDIA_BASE_URL",
        description="OpenAI-compatible NIM endpoint base URL",
    )

    # --- Models (flat env vars, assembled in validator) ---
    pipeline_model_id: str = Field(default="")
    pipeline_model_provider: str = Field(default="")
    pipeline_model_family: str = Field(default="")
    pipeline_model_price_in: float = Field(default=0.0)
    pipeline_model_price_out: float = Field(default=0.0)

    pipeline_fallback_model_id: str = Field(default="")
    pipeline_fallback_model_provider: str = Field(default="")
    pipeline_fallback_model_family: str = Field(default="")
    pipeline_fallback_model_price_in: float = Field(default=0.0)
    pipeline_fallback_model_price_out: float = Field(default=0.0)

    judge_model_id: str = Field(default="")
    judge_model_provider: str = Field(default="")
    judge_model_family: str = Field(default="")

    # Per-model output budget and reasoning flag, declared as fields because
    # pydantic-settings only surfaces an env var to the `mode="before"`
    # validator when a field binds it — an undeclared `PIPELINE_MODEL_MAX_TOKENS`
    # was silently dropped, so `build_model` always saw the 4096 default and
    # `MODEL_IS_REASONING` was never read at all. `apply_model_knobs` (mode
    # "after") copies these onto the assembled `ModelConfig`s.
    pipeline_model_max_tokens: int = Field(default=4096, gt=0, exclude=True)
    pipeline_model_is_reasoning: bool = Field(default=False, exclude=True)
    pipeline_fallback_model_max_tokens: int = Field(default=4096, gt=0, exclude=True)
    pipeline_fallback_model_is_reasoning: bool = Field(default=False, exclude=True)
    judge_model_max_tokens: int = Field(default=4096, gt=0, exclude=True)
    judge_model_is_reasoning: bool = Field(default=False, exclude=True)
    cheap_model_max_tokens: int = Field(default=4096, gt=0, exclude=True)
    cheap_model_is_reasoning: bool = Field(default=False, exclude=True)

    request_timeout_s: float = Field(
        default=60.0,
        gt=0,
        description=(
            "Per-request HTTP timeout. Reasoning models under a JSON schema can take "
            "70-110s on a single document; at the old fixed 60s every attempt timed "
            "out, then retried three times across three tiers."
        ),
    )
    connect_timeout_s: float = Field(default=10.0, gt=0, description="Connect timeout")
    judge_family_separation_required: bool = Field(
        default=True,
        description="Fail startup when the judge shares a family with the pipeline models",
    )
    judge_model_price_in: float = Field(default=0.0)
    judge_model_price_out: float = Field(default=0.0)

    cheap_model_id: str = Field(default="")
    cheap_model_provider: str = Field(default="")
    cheap_model_family: str = Field(default="")
    cheap_model_price_in: float = Field(default=0.0)
    cheap_model_price_out: float = Field(default=0.0)

    # --- Cost control ---
    spend_cap_per_run_usd: float = Field(
        default=0.50, gt=0, description="Max spend per pipeline run (USD)"
    )
    spend_cap_global_usd: float = Field(
        default=20.00, gt=0, description="Max cumulative spend (USD)"
    )
    cache_enabled: bool = Field(default=True, description="Enable response cache")
    dry_run: bool = Field(default=False, description="Dry-run mode (no network calls)")

    # --- Paths ---
    # Read from env so a test (or a deployment) can redirect every output
    # location without touching the code that consumes it.
    data_dir: Path = Field(default=Path("data"))
    traces_dir: Path = Field(default=Path("data/traces"))
    db_path: Path = Field(default=Path("data/forensics.db"))
    spend_file: Path = Field(
        default=Path("data/spend.json"),
        description=(
            "Spend ledger. Env-configurable because a deployment that keeps its "
            "database on a persistent disk but its ledger in the build directory "
            "resets the global spend cap on every deploy."
        ),
    )
    dataset_dir: Path = Field(default=Path("dataset"))
    cache_dir: Path = Field(default=Path("data/cache"))

    # --- Server ---
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000, ge=1, le=65535)
    static_dir: Path = Field(default=Path("frontend/dist"))
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:8000",
        description=(
            "Comma-separated browser origins allowed to call the API. An explicit "
            "list — never '*' — because the CORS middleware enables credentials, "
            "and `*` plus credentials would let any page an operator visits drive "
            "the API with their key."
        ),
    )

    # --- OpenTelemetry ---
    # The flat fields exist only to bind the env names spec 01 documents
    # (`OTEL_EXPORTER`, ...). pydantic-settings fills a nested model only from a
    # *nested* env shape (`OTEL__EXPORTER`) and silently drops env vars that match
    # no known field, so without these the whole OTel feature was unreachable by
    # configuration. `build_otel_settings` assembles `otel` from them.
    otel_exporter: Literal["none", "otlp", "console"] = Field(
        default="none", validation_alias="OTEL_EXPORTER"
    )
    otel_endpoint: str = Field(
        default="http://localhost:4318/v1/traces", validation_alias="OTEL_ENDPOINT"
    )
    otel_service_name: str = Field(
        default="failure-forensics", validation_alias="OTEL_SERVICE_NAME"
    )
    otel_sampling_rate: float = Field(
        default=1.0, ge=0.0, le=1.0, validation_alias="OTEL_SAMPLING_RATE"
    )
    otel_max_prompt_chars: int = Field(default=4000, gt=0, validation_alias="OTEL_MAX_PROMPT_CHARS")
    otel: OTelSettings = Field(default_factory=OTelSettings)

    # --- PII Redaction ---
    pii_redaction_enabled: bool = Field(default=False, description="Enable PII redaction")
    pii_entity_types: str = Field(
        default="api_key,email,phone,credit_card,ssn,ipv4,jwt",
        description="Comma-separated entity types to redact",
    )
    pii_custom_patterns: str = Field(
        default="",
        description="JSON array of custom patterns: [{name, pattern}]",
    )
    pii_redaction_char: str = Field(default="█", description="Character used for redaction")

    # --- Judge LLM ---
    judge_enabled: bool = Field(default=False, description="Enable judge LLM for semantic analysis")
    judge_force: bool = Field(
        default=False, description="Force judge to run even if deterministic passes"
    )

    # --- Custom check plugins (spec 05) ---
    check_plugins: str = Field(
        default="",
        description=(
            "Comma-separated 'module:attr' import paths for custom deterministic "
            "checks, e.g. 'my_checks:check_pii_in_summary,other:MyCheck'"
        ),
    )

    # --- Auth / multi-tenancy (spec 04) ---
    auth_enabled: bool = Field(
        default=False,
        description="Require an API key on every data endpoint (AUTH_ENABLED)",
    )
    auth_api_key_prefix: str = Field(
        default="ffk_",
        description="Prefix for generated API keys, used to identify a key (AUTH_API_KEY_PREFIX)",
    )
    auth_bcrypt_rounds: int = Field(
        default=12,
        ge=4,
        le=16,
        description="bcrypt cost factor for API key hashing (AUTH_BCRYPT_ROUNDS)",
    )

    # --- Scheduled eval and alerting (spec 06) ---
    eval_schedule: str = Field(
        default="",
        description="Cron expression for the nightly eval; empty disables scheduling",
    )
    eval_schedule_timezone: str = Field(
        default="UTC", description="Timezone the cron expression is interpreted in"
    )
    scheduler_db_path: Path = Field(
        default=Path("data/scheduler.db"),
        description="SQLite file backing the APScheduler job store (survives restarts)",
    )
    alert_webhook_url: str | None = Field(
        default=None, description="POST target for regression/improvement alerts"
    )
    alert_webhook_secret: SecretStr | None = Field(
        default=None, description="Shared secret used to sign alert payloads with HMAC-SHA256"
    )
    alert_regression_threshold: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Accuracy drop (as a fraction) that counts as a regression",
    )
    alert_on_fixed: bool = Field(
        default=True, description="Also alert when a previously failing case now passes"
    )

    # --- Worker queue (spec 07) ---
    queue_enabled: bool = Field(
        default=False,
        description="Process pipeline runs on workers instead of inline (QUEUE_ENABLED)",
    )
    queue_worker_concurrency: int = Field(
        default=2, ge=1, le=32, description="Jobs a worker claims per poll"
    )
    queue_poll_interval: float = Field(
        default=1.0, gt=0.0, description="Seconds between polls when the queue is empty"
    )
    queue_job_ttl: int = Field(
        default=86400, ge=0, description="Seconds a finished job is retained before cleanup"
    )

    # --- Versions ---
    pipeline_version: str = Field(default="0.1.0")
    schema_version: int = Field(default=1)
    analyzer_version: str = Field(default="0.1.0")

    # --- Computed model configs (built in validator) ---
    pipeline_model: ModelConfig = Field(default=None, exclude=True)  # type: ignore[assignment]
    pipeline_fallback_model: ModelConfig = Field(default=None, exclude=True)  # type: ignore[assignment]
    judge_model: ModelConfig = Field(default=None, exclude=True)  # type: ignore[assignment]
    cheap_model: ModelConfig = Field(default=None, exclude=True)  # type: ignore[assignment]

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: SecretStr) -> SecretStr:
        secret = v.get_secret_value() if isinstance(v, SecretStr) else str(v)
        if not secret or secret.strip() == "":
            raise ValueError("NVIDIA_API_KEY must be set and non-empty")
        return SecretStr(secret.strip())

    @field_validator("traces_dir", "cache_dir", mode="before")
    @classmethod
    def ensure_dirs(cls, v: str | Path) -> Path:
        path = Path(v)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @model_validator(mode="before")
    @classmethod
    def allow_keyless_dry_run(cls, data: Any) -> Any:
        """Accept an unset key when `DRY_RUN=true`.

        `DRY_RUN` makes no network calls, so requiring a credential to construct
        `Settings` at all made the documented offline demo impossible: the
        process died in model validation before it could reach the code that
        honours the flag. A real run still fails fast without a key.
        """
        if not isinstance(data, dict):
            return data
        if not _is_truthy(data.get("dry_run", data.get("DRY_RUN"))):
            return data

        present = data.get("api_key", data.get("NVIDIA_API_KEY"))
        secret = present.get_secret_value() if isinstance(present, SecretStr) else present
        if present is None or not str(secret or "").strip():
            # Both spellings: the field is aliased to NVIDIA_API_KEY, and which
            # key pydantic-settings hands a `mode="before"` validator depends on
            # the source (env, dotenv, init kwargs).
            # Not a credential: an obvious placeholder, never sent anywhere.
            data["api_key"] = "dry-run-placeholder"  # pragma: allowlist secret
            data["NVIDIA_API_KEY"] = "dry-run-placeholder"  # pragma: allowlist secret
        return data

    @field_validator("alert_webhook_url", "alert_webhook_secret", mode="before")
    @classmethod
    def blank_means_unset(cls, v: Any) -> Any:
        """Treat an empty env value as "not configured".

        `ALERT_WEBHOOK_SECRET=` in a `.env` file yields an empty string, which is
        *falsy-looking but truthy as a `SecretStr`* — it would sign alerts with a
        known-empty key, i.e. no signature at all while looking signed.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="before")
    @classmethod
    def build_otel_settings(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Assemble `otel` from the flat `OTEL_*` fields above.

        Spec 01 documents flat names (`OTEL_EXPORTER`, ...); pydantic-settings
        would only fill a nested model from `OTEL__EXPORTER`, and it drops env
        vars matching no field, so the flat fields are the binding layer and
        `otel` is the single object everything downstream reads. Same shape as
        `build_model_configs` below.
        """

        def bound(alias: str) -> Any:
            # pydantic-settings keys env-sourced *aliased* values by the alias as
            # written (`OTEL_EXPORTER`). The lowercase field name is accepted too,
            # so this does not depend on that internal choice.
            return data.get(alias, data.get(alias.lower()))

        if not isinstance(data.get("otel"), dict):
            data["otel"] = {
                "exporter": bound("OTEL_EXPORTER") or "none",
                "endpoint": bound("OTEL_ENDPOINT") or "http://localhost:4318/v1/traces",
                "service_name": bound("OTEL_SERVICE_NAME") or "failure-forensics",
                "sampling_rate": bound("OTEL_SAMPLING_RATE") or 1.0,
                "max_prompt_chars": bound("OTEL_MAX_PROMPT_CHARS") or 4000,
            }
        return data

    @model_validator(mode="before")
    @classmethod
    def build_model_configs(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Build ModelConfig objects from flat env vars."""

        def build_model(prefix: str) -> ModelConfig:
            return ModelConfig(
                model_id=data.get(f"{prefix}_model_id", ""),
                provider=data.get(f"{prefix}_model_provider", ""),
                family=data.get(f"{prefix}_model_family", ""),
                price_in_per_1m=float(data.get(f"{prefix}_model_price_in", 0)),
                price_out_per_1m=float(data.get(f"{prefix}_model_price_out", 0)),
            )

        data["pipeline_model"] = build_model("pipeline")
        data["pipeline_fallback_model"] = build_model("pipeline_fallback")
        data["judge_model"] = build_model("judge")
        data["cheap_model"] = build_model("cheap")
        return data

    @model_validator(mode="after")
    def apply_model_knobs(self) -> "Settings":
        """Copy the per-model output budget and reasoning flag onto each config.

        Reasoning models (nemotron-3-super and friends) spend a large share of
        the budget on hidden reasoning tokens. At the 4096 default they finish
        with `finish_reason="length"` and an empty content string, which the
        structured-output tiers read as an unparseable reply. Applied here, from
        the parsed fields, rather than in the `mode="before"` builder, which
        cannot see an env var that no field binds.
        """
        for prefix, attr in (
            ("pipeline", "pipeline_model"),
            ("pipeline_fallback", "pipeline_fallback_model"),
            ("judge", "judge_model"),
            ("cheap", "cheap_model"),
        ):
            config: ModelConfig = getattr(self, attr)
            config.max_tokens = getattr(self, f"{prefix}_model_max_tokens")
            config.is_reasoning = getattr(self, f"{prefix}_model_is_reasoning")
        return self

    @model_validator(mode="after")
    def validate_judge_family_separation(self) -> "Settings":
        """Judge model must come from a different provider family than pipeline models.

        Set JUDGE_FAMILY_SEPARATION_REQUIRED=false to run a single model in
        every role. That is a deliberate trade: the judge then grades output
        from the same weights that produced it, which is exactly the
        self-preference bias this guard exists to prevent, so the verdict is
        weaker evidence than a cross-family judge's.
        """
        pipeline_families = {self.pipeline_model.family, self.pipeline_fallback_model.family}
        if self.judge_model.family not in pipeline_families:
            return self
        message = (
            f"Judge model family '{self.judge_model.family}' matches pipeline family. "
            f"Pipeline families: {pipeline_families}. "
            f"Use a different provider family for the judge to avoid self-preference bias."
        )
        if self.judge_family_separation_required:
            raise ValueError(message)
        warnings.warn(
            f"{message} Proceeding because JUDGE_FAMILY_SEPARATION_REQUIRED=false.",
            RuntimeWarning,
            stacklevel=2,
        )
        return self

    @model_validator(mode="after")
    def validate_model_ids_unique(self) -> "Settings":
        """Ensure we're not accidentally using the same model for everything."""
        ids = {
            "pipeline": self.pipeline_model.model_id,
            "pipeline_fallback": self.pipeline_fallback_model.model_id,
            "judge": self.judge_model.model_id,
            "cheap": self.cheap_model.model_id,
        }
        if len(set(ids.values())) < len(ids):
            duplicates = {k: v for k, v in ids.items() if list(ids.values()).count(v) > 1}
            message = f"Duplicate model IDs detected: {duplicates}"
            if self.judge_family_separation_required:
                raise ValueError(message)
            warnings.warn(
                f"{message} One model is serving every role, so the fallback path "
                f"retries the same weights. Proceeding because "
                f"JUDGE_FAMILY_SEPARATION_REQUIRED=false.",
                RuntimeWarning,
                stacklevel=2,
            )
        return self

    @model_validator(mode="after")
    def validate_model_configs_complete(self) -> "Settings":
        """Ensure all model configs have required fields."""
        for name, model in [
            ("pipeline", self.pipeline_model),
            ("pipeline_fallback", self.pipeline_fallback_model),
            ("judge", self.judge_model),
            ("cheap", self.cheap_model),
        ]:
            if not model.model_id:
                raise ValueError(f"{name}_model.model_id is empty (check {name.upper()}_MODEL_ID)")
            if not model.provider:
                raise ValueError(
                    f"{name}_model.provider is empty (check {name.upper()}_MODEL_PROVIDER)"
                )
            if not model.family:
                raise ValueError(
                    f"{name}_model.family is empty (check {name.upper()}_MODEL_FAMILY)"
                )
            if model.price_in_per_1m < 0 or model.price_out_per_1m < 0:
                raise ValueError(f"{name}_model prices must be non-negative")
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global settings instance (singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset the singleton (for testing)."""
    global _settings
    _settings = None


def _is_truthy(value: Any) -> bool:
    """Coerce an env-style flag ("true", "1", "yes", "on") to a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _validate_pii_custom_patterns(settings: Settings) -> None:
    """Fail startup on a malformed `PII_CUSTOM_PATTERNS` value.

    Redaction runs at the *end* of a pipeline run, so an invalid regex used to
    surface only after the LLM calls had already been paid for. Validating here
    turns it into an immediate, actionable startup error.
    """
    from forensics.pii import parse_custom_patterns

    parse_custom_patterns(settings.pii_custom_patterns)


def _validate_alert_webhook(settings: Settings) -> None:
    """Reject an alert webhook that could be used for SSRF.

    `ALERT_WEBHOOK_URL` is delivered by the server, not the browser, so pointing
    it at a link-local address reaches cloud metadata and at a loopback address
    reaches services bound only to the host. Plain HTTP is only acceptable in
    dry-run/development mode, where no real credential is sent.
    """
    url = settings.alert_webhook_url
    if not url:
        return

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("ALERT_WEBHOOK_URL must be an http(s) URL")
    if parsed.scheme == "http" and not settings.dry_run:
        raise ValueError("ALERT_WEBHOOK_URL must use https:// (http:// only with DRY_RUN=true)")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("ALERT_WEBHOOK_URL must include a host")
    if settings.dry_run:
        return
    if host in {"localhost", "metadata", "metadata.google.internal"}:
        raise ValueError(f"ALERT_WEBHOOK_URL host '{host}' is not allowed")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return  # a normal DNS name
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise ValueError(f"ALERT_WEBHOOK_URL must not target a private/loopback address ({host})")


def validate_startup() -> None:
    """Run all startup validations. Raises on failure."""
    settings = get_settings()

    # Validate API key format (basic check). Dry-run makes no network call, so a
    # key is neither required nor checked — that is what lets the offline demo
    # run on a clean checkout with no `.env`.
    key = settings.api_key
    key_str = key.get_secret_value() if isinstance(key, SecretStr) else str(key)
    if not settings.dry_run:
        if not key_str.startswith(("nvapi-", "sk-")):
            raise ValueError(
                "NVIDIA_API_KEY does not look like a valid NVIDIA NIM key "
                "(expected 'nvapi-' or 'sk-' prefix)"
            )
        if not settings.api_base_url.startswith("https://"):
            raise ValueError("NVIDIA_BASE_URL must be an https:// URL")

    _validate_pii_custom_patterns(settings)
    _validate_alert_webhook(settings)

    # Ensure directories exist
    settings.traces_dir.mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.dataset_dir.mkdir(parents=True, exist_ok=True)

    # Load custom check plugins (spec 05). A bad spec raises here, naming the
    # offending value, so the operator learns at startup instead of at analysis.
    from forensics.checks import load_plugins_from_config

    load_plugins_from_config(settings.check_plugins)


__all__ = [
    "ModelConfig",
    "OTelSettings",
    "Settings",
    "get_settings",
    "reset_settings",
    "validate_startup",
]
