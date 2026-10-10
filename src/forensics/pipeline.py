"""The four pipeline steps and the chain that runs them.

Intake -> Extraction -> Classification -> Summarization. Each step is a clean
isolated function with typed input and output; if the steps are spaghetti, the
tracing is meaningless.
"""

from pathlib import Path

from forensics.llm import LLMClient, create_llm_client
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
    async def extraction(self, intake_output: IntakeOutput) -> ExtractionOutput:
        """Extract entities from the cleaned text."""
        return await self.client.extraction(intake_output)

    @traced_step("classification")
    async def classification(self, extraction_output: ExtractionOutput) -> ClassificationOutput:
        """Classify the document type."""
        return await self.client.classification(extraction_output)

    @traced_step("summarization")
    async def summarization(
        self, intake_output: IntakeOutput, classification_output: ClassificationOutput
    ) -> SummaryOutput:
        """Generate a summary of the document."""
        return await self.client.summarization(intake_output, classification_output)

    async def run(self, document: Document) -> PipelineOutput:
        """Run the full pipeline on a document."""
        with self.client.run_spend():
            intake_out = await self.intake(document)
            extraction_out = await self.extraction(intake_out)
            classification_out = await self.classification(extraction_out)
            summarization_out = await self.summarization(intake_out, classification_out)

        return PipelineOutput(
            intake=intake_out,
            extraction=extraction_out,
            classification=classification_out,
            summarization=summarization_out,
        )


async def run_pipeline(document: Document, cache_dir: Path | None = None) -> PipelineOutput:
    """Convenience function to run the pipeline with default settings."""
    pipeline = await Pipeline.create(cache_dir=cache_dir)
    try:
        return await pipeline.run(document)
    finally:
        await pipeline.client.close()
