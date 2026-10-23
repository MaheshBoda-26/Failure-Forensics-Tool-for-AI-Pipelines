"""The committed real-model traces must seed a fresh instance without a key.

`forensics seed` is what makes a fresh deployment show real traces instead of
only the DRY_RUN fixture, so the fixture files themselves are part of the
contract: if a committed trace no longer parses against the current models, or
its document is missing, this test fails rather than the deploy booting empty.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from forensics.cli import app
from forensics.storage import Storage


def _fixture_count() -> int:
    from forensics.config import get_settings

    fixture_dir: Path = get_settings().dataset_dir / "live" / "traces"
    return len(list(fixture_dir.glob("*.json")))


def test_seed_loads_committed_live_traces_with_diagnoses() -> None:
    expected = _fixture_count()
    assert expected >= 1, "dataset/live/traces must ship at least one real trace"

    result = CliRunner().invoke(app, ["seed"])

    assert result.exit_code == 0, result.output
    storage = Storage()
    traces = storage.list_traces(limit=100)
    assert len(traces) == expected
    assert all(trace.spans for trace in traces), "a seeded trace has no spans"
    for trace in traces:
        assert storage.get_diagnosis_for_trace(trace.trace_id) is not None


def test_seed_is_idempotent_on_every_boot() -> None:
    runner = CliRunner()

    first = runner.invoke(app, ["seed"])
    second = runner.invoke(app, ["seed"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "0 loaded" in second.output
    assert len(Storage().list_traces(limit=100)) == _fixture_count()
