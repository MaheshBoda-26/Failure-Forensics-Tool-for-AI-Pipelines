"""Fake LLM client for deterministic, zero-cost testing and development.

Returns canned typed responses matching each pipeline step's output schema.
Supports record/replay mode for future real OpenRouter integration.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from forensics.models import (
    ClassificationOutput,
    Document,
    DocumentType,
    ExtractionOutput,
    IntakeOutput,
    SummaryOutput,
)

T = TypeVar("T", bound=BaseModel)


class FakeLLMClient:
    """Deterministic LLM client that returns pre-defined responses.

    In record mode: saves prompts/responses to cache.
    In replay mode: returns cached response matching prompt hash.
    In canned mode: returns hardcoded responses per step.
    """

    def __init__(self, cache_dir: Path, mode: str = "canned"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode  # "canned", "record", "replay"
        self._canned_responses = self._build_canned_responses()

    def _cache_key(self, model: str, prompt: str) -> str:
        """Generate deterministic cache key."""
        content = f"{model}:{prompt}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _build_canned_responses(self) -> dict[str, dict[str, Any]]:
        """Hardcoded responses for each pipeline step."""
        return {
            "intake": {
                "cleaned_text": "{text}",
                "char_count": "{char_count}",
                "detected_encoding": "utf-8",
            },
            "extraction": {
                "entities": [
                    {
                        "value": "John Smith",
                        "entity_type": "person",
                        "start_offset": 0,
                        "end_offset": 10,
                        "confidence": 0.95,
                    },
                    {
                        "value": "2024-01-15",
                        "entity_type": "date",
                        "start_offset": 20,
                        "end_offset": 30,
                        "confidence": 0.90,
                    },
                    {
                        "value": "$10,000",
                        "entity_type": "amount",
                        "start_offset": 40,
                        "end_offset": 47,
                        "confidence": 0.85,
                    },
                ],
                "raw_response": '{"entities": [...]}',
            },
            "classification": {
                "document_type": "contract",
                "confidence": 0.92,
                "all_scores": {
                    "contract": 0.92,
                    "invoice": 0.05,
                    "report": 0.02,
                    "correspondence": 0.01,
                },
            },
            "summarization": {
                "summary": "This document is a {type} containing key terms and parties.",
                "key_points": [
                    "Party A agrees to terms",
                    "Party B accepts conditions",
                    "Effective date specified",
                ],
                "metadata": {"word_count": 150},
            },
        }

    def _format_canned(self, step: str, input_data: dict[str, Any]) -> dict[str, Any]:
        """Format canned response with input-specific values."""
        canned = self._canned_responses[step].copy()

        if step == "intake":
            text = input_data.get("text", "")
            canned["cleaned_text"] = text.strip()
            canned["char_count"] = len(text.strip())

        elif step == "summarization":
            doc_type = input_data.get("document_type", "document")
            canned["summary"] = canned["summary"].format(type=doc_type)

        elif step == "classification":
            # Return the input's type if provided (for seeded failures)
            if "expected_type" in input_data:
                canned["document_type"] = input_data["expected_type"]
                canned["confidence"] = 0.95
                canned["all_scores"] = {input_data["expected_type"]: 0.95}

        elif step == "extraction":
            # For seeded failures, return specific entities
            if "entities" in input_data:
                canned["entities"] = input_data["entities"]

        return canned

    def structured(
        self,
        model: str,
        prompt: str,
        response_model: type[T],
        step: str = "",
        input_data: dict[str, Any] | None = None,
    ) -> T:
        """Return structured response matching response_model."""

        if self.mode == "replay":
            key = self._cache_key(model, prompt)
            path = self._cache_path(key)
            if path.exists():
                with open(path) as f:
                    return response_model.model_validate(json.load(f))

        if self.mode == "canned" and step and step in self._canned_responses:
            input_data = input_data or {}
            response_dict = self._format_canned(step, input_data)
            return response_model.model_validate(response_dict)

        # Fallback: minimal valid response - use cast since runtime type matches
        if response_model == IntakeOutput:
            return cast(
                T,
                IntakeOutput(
                    document=Document(content=input_data.get("text", "") if input_data else ""),
                    cleaned_text=input_data.get("text", "") if input_data else "",
                    char_count=0,
                ),
            )
        elif response_model == ExtractionOutput:
            return cast(T, ExtractionOutput(entities=[]))
        elif response_model == ClassificationOutput:
            ct = ClassificationOutput(document_type=DocumentType.CONTRACT, confidence=0.5)
            return cast(T, ct)
        elif response_model == SummaryOutput:
            so = SummaryOutput(summary="Summary not available", key_points=[])
            return cast(T, so)
        else:
            raise ValueError(f"Unknown response model: {response_model}")

    def chat_completion(
        self, model: str, messages: list[dict[str, Any]], **kwargs: Any
    ) -> dict[str, Any]:
        """OpenAI-compatible chat completion for compatibility."""
        # Extract the user message as prompt
        prompt = ""
        for msg in messages:
            if msg.get("role") == "user":
                prompt = msg.get("content", "")
                break

        if self.mode == "record":
            _ = self._cache_key(model, prompt)
            # Would save response here in real implementation
            pass

        # Return minimal OpenAI-compatible response
        return {
            "id": "fake-chatcmpl-123",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "{}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }


def create_fake_client(cache_dir: Path, mode: str = "canned") -> FakeLLMClient:
    """Factory function for dependency injection."""
    return FakeLLMClient(cache_dir, mode)
