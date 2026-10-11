"""Shared pytest fixtures.

Every test runs against a temporary data directory. Several production helpers
default to CWD-relative paths (`data/traces`, `data/forensics.db`); without this
fixture a bare `finalize_trace(trace)` in a test wrote real files into the
repository's working tree, so tests were non-hermetic, order-dependent, and their
artifacts indistinguishable from real demo output.

The working directory is deliberately NOT changed: `.env` and the dataset are
resolved relative to it. Only the output paths are redirected, via the env vars
pydantic-settings maps onto `Settings`.
"""

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect the default data paths at every relative `data/` reference."""
    tmp = Path(tempfile.mkdtemp(prefix="forensics-test-"))

    monkeypatch.setenv("TRACES_DIR", str(tmp / "data" / "traces"))
    monkeypatch.setenv("DB_PATH", str(tmp / "data" / "forensics.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp / "data" / "cache"))
    monkeypatch.setenv("DATA_DIR", str(tmp / "data"))

    # Settings is a cached singleton; drop any instance built before this ran.
    from forensics.config import reset_settings

    reset_settings()
    yield tmp
    reset_settings()
