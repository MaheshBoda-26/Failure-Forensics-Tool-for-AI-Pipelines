"""`DRY_RUN=true` must reproduce the five seeded failures in `dataset/labels.json`.

`docs/limitations.md` §7 and the README both make that promise, and the eval
loop depends on it: the fixture is the only offline ground truth the project
has. Before this revision the fixture keyed off keywords in the *prompt*, but
the classification prompt carries only extracted entities — no document text,
no filename — so every document fell through to `contract`. That hid the
seeded `contract_001` failure, invented twelve others, and made four of the five
seeded failures unreachable.

These tests assert the promise directly, per document, against `labels.json`.
"""

import json
from pathlib import Path
from typing import Any, cast

import pytest

from forensics.analyzer_simple import analyze_trace
from forensics.config import get_settings, reset_settings
from forensics.llm import LLMClient, NIMClient
from forensics.models import (
    ClassificationOutput,
    Document,
    DocumentType,
    ExtractionOutput,
    SummaryOutput,
)
from forensics.pipeline import Pipeline
from forensics.tracing import trace_context

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Enough of an environment for `Settings()` to build without reading `.env`.
_MODEL_ENV: dict[str, str] = {
    "NVIDIA_API_KEY": "nvapi-dry-run-fixture-key",  # pragma: allowlist secret
    "DRY_RUN": "true",
    "PIPELINE_MODEL_ID": "nvidia/pipeline",
    "PIPELINE_MODEL_PROVIDER": "nvidia",
    "PIPELINE_MODEL_FAMILY": "nvidia",
    "PIPELINE_FALLBACK_MODEL_ID": "meta/fallback",
    "PIPELINE_FALLBACK_MODEL_PROVIDER": "meta",
    "PIPELINE_FALLBACK_MODEL_FAMILY": "meta",
    "JUDGE_MODEL_ID": "google/judge",
    "JUDGE_MODEL_PROVIDER": "google",
    "JUDGE_MODEL_FAMILY": "google",
    "CHEAP_MODEL_ID": "microsoft/cheap",
    "CHEAP_MODEL_PROVIDER": "microsoft",
    "CHEAP_MODEL_FAMILY": "microsoft",
}


@pytest.fixture
def dry_run_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _MODEL_ENV.items():
        monkeypatch.setenv(key, value)
    reset_settings()
    yield  # type: ignore[misc]
    reset_settings()


def _labels() -> list[dict[str, Any]]:
    path = Path(get_settings().dataset_dir) / "labels.json"
    return cast("list[dict[str, Any]]", json.loads(path.read_text())["documents"])


async def _diagnose(doc_id: str, client: LLMClient) -> tuple[str, str]:
    """Run the pipeline on one dataset document and return its (step, category)."""
    dataset_dir = Path(get_settings().dataset_dir)
    text = (dataset_dir / "documents" / f"{doc_id}.txt").read_text()
    with trace_context(document_id=doc_id) as ctx:
        await Pipeline(client=client).run(Document(content=text), document_id=doc_id)
        assert ctx.trace is not None
        # The label's own doc_type is the ground truth the analyzer scores
        # against; passing it is what makes a misclassification observable.
        entry = next(item for item in _labels() if item["doc_id"] == doc_id)
        diagnosis = analyze_trace(ctx.trace, text, entry.get("doc_type"))
    if diagnosis.root_cause_step == "none":
        return "none", "unknown"
    return diagnosis.root_cause_step, diagnosis.category.value


async def test_dry_run_reproduces_every_dataset_label(dry_run_env: None) -> None:
    """Every document's diagnosis must match its label, not just the failures."""
    client = LLMClient(dry_run=True)
    mismatches: list[str] = []

    for entry in _labels():
        step, category = await _diagnose(entry["doc_id"], client)
        if entry["should_fail"]:
            expected = (entry["failure_step"], entry["failure_category"])
            actual = ("none", "unknown") if step == "none" else (step, category)
        else:
            expected = ("none", "unknown")
            actual = (step, category)
        if expected != actual:
            mismatches.append(f"{entry['doc_id']}: expected {expected}, got {actual}")

    assert not mismatches, "dry-run no longer matches dataset/labels.json:\n" + "\n".join(
        mismatches
    )


async def test_all_five_seeded_failures_are_reproduced(dry_run_env: None) -> None:
    """The five seeded pairs, asserted by name so a regression names itself."""
    client = LLMClient(dry_run=True)
    expected_pairs = {
        entry["doc_id"]: (entry["failure_step"], entry["failure_category"])
        for entry in _labels()
        if entry["should_fail"]
    }

    assert len(expected_pairs) == 5, "labels.json no longer seeds exactly five failures"

    for doc_id, expected in expected_pairs.items():
        assert await _diagnose(doc_id, client) == expected, doc_id


async def test_documents_without_a_seeded_failure_pass(dry_run_env: None) -> None:
    """The fixture must not invent failures for the ten clean documents."""
    client = LLMClient(dry_run=True)
    clean = [entry["doc_id"] for entry in _labels() if not entry["should_fail"]]

    assert len(clean) == 10
    for doc_id in clean:
        assert await _diagnose(doc_id, client) == ("none", "unknown"), doc_id


def test_fixture_classifies_from_the_document_id_not_the_prompt() -> None:
    """The classification prompt cannot name the document, so the id must.

    This is the root cause of the old behaviour: with an empty prompt every
    document became `contract`.
    """
    assert NIMClient._dry_run_document_type("invoice_001", "") is DocumentType.INVOICE
    assert NIMClient._dry_run_document_type("report_001", "") is DocumentType.REPORT
    assert NIMClient._dry_run_document_type("correspondence_001", "") is (
        DocumentType.CORRESPONDENCE
    )
    assert NIMClient._dry_run_document_type("mixed_001", "") is DocumentType.MIXED
    assert NIMClient._dry_run_document_type("contract_001", "") is DocumentType.CONTRACT
    # An id with no dataset prefix keeps the historical keyword fallback.
    assert NIMClient._dry_run_document_type("upload", "TAX INVOICE bill to") is (
        DocumentType.INVOICE
    )
    assert NIMClient._dry_run_document_type("upload", "nothing indicative") is (
        DocumentType.CONTRACT
    )


class _RecordingClient:
    """Captures the arguments `Pipeline` forwards to each step."""

    def __init__(self) -> None:
        self.seen_document_ids: list[str] = []
        self.seen_steps: list[str] = []

    def run_spend(self) -> Any:
        from contextlib import nullcontext

        return nullcontext()

    async def intake(self, document_text: str) -> Any:
        from forensics.models import Document, IntakeOutput

        return IntakeOutput(
            document=Document(content=document_text),
            cleaned_text=document_text,
            char_count=len(document_text),
        )

    async def extraction(self, intake_output: Any, document_id: str = "") -> ExtractionOutput:
        self.seen_steps.append("extraction")
        self.seen_document_ids.append(document_id)
        return ExtractionOutput(entities=[])

    async def classification(
        self, extraction_output: Any, document_id: str = ""
    ) -> ClassificationOutput:
        self.seen_steps.append("classification")
        self.seen_document_ids.append(document_id)
        return ClassificationOutput(document_type=DocumentType.CONTRACT, confidence=0.9)

    async def summarization(
        self, intake_output: Any, classification_output: Any, document_id: str = ""
    ) -> SummaryOutput:
        self.seen_steps.append("summarization")
        self.seen_document_ids.append(document_id)
        return SummaryOutput(summary="ok")


async def test_pipeline_forwards_document_id_to_every_llm_step() -> None:
    """The plumbing the fixture depends on: Pipeline.run -> each client step."""
    client = _RecordingClient()
    pipeline = Pipeline(client=cast("LLMClient", client))

    with trace_context(document_id="invoice_042"):
        await pipeline.run(Document(content="text"), document_id="invoice_042")

    assert client.seen_steps == ["extraction", "classification", "summarization"]
    assert client.seen_document_ids == ["invoice_042"] * 3


async def test_structured_dry_run_ignores_prompt_keywords_for_a_seeded_doc(
    dry_run_env: None, tmp_path: Path
) -> None:
    """An `invoice_001` prompt with no invoice words must still classify INVOICE."""
    client = NIMClient(
        api_key="nvapi-dry-run-fixture-key",  # pragma: allowlist secret
        cache_dir=tmp_path / "cache",
        spend_file=tmp_path / "spend.json",
        dry_run=True,
    )
    settings = get_settings()

    response = await client.structured(
        settings.pipeline_model,
        "Classify this document. No hints here.",
        ClassificationOutput,
        document_id="invoice_001",
    )

    assert isinstance(response.parsed, ClassificationOutput)
    assert response.parsed.document_type is DocumentType.INVOICE
