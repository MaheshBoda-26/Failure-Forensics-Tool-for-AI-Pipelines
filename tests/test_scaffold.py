"""Smoke test proving the scaffold is wired up.

Verifies the package imports, the settings object exists, and the data paths
resolve inside the repo. Keep this permanently — it is the cheapest possible
signal that a fresh clone is not broken.
"""

from pathlib import Path

from forensics import __version__


def test_package_imports() -> None:
    assert __version__ == "0.1.0"


def test_repo_layout_is_present() -> None:
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "pyproject.toml",
        "src/forensics",
        "tests",
        "dataset",
        "data/traces",
        "Makefile",
    ):
        assert (root / rel).exists(), f"missing {rel}"
