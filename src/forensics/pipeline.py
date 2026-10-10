"""The four pipeline steps and the chain that runs them.

Intake -> Extraction -> Classification -> Summarization. Each step is a clean
isolated function with typed input and output; if the steps are spaghetti, the
tracing is meaningless.
"""

from pathlib import Path

from forensics.fake_llm import FakeLLMClient, create_fake_client
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
    """Four-step document processing pipeline with fake LLM."""

    def __init__(
        self,
        client: FakeLLMClient | None = None,
        cache_dir: Path | None = None,
    ):
        self.client = client or create_fake_client(cache_dir or Path("data/cache"))
        self.pipeline_version = "0.1.0"

    @traced_step("intake")
    def intake(self, document: Document) -> IntakeOutput:
        """Normalize and validate input document."""
        cleaned = document.content.strip()
        return IntakeOutput(
            document=document,
            cleaned_text=cleaned,
            char_count=len(cleaned),
            detected_encoding="utf-8",
        )

    @traced_step("extraction")
    def extraction(self, intake_output: IntakeOutput) -> ExtractionOutput:
        """Extract entities from the cleaned text."""
        response = self.client.structured(
            model="fake",
            prompt=f"Extract entities from: {intake_output.cleaned_text[:500]}",
            response_model=ExtractionOutput,
            step="extraction",
            input_data={"text": intake_output.cleaned_text},
        )
        return response

    @traced_step("classification")
    def classification(self, extraction_output: ExtractionOutput) -> ClassificationOutput:
        """Classify the document type."""
        response = self.client.structured(
            model="fake",
            prompt=f"Classify document with entities: {extraction_output.entities}",
            response_model=ClassificationOutput,
            step="classification",
            input_data={"entities": extraction_output.entities},
        )
        return response

    @traced_step("summarization")
    def summarization(
        self, intake_output: IntakeOutput, classification_output: ClassificationOutput
    ) -> SummaryOutput:
        """Generate a summary of the document."""
        doc_type = classification_output.document_type.value
        prompt = f"Summarize {doc_type} document: {intake_output.cleaned_text[:500]}"
        response = self.client.structured(
            model="fake",
            prompt=prompt,
            response_model=SummaryOutput,
            step="summarization",
            input_data={
                "text": intake_output.cleaned_text,
                "document_type": doc_type,
            },
        )
        return response

    def run(self, document: Document) -> PipelineOutput:
        """Run the full pipeline on a document."""
        intake_out = self.intake(document)
        extraction_out = self.extraction(intake_out)
        classification_out = self.classification(extraction_out)
        summarization_out = self.summarization(intake_out, classification_out)

        return PipelineOutput(
            intake=intake_out,
            extraction=extraction_out,
            classification=classification_out,
            summarization=summarization_out,
        )


def run_pipeline(document: Document, cache_dir: Path | None = None) -> PipelineOutput:
    """Convenience function to run the pipeline with default settings."""
    pipeline = Pipeline(cache_dir=cache_dir)
    return pipeline.run(document)
