"""The four pipeline steps and the chain that runs them.

Intake -> Extraction -> Classification -> Summarization. Each step is a clean
isolated function with typed input and output; if the steps are spaghetti, the
tracing is meaningless.
"""

from pathlib import Path

from forensics.llm import LLMClient, create_llm_client, using_llm_client
from forensics.models import (
    ClassificationOutput,
    Document,
    ExtractionOutput,
    IntakeOutput,
    PipelineOutput,
    SummaryOutput,
)
from forensics.tracing import traced_step


class Pipeline:
    """Four-step document processing pipeline with real LLM client."""

    def __init__(self, client: LLMClient):
        self.client = client
        self.pipeline_version = "0.1.0"

    @classmethod
    async def create(cls, cache_dir: Path | None = None) -> "Pipeline":
        """Create a new Pipeline with initialized LLM client."""
        client = await create_llm_client(cache_dir=cache_dir or Path("data/cache"))
        return cls(client=client)

    @traced_step("intake")
    async def intake(self, document: Document) -> IntakeOutput:
        """Normalize and validate input document."""
        return await self.client.intake(document.content)

    @traced_step("extraction")
    async def extraction(
        self, intake_output: IntakeOutput, document_id: str = ""
    ) -> ExtractionOutput:
        """Extract entities from the cleaned text."""
        return await self.client.extraction(intake_output, document_id=document_id)

    @traced_step("classification")
    async def classification(
        self, extraction_output: ExtractionOutput, document_id: str = ""
    ) -> ClassificationOutput:
        """Classify the document type."""
        return await self.client.classification(extraction_output, document_id=document_id)

    @traced_step("summarization")
    async def summarization(
        self,
        intake_output: IntakeOutput,
        classification_output: ClassificationOutput,
        document_id: str = "",
    ) -> SummaryOutput:
        """Generate a summary of the document."""
        return await self.client.summarization(
            intake_output, classification_output, document_id=document_id
        )

    async def run(self, document: Document, document_id: str = "") -> PipelineOutput:
        """Run the full pipeline on a document.

        `document_id` is forwarded to each step so the offline (`DRY_RUN`)
        fixture can key its canned failures on the document; it is not part of
        any prompt, so it has no effect on a real model call.
        """
        with self.client.run_spend():
            intake_out = await self.intake(document)
            extraction_out = await self.extraction(intake_out, document_id=document_id)
            classification_out = await self.classification(extraction_out, document_id=document_id)
            summarization_out = await self.summarization(
                intake_out, classification_out, document_id=document_id
            )

        return PipelineOutput(
            intake=intake_out,
            extraction=extraction_out,
            classification=classification_out,
            summarization=summarization_out,
        )


async def run_pipeline(
    document: Document, cache_dir: Path | None = None, document_id: str = ""
) -> PipelineOutput:
    """Convenience function to run the pipeline with default settings."""
    async with using_llm_client(cache_dir=cache_dir) as client:
        pipeline = Pipeline(client=client)
        return await pipeline.run(document, document_id=document_id)
