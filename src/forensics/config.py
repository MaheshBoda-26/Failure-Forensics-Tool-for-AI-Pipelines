"""Settings loaded from env, plus startup validation.

Loads model ids, prices, paths, and caps from `.env` / environment. Must fail
fast at startup on an unknown model slug, an unset API key, or a judge model
that shares a provider family with the pipeline models.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseModel):
    """Configuration for a single model."""

    model_id: str = Field(..., description="OpenRouter model slug (e.g., 'openai/gpt-4o-mini')")
    provider: str = Field(..., description="Pinned provider routing (e.g., 'openai')")
    family: str = Field(
        ...,
        description="Provider family for bias separation (e.g., 'openai', 'anthropic', 'google')",
    )
    price_in_per_1m: float = Field(..., ge=0, description="Input price per 1M tokens (USD)")
    price_out_per_1m: float = Field(..., ge=0, description="Output price per 1M tokens (USD)")
    supports_structured: bool = Field(
        default=True, description="Whether model supports native structured output"
    )
    max_tokens: int = Field(default=4096, gt=0, description="Max output tokens")


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Secrets ---
    # SecretStr so the key is masked in repr()/logs/tracebacks instead of being
    # printed in plaintext by any accidental repr of Settings.
    openrouter_api_key: SecretStr = Field(..., description="OpenRouter API key")

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
    dataset_dir: Path = Field(default=Path("dataset"))
    cache_dir: Path = Field(default=Path("data/cache"))

    # --- Server ---
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000, ge=1, le=65535)
    static_dir: Path = Field(default=Path("frontend/dist"))

    # --- Versions ---
    pipeline_version: str = Field(default="0.1.0")
    schema_version: int = Field(default=1)
    analyzer_version: str = Field(default="0.1.0")

    # --- Computed model configs (built in validator) ---
    pipeline_model: ModelConfig = Field(default=None, exclude=True)  # type: ignore[assignment]
    pipeline_fallback_model: ModelConfig = Field(default=None, exclude=True)  # type: ignore[assignment]
    judge_model: ModelConfig = Field(default=None, exclude=True)  # type: ignore[assignment]
    cheap_model: ModelConfig = Field(default=None, exclude=True)  # type: ignore[assignment]

    @field_validator("openrouter_api_key")
    @classmethod
    def validate_api_key(cls, v: SecretStr) -> SecretStr:
        secret = v.get_secret_value() if isinstance(v, SecretStr) else str(v)
        if not secret or secret.strip() == "":
            raise ValueError("OPENROUTER_API_KEY must be set and non-empty")
        return SecretStr(secret.strip())

    @field_validator("traces_dir", "cache_dir", mode="before")
    @classmethod
    def ensure_dirs(cls, v: str | Path) -> Path:
        path = Path(v)
        path.mkdir(parents=True, exist_ok=True)
        return path

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
    def validate_judge_family_separation(self) -> "Settings":
        """Judge model must come from a different provider family than pipeline models."""
        pipeline_families = {self.pipeline_model.family, self.pipeline_fallback_model.family}
        if self.judge_model.family in pipeline_families:
            raise ValueError(
                f"Judge model family '{self.judge_model.family}' matches pipeline family. "
                f"Pipeline families: {pipeline_families}. "
                f"Use a different provider family for the judge to avoid self-preference bias."
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
            raise ValueError(f"Duplicate model IDs detected: {duplicates}")
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
        _settings = Settings()  # type: ignore[call-arg]
    return _settings


def reset_settings() -> None:
    """Reset the singleton (for testing)."""
    global _settings
    _settings = None


def validate_startup() -> None:
    """Run all startup validations. Raises on failure."""
    settings = get_settings()

    # Validate API key format (basic check)
    key = settings.openrouter_api_key
    key_str = key.get_secret_value() if isinstance(key, SecretStr) else str(key)
    if not key_str.startswith(("sk-or-", "sk-")):
        raise ValueError(
            "OPENROUTER_API_KEY does not look like a valid OpenRouter key "
            "(expected 'sk-or-' or 'sk-' prefix)"
        )

    # Ensure directories exist
    settings.traces_dir.mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.dataset_dir.mkdir(parents=True, exist_ok=True)


__all__ = ["ModelConfig", "Settings", "get_settings", "reset_settings", "validate_startup"]
