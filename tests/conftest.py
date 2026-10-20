"""Shared pytest fixtures.

Every test runs against a temporary data directory. Several production helpers
default to CWD-relative paths (`data/traces`, `data/forensics.db`); without this
fixture a bare `finalize_trace(trace)` in a test wrote real files into the
repository's working tree, so tests were non-hermetic, order-dependent, and their
artifacts indistinguishable from real demo output.

A clean checkout has no `.env` (it is gitignored), so the fixture also seeds a
complete, obviously-fake model configuration. Only the API key used to be
required, which made `pytest` on a fresh clone fail across every module that
constructs `Settings` with `ValidationError: pipeline_model.model_id is empty`.

The working directory is deliberately NOT changed: `.env` and the dataset are
resolved relative to it. Only the output paths and the model/credential
configuration are redirected, via the env vars pydantic-settings maps onto
`Settings`.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

#: Everything a `Settings` instance needs, with unmistakably synthetic values.
#: Distinct ids and families keep the duplicate/family-separation guards quiet.
_TEST_MODEL_ENV = {
    "NVIDIA_API_KEY": "nvapi-test-key",  # pragma: allowlist secret
    "NVIDIA_BASE_URL": "https://integrate.api.nvidia.com/v1",
    "PIPELINE_MODEL_ID": "test/pipeline-model",
    "PIPELINE_MODEL_PROVIDER": "test",
    "PIPELINE_MODEL_FAMILY": "test-pipeline",
    "PIPELINE_FALLBACK_MODEL_ID": "test/fallback-model",
    "PIPELINE_FALLBACK_MODEL_PROVIDER": "test",
    "PIPELINE_FALLBACK_MODEL_FAMILY": "test-fallback",
    "JUDGE_MODEL_ID": "test/judge-model",
    "JUDGE_MODEL_PROVIDER": "test",
    "JUDGE_MODEL_FAMILY": "test-judge",
    "CHEAP_MODEL_ID": "test/cheap-model",
    "CHEAP_MODEL_PROVIDER": "test",
    "CHEAP_MODEL_FAMILY": "test-cheap",
}


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Redirect the default data paths at every relative `data/` reference."""
    data_dir = tmp_path / "data"

    monkeypatch.setenv("TRACES_DIR", str(data_dir / "traces"))
    monkeypatch.setenv("DB_PATH", str(data_dir / "forensics.db"))
    monkeypatch.setenv("CACHE_DIR", str(data_dir / "cache"))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    # Both ledgers are process-global by default: without these a test wrote
    # spend into the real `data/spend.json` and the APScheduler job store into
    # `data/scheduler.db`, so the numbers and the schedule leaked between runs.
    monkeypatch.setenv("SPEND_FILE", str(data_dir / "spend.json"))
    monkeypatch.setenv("SCHEDULER_DB_PATH", str(data_dir / "scheduler.db"))

    for key, value in _TEST_MODEL_ENV.items():
        monkeypatch.setenv(key, value)

    # Settings is a cached singleton; drop any instance built before this ran.
    from forensics.config import reset_settings

    reset_settings()
    yield tmp_path
    reset_settings()
