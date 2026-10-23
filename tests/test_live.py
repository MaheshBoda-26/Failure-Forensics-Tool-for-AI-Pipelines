"""Tests that make real NVIDIA NIM calls.

These are the only tests allowed to touch the network. They are marked `live`
so `make test` (``pytest -m "not live"``) never spends quota; run them with
``make test-live``.

The suite is skipped, not failed, when no real key is configured: `.env` is
gitignored, so a fresh clone legitimately has none. A key is considered real
only when it is not the synthetic `nvapi-test-key` that `conftest.py` seeds for
the hermetic suite.

What this proves that the mocked tests cannot:
- the configured model slug still exists on NIM and accepts our request shape;
- native structured output parses for the real reply (not just a fixture);
- the extraction values are actually grounded in the real document, so the
  analyzer's clean-baseline verdict is reachable with a real model;
- the trace records non-zero latency, i.e. it is not the DRY_RUN fixture.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from forensics.analyzer_simple import analyze_trace
from forensics.config import get_settings, reset_settings
from forensics.llm import QuotaExhaustedError, RateLimitError, using_llm_client
from forensics.models import Document, DocumentType, Trace
from forensics.pipeline import Pipeline
from forensics.tracing import trace_context

pytestmark = pytest.mark.live

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

#: Paths the hermetic suite redirects to `tmp_path`. Applying `.env` over them
#: would write real traces into the repository from a test run.
_ISOLATED_SETTINGS = frozenset(
    {
        "DATA_DIR",
        "TRACES_DIR",
        "DB_PATH",
        "CACHE_DIR",
        "SPEND_FILE",
        "SCHEDULER_DB_PATH",
    }
)

#: The obviously-fake key `tests/conftest.py` installs for offline tests.
_FAKE_KEY_PREFIX = "nvapi-test"


def _dotenv() -> dict[str, str]:
    """Read `.env` without adding a dependency, ignoring comments and blanks."""
    if not _ENV_PATH.exists():
        return {}
    values: dict[str, str] = {}
    for line in _ENV_PATH.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip()
    return values


def _real_api_key(values: dict[str, str]) -> str | None:
    """The real key from the environment or `.env`, never the synthetic one."""
    for candidate in (os.environ.get("FORENSICS_LIVE_API_KEY"), values.get("NVIDIA_API_KEY")):
        if candidate and not candidate.startswith(_FAKE_KEY_PREFIX):
            return candidate
    return None


@pytest.fixture(autouse=True)
def real_nim_configuration(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace conftest's synthetic model configuration with the real one.

    `conftest.py` deliberately overrides every model id and the key (hermetic
    suite). A live test is the one place that must undo that, while keeping the
    temporary data paths it established.
    """
    values = _dotenv()
    key = _real_api_key(values)
    if key is None:
        pytest.skip("no real NVIDIA_API_KEY configured (set one in .env or FORENSICS_LIVE_API_KEY)")

    for name, value in values.items():
        if name in _ISOLATED_SETTINGS:
            continue
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("NVIDIA_API_KEY", key)
    monkeypatch.setenv("DRY_RUN", "false")

    reset_settings()
    settings = get_settings()
    if settings.dry_run:
        pytest.skip("DRY_RUN=true in .env; live tests require a real model call")

    yield
    reset_settings()


@pytest.mark.asyncio
async def test_live_pipeline_run_is_grounded_and_diagnosable() -> None:
    """One document, end to end, against the real configured model."""
    settings = get_settings()
    doc_path = settings.dataset_dir / "documents" / "invoice_001.txt"
    if not doc_path.exists():
        pytest.skip(f"dataset document missing: {doc_path}")

    document = Document(content=doc_path.read_text())

    try:
        async with using_llm_client() as client:
            pipeline = Pipeline(client=client)
            with trace_context(document_id="invoice_001") as ctx:
                result = await pipeline.run(document, document_id="invoice_001")
    except (QuotaExhaustedError, RateLimitError) as exc:
        # The hosted quota is shared and environmental; it says nothing about
        # the code. Report it as a skip so a rate-limited run cannot look like
        # a product pass or a product failure.
        pytest.skip(f"NVIDIA NIM quota unavailable for the live smoke test: {exc}")

    # The configuration says which model; the reply must come from one.
    assert result.classification.document_type is DocumentType.INVOICE
    assert result.extraction.entities, "the real model extracted no entities"
    assert result.summarization.summary.strip()

    # Grounding is what `entity_grounding` checks; a real model that invents
    # values would make the clean baseline unreachable.
    grounded = [entity.value for entity in result.extraction.entities]
    assert any(value in document.content for value in grounded)

    trace: Trace | None = ctx.trace
    assert trace is not None
    assert sum(span.latency_ms for span in trace.spans) > 0, "a zero-latency trace is DRY_RUN"

    diagnosis = analyze_trace(trace, document.content, expected_type="invoice")
    assert diagnosis.root_cause_step == "none", diagnosis.evidence_chain
