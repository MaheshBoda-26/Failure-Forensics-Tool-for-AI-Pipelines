"""The synthetic corpus generator must be reproducible and clock-free.

`generate_dataset` used `datetime.now()` for report bodies, so two runs with
the same seed differed. That made `dataset_large/` un-regenerable and let the
committed corpus drift away from the generator that claims to produce it. These
tests pin both properties.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from forensics import generate_dataset


def _generate(target: Path, seed: int) -> dict[str, bytes]:
    generate_dataset.generate_dataset(target, docs_per_type=3, failure_rate=0.4, seed=seed)
    return {
        str(path.relative_to(target)): path.read_bytes()
        for path in sorted(target.rglob("*"))
        if path.is_file()
    }


def test_same_seed_produces_byte_identical_output(tmp_path: Path) -> None:
    first = _generate(tmp_path / "a", seed=12345)
    second = _generate(tmp_path / "b", seed=12345)

    assert first.keys() == second.keys()
    differing = [name for name, blob in first.items() if second[name] != blob]
    assert differing == [], f"same seed produced different bytes for: {differing}"


def test_different_seeds_produce_different_documents(tmp_path: Path) -> None:
    first = _generate(tmp_path / "a", seed=1)
    second = _generate(tmp_path / "b", seed=2)

    documents = [name for name in first if name.startswith("documents/")]
    assert documents, "generator produced no documents"
    assert any(first[name] != second[name] for name in documents), (
        "two different seeds produced identical documents; the seed is not reaching the RNG"
    )


def test_generator_never_reads_the_wall_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clock read anywhere in the generator breaks reproducibility."""

    class _NoClock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            raise AssertionError("generate_dataset read the wall clock via datetime.now()")

        @classmethod
        def utcnow(cls) -> datetime:  # type: ignore[override]
            raise AssertionError("generate_dataset read the wall clock via datetime.utcnow()")

    monkeypatch.setattr(generate_dataset, "datetime", _NoClock)

    _generate(tmp_path / "a", seed=7)

    assert (tmp_path / "a" / "labels.json").exists()


def test_generated_documents_have_no_trailing_whitespace(tmp_path: Path) -> None:
    """The repo's pre-commit hook strips trailing whitespace.

    If the generator emitted it, committing the corpus would rewrite it and the
    checked-in files would stop matching generator output — the drift the seed
    guarantee is supposed to rule out.
    """
    generate_dataset.generate_dataset(tmp_path, docs_per_type=2, failure_rate=0.5, seed=3)

    offenders = []
    for path in (tmp_path / "documents").glob("*.txt"):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line != line.rstrip():
                offenders.append(f"{path.name}:{number}")
    assert offenders == [], f"trailing whitespace in generated documents: {offenders}"


def test_generated_files_pass_the_repo_hooks(tmp_path: Path) -> None:
    """Files the generator writes must survive pre-commit unchanged.

    `end-of-file-fixer` adds a trailing newline and `trailing-whitespace`
    strips indentation leftovers; either one rewriting the committed corpus
    would make the checked-in files stop matching generator output.
    """
    generate_dataset.generate_dataset(tmp_path, docs_per_type=2, failure_rate=0.5, seed=4)

    labels = (tmp_path / "labels.json").read_text()
    assert labels.endswith("\n"), "labels.json must end with a newline"

    for path in (tmp_path / "documents").glob("*.txt"):
        content = path.read_text()
        assert content.endswith("\n"), f"{path.name} must end with a newline"
        assert content == content.rstrip("\n") + "\n", f"{path.name} must not end with blank lines"
