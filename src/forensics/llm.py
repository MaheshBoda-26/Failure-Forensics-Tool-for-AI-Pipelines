"""OpenRouter client — the single LLM entry point for the whole project.

Exists so that prompt, raw response, token usage, provider routing, latency,
retries, caching, replay, and spend caps are captured in exactly one place
instead of leaking into every step. Handles the three-tier structured-output
fallback and both plain and reasoning-model response shapes.
"""

import hashlib
import json
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from forensics.config import ModelConfig, get_settings
from forensics.models import (
    ClassificationOutput,
    ExtractionOutput,
    IntakeOutput,
    SummaryOutput,
)

T = TypeVar("T", bound=BaseModel)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMError(Exception):
    """Base exception for LLM errors."""

    def __init__(self, message: str, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class AuthError(LLMError):
    """Authentication/authorization error (401, 403)."""

    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message, retryable=False, status_code=status_code)


class ModelNotFoundError(LLMError):
    """Model not found (404)."""

    def __init__(self, message: str, status_code: int = 404):
        super().__init__(message, retryable=False, status_code=status_code)


class RateLimitError(LLMError):
    """Rate limit exceeded (429)."""

    def __init__(self, message: str, status_code: int = 429, retry_after: float | None = None):
        super().__init__(message, retryable=True, status_code=status_code)
        self.retry_after = retry_after


class ServerError(LLMError):
    """Server error (5xx)."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message, retryable=True, status_code=status_code)


class ValidationError(LLMError):
    """Structured output validation failed."""

    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None):
        super().__init__(message, retryable=False)
        self.errors = errors


class SpendCapExceededError(LLMError):
    """Per-run or global spend cap exceeded."""

    def __init__(self, message: str, cap_type: str):
        super().__init__(message, retryable=False)
        self.cap_type = cap_type


@dataclass
class TokenUsage:
    """Token usage from an LLM response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    @property
    def billable_tokens(self) -> int:
        """Tokens that count toward cost (prompt + completion + reasoning)."""
        return self.prompt_tokens + self.completion_tokens + self.reasoning_tokens


@dataclass
class LLMResponse:
    """Raw LLM response with metadata."""

    content: str
    model: str
    provider: str
    usage: TokenUsage
    latency_ms: int
    raw_response: dict[str, Any]
    cached: bool = False


@dataclass
class StructuredResponse(LLMResponse):
    """Structured LLM response with parsed Pydantic model."""

    parsed: BaseModel | None = None


class ResponseCache:
    """File-based response cache keyed by (model, provider, prompt_version, input_hash)."""

    def __init__(self, cache_dir: Path, enabled: bool = True):
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled
        if enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, model: str, provider: str, prompt_version: str, prompt: str) -> str:
        """Generate deterministic cache key."""
        content = f"{model}:{provider}:{prompt_version}:{prompt}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(
        self, model: str, provider: str, prompt_version: str, prompt: str
    ) -> LLMResponse | None:
        """Get cached response if exists."""
        if not self.enabled:
            return None
        key = self._cache_key(model, provider, prompt_version, prompt)
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            return LLMResponse(**data)
        except Exception:
            return None

    def set(
        self,
        model: str,
        provider: str,
        prompt_version: str,
        prompt: str,
        response: LLMResponse,
    ) -> None:
        """Cache response."""
        if not self.enabled:
            return
        key = self._cache_key(model, provider, prompt_version, prompt)
        path = self._cache_path(key)
        try:
            # Mark as cached for future retrievals
            response.cached = True
            with open(path, "w") as f:
                json.dump(
                    {
                        "content": response.content,
                        "model": response.model,
                        "provider": response.provider,
                        "usage": {
                            "prompt_tokens": response.usage.prompt_tokens,
                            "completion_tokens": response.usage.completion_tokens,
                            "reasoning_tokens": response.usage.reasoning_tokens,
                            "total_tokens": response.usage.total_tokens,
                        },
                        "latency_ms": response.latency_ms,
                        "raw_response": response.raw_response,
                        "cached": True,
                    },
                    f,
                )
        except Exception:
            pass  # Cache failures are non-fatal


class SpendTracker:
    """Tracks per-run and global spend, enforces caps."""

    def __init__(
        self,
        cap_per_run_usd: float,
        cap_global_usd: float,
        spend_file: Path | None = None,
    ):
        self.cap_per_run_usd = cap_per_run_usd
        self.cap_global_usd = cap_global_usd
        self.spend_file = spend_file or Path("data/spend.json")
        self.spend_file.parent.mkdir(parents=True, exist_ok=True)
        self._run_spend_usd = 0.0
        self._global_spend_usd = self._load_global_spend()

    def _load_global_spend(self) -> float:
        if self.spend_file.exists():
            try:
                with open(self.spend_file) as f:
                    data = json.load(f)
                return float(data.get("global_spend_usd", 0.0))
            except Exception:
                return 0.0
        return 0.0

    def _save_global_spend(self) -> None:
        try:
            with open(self.spend_file, "w") as f:
                json.dump({"global_spend_usd": self._global_spend_usd}, f)
        except Exception:
            pass

    def reset_run(self) -> None:
        """Reset per-run spend counter."""
        self._run_spend_usd = 0.0

    @property
    def run_spend_usd(self) -> float:
        return self._run_spend_usd

    @property
    def global_spend_usd(self) -> float:
        return self._global_spend_usd

    def check_and_add(self, cost_usd: float) -> None:
        """Check caps and add cost. Raises SpendCapExceededError if exceeded."""
        if self._run_spend_usd + cost_usd > self.cap_per_run_usd:
            msg = (
                f"Per-run spend cap exceeded: "
                f"${self._run_spend_usd:.4f} + ${cost_usd:.4f} > ${self.cap_per_run_usd:.2f}"
            )
            raise SpendCapExceededError(msg, "per_run")
        if self._global_spend_usd + cost_usd > self.cap_global_usd:
            msg = (
                f"Global spend cap exceeded: "
                f"${self._global_spend_usd:.4f} + ${cost_usd:.4f} > ${self.cap_global_usd:.2f}"
            )
            raise SpendCapExceededError(msg, "global")
        self._run_spend_usd += cost_usd
        self._global_spend_usd += cost_usd
        self._save_global_spend()


class OpenRouterClient:
    """OpenRouter API client with structured output, caching, spend caps, and retries."""

    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: Path | None = None,
        spend_file: Path | None = None,
        dry_run: bool = False,
    ):
        settings = get_settings()
        self.api_key = api_key or settings.openrouter_api_key
        self.dry_run = dry_run
        self.cache = ResponseCache(cache_dir or settings.cache_dir, settings.cache_enabled)
        self.spend_tracker = SpendTracker(
            settings.spend_cap_per_run_usd,
            settings.spend_cap_global_usd,
            spend_file,
        )
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=OPENROUTER_BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/failure-forensics",
                    "X-Title": "Failure Forensics Tool",
                },
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _build_messages(self, prompt: str, system: str | None = None) -> list[dict[str, str]]:
        """Build messages array for chat completion."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _build_structured_prompt(self, prompt: str, schema: dict[str, Any]) -> str:
        """Build prompt with schema instructions for tier 2 fallback."""
        schema_json = json.dumps(schema, indent=2)
        return (
            f"{prompt}\n\n"
            f"Respond with a JSON object matching this schema:\n"
            f"```json\n{schema_json}\n```\n"
            f"Output ONLY the JSON object, no extra text."
        )

    def _extract_reasoning(self, response: dict[str, Any]) -> tuple[str, int]:
        """Extract content and reasoning tokens from response.

        Handles both standard and reasoning model response shapes.
        """
        choices = response.get("choices", [])
        if not choices:
            return "", 0

        message = choices[0].get("message", {})
        content = message.get("content", "") or ""
        reasoning = message.get("reasoning", "") or ""

        # Some models put reasoning in a separate field
        if reasoning:
            content = f"{reasoning}\n\n{content}".strip()

        reasoning_tokens = response.get("usage", {}).get("reasoning_tokens", 0)
        return content, reasoning_tokens

    def _calculate_cost(self, model: ModelConfig, usage: TokenUsage) -> float:
        """Calculate cost in USD for given token usage."""
        cost_in = (usage.prompt_tokens / 1_000_000) * model.price_in_per_1m
        cost_out = (
            (usage.completion_tokens + usage.reasoning_tokens) / 1_000_000
        ) * model.price_out_per_1m
        return cost_in + cost_out

    async def _make_request(
        self,
        model_config: ModelConfig,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Make raw request to OpenRouter API."""
        client = await self._get_client()

        payload = {
            "model": model_config.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or model_config.max_tokens,
        }

        if model_config.provider:
            payload["provider"] = {"order": [model_config.provider], "allow_fallbacks": False}

        if response_format:
            payload["response_format"] = response_format

        start = time.perf_counter()
        response = await client.post("/chat/completions", json=payload)
        latency_ms = int((time.perf_counter() - start) * 1000)

        if response.status_code == 401:
            raise AuthError("Invalid API key", 401)
        elif response.status_code == 403:
            raise AuthError("Forbidden - check API key permissions", 403)
        elif response.status_code == 404:
            raise ModelNotFoundError(f"Model not found: {model_config.model_id}", 404)
        elif response.status_code == 429:
            retry_after = None
            if "retry-after" in response.headers:
                retry_after = float(response.headers["retry-after"])
            raise RateLimitError("Rate limit exceeded", 429, retry_after)
        elif 500 <= response.status_code < 600:
            raise ServerError(f"Server error: {response.status_code}", response.status_code)
        elif response.status_code != 200:
            msg = f"Request failed: {response.status_code} - {response.text}"
            raise LLMError(msg, retryable=False, status_code=response.status_code)

        data: dict[str, Any] = response.json()
        data["_latency_ms"] = latency_ms
        return data

    async def _try_native_structured(
        self,
        model_config: ModelConfig,
        prompt: str,
        response_model: type[T],
        system: str | None = None,
    ) -> StructuredResponse:
        """Tier 1: Native structured output via response_format."""
        schema = response_model.model_json_schema()
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": response_model.__name__, "schema": schema},
        }

        messages = self._build_messages(prompt, system)
        data = await self._make_request(model_config, messages, response_format=response_format)

        content, reasoning_tokens = self._extract_reasoning(data)
        parsed = response_model.model_validate_json(content)

        usage_data = data.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            reasoning_tokens=reasoning_tokens,
            total_tokens=usage_data.get("total_tokens", 0),
        )

        return StructuredResponse(
            content=content,
            model=model_config.model_id,
            provider=model_config.provider,
            usage=usage,
            latency_ms=data.get("_latency_ms", 0),
            raw_response=data,
            parsed=parsed,
        )

    async def _try_schema_in_prompt(
        self,
        model_config: ModelConfig,
        prompt: str,
        response_model: type[T],
        system: str | None = None,
    ) -> StructuredResponse:
        """Tier 2: Schema in prompt with tolerant parsing."""
        schema = response_model.model_json_schema()
        structured_prompt = self._build_structured_prompt(prompt, schema)

        messages = self._build_messages(structured_prompt, system)
        data = await self._make_request(model_config, messages)

        content, reasoning_tokens = self._extract_reasoning(data)

        # Tolerant parsing: try to extract JSON from response
        try:
            # Find JSON object in response
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = content[start:end]
                parsed = response_model.model_validate_json(json_str)
            else:
                raise ValidationError("No JSON object found in response")
        except Exception as e:
            raise ValidationError(f"Failed to parse structured output: {e}") from e

        usage_data = data.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            reasoning_tokens=reasoning_tokens,
            total_tokens=usage_data.get("total_tokens", 0),
        )

        return StructuredResponse(
            content=content,
            model=model_config.model_id,
            provider=model_config.provider,
            usage=usage,
            latency_ms=data.get("_latency_ms", 0),
            raw_response=data,
            parsed=parsed,
        )

    async def _try_repair_roundtrip(
        self,
        model_config: ModelConfig,
        prompt: str,
        response_model: type[T],
        system: str | None = None,
        last_error: Exception | None = None,
    ) -> StructuredResponse:
        """Tier 3: Repair roundtrip - feed validation error back to model."""
        schema = response_model.model_json_schema()
        structured_prompt = self._build_structured_prompt(prompt, schema)

        if last_error:
            error_msg = str(last_error)
            structured_prompt += (
                f"\n\nPrevious attempt failed validation: {error_msg}"
                f"\nPlease fix and output valid JSON only."
            )

        messages = self._build_messages(structured_prompt, system)
        data = await self._make_request(model_config, messages)

        content, reasoning_tokens = self._extract_reasoning(data)

        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = content[start:end]
                parsed = response_model.model_validate_json(json_str)
            else:
                raise ValidationError("No JSON object found in repair response")
        except Exception as e:
            raise ValidationError(f"Repair roundtrip failed: {e}") from e

        usage_data = data.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            reasoning_tokens=reasoning_tokens,
            total_tokens=usage_data.get("total_tokens", 0),
        )

        return StructuredResponse(
            content=content,
            model=model_config.model_id,
            provider=model_config.provider,
            usage=usage,
            latency_ms=data.get("_latency_ms", 0),
            raw_response=data,
            parsed=parsed,
        )

    async def structured(
        self,
        model_config: ModelConfig,
        prompt: str,
        response_model: type[T],
        system: str | None = None,
        prompt_version: str = "v1",
        step: str = "",
    ) -> StructuredResponse:
        """Call LLM with 3-tier structured output fallback."""

        if self.dry_run:
            # Return minimal valid response for dry run
            from forensics.models import Document, DocumentType

            if response_model == IntakeOutput:
                parsed: BaseModel = IntakeOutput(
                    document=Document(content=prompt),
                    cleaned_text=prompt,
                    char_count=len(prompt),
                )
            elif response_model == ExtractionOutput:
                parsed = ExtractionOutput(entities=[])
            elif response_model == ClassificationOutput:
                parsed = ClassificationOutput(document_type=DocumentType.CONTRACT, confidence=0.5)
            elif response_model == SummaryOutput:
                parsed = SummaryOutput(summary="Dry run summary", key_points=[])
            else:
                raise ValueError(f"Unknown response model: {response_model}")

            return StructuredResponse(
                content="",
                model=model_config.model_id,
                provider=model_config.provider,
                usage=TokenUsage(),
                latency_ms=0,
                raw_response={},
                parsed=parsed,
                cached=True,
            )

        # Check cache first
        cached = self.cache.get(
            model_config.model_id, model_config.provider, prompt_version, prompt
        )
        if cached:
            # Try to parse cached content
            try:
                parsed = response_model.model_validate_json(cached.content)
                return StructuredResponse(
                    content=cached.content,
                    model=cached.model,
                    provider=cached.provider,
                    usage=cached.usage,
                    latency_ms=cached.latency_ms,
                    raw_response=cached.raw_response,
                    parsed=parsed,
                    cached=True,
                )
            except Exception:
                pass  # Cache miss, continue to actual call

        # Try tiers in order
        last_error: Exception | None = None

        # Tier 1: Native structured output
        if model_config.supports_structured:
            try:
                return await self._try_native_structured(
                    model_config, prompt, response_model, system
                )
            except (ValidationError, LLMError) as e:
                last_error = e

        # Tier 2: Schema in prompt
        try:
            return await self._try_schema_in_prompt(model_config, prompt, response_model, system)
        except (ValidationError, LLMError) as e:
            last_error = e

        # Tier 3: Repair roundtrip
        try:
            return await self._try_repair_roundtrip(
                model_config, prompt, response_model, system, last_error
            )
        except (ValidationError, LLMError) as e:
            last_error = e

        # All tiers failed
        raise LLMError(f"All structured output tiers failed: {last_error}")

    async def chat_completion(
        self,
        model_config: ModelConfig,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Simple chat completion without structured output."""
        if self.dry_run:
            return LLMResponse(
                content="{}",
                model=model_config.model_id,
                provider=model_config.provider,
                usage=TokenUsage(),
                latency_ms=0,
                raw_response={},
                cached=True,
            )

        data = await self._make_request(
            model_config, messages, max_tokens=max_tokens, temperature=temperature
        )

        content, reasoning_tokens = self._extract_reasoning(data)

        usage_data = data.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            reasoning_tokens=reasoning_tokens,
            total_tokens=usage_data.get("total_tokens", 0),
        )

        return LLMResponse(
            content=content,
            model=model_config.model_id,
            provider=model_config.provider,
            usage=usage,
            latency_ms=data.get("_latency_ms", 0),
            raw_response=data,
        )

    async def run_with_fallback(
        self,
        primary: ModelConfig,
        fallback: ModelConfig,
        prompt: str,
        response_model: type[T],
        system: str | None = None,
        prompt_version: str = "v1",
        step: str = "",
    ) -> StructuredResponse:
        """Run with primary model, fall back on failure."""
        try:
            return await self.structured(
                primary, prompt, response_model, system, prompt_version, step
            )
        except (RateLimitError, ServerError, ModelNotFoundError):
            # Log fallback trigger
            return await self.structured(
                fallback, prompt, response_model, system, prompt_version, step
            )


class LLMClient:
    """High-level LLM client for the pipeline steps."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        spend_file: Path | None = None,
        dry_run: bool = False,
    ):
        self.openrouter = OpenRouterClient(
            cache_dir=cache_dir, spend_file=spend_file, dry_run=dry_run
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.openrouter.close()

    @contextmanager
    def run_spend(self) -> Generator[None, None, None]:
        """Context manager to track spend for a single pipeline run."""
        self.openrouter.spend_tracker.reset_run()
        try:
            yield
        finally:
            pass  # Spend is tracked incrementally

    def _get_step_prompt(self, step: str, input_data: dict[str, Any]) -> tuple[str, str | None]:
        """Get prompt and system message for a pipeline step."""
        prompts = {
            "intake": (
                "Clean and normalize the input document text. Return the "
                "cleaned text, character count, and detected encoding.",
                "You are a document intake processor. Normalize text, handle "
                "encoding issues, and prepare for downstream processing.",
            ),
            "extraction": (
                f"Extract entities from this document: {input_data.get('text', '')[:2000]}",
                "You are an entity extraction specialist. Extract names, dates, "
                "amounts, organizations, and key terms with their source spans.",
            ),
            "classification": (
                f"Classify this document type based on extracted entities: "
                f"{input_data.get('entities', [])}",
                "You are a document classifier. Classify as: contract, invoice, "
                "report, or correspondence. Return confidence scores.",
            ),
            "summarization": (
                f"Summarize this {input_data.get('document_type', 'document')}: "
                f"{input_data.get('text', '')[:2000]}",
                "You are a summarizer. Create a concise summary with key points "
                "tailored to the document type.",
            ),
        }
        return prompts.get(step, ("Process this input.", "You are a helpful assistant."))

    async def intake(self, document_text: str) -> IntakeOutput:
        settings = get_settings()
        prompt, system = self._get_step_prompt("intake", {"text": document_text})
        response = await self.openrouter.run_with_fallback(
            settings.pipeline_model,
            settings.pipeline_fallback_model,
            prompt,
            IntakeOutput,
            system=system,
            prompt_version="intake-v1",
            step="intake",
        )
        return response.parsed  # type: ignore[return-value]

    async def extraction(self, intake_output: IntakeOutput) -> ExtractionOutput:
        settings = get_settings()
        prompt, system = self._get_step_prompt("extraction", {"text": intake_output.cleaned_text})
        response = await self.openrouter.run_with_fallback(
            settings.pipeline_model,
            settings.pipeline_fallback_model,
            prompt,
            ExtractionOutput,
            system=system,
            prompt_version="extraction-v1",
            step="extraction",
        )
        return response.parsed  # type: ignore[return-value]

    async def classification(self, extraction_output: ExtractionOutput) -> ClassificationOutput:
        settings = get_settings()
        entities_data = [
            {"value": e.value, "type": e.entity_type} for e in extraction_output.entities
        ]
        prompt, system = self._get_step_prompt("classification", {"entities": entities_data})
        response = await self.openrouter.run_with_fallback(
            settings.pipeline_model,
            settings.pipeline_fallback_model,
            prompt,
            ClassificationOutput,
            system=system,
            prompt_version="classification-v1",
            step="classification",
        )
        return response.parsed  # type: ignore[return-value]

    async def summarization(
        self,
        intake_output: IntakeOutput,
        classification_output: ClassificationOutput,
    ) -> SummaryOutput:
        settings = get_settings()
        prompt, system = self._get_step_prompt(
            "summarization",
            {
                "text": intake_output.cleaned_text,
                "document_type": classification_output.document_type.value,
            },
        )
        response = await self.openrouter.run_with_fallback(
            settings.pipeline_model,
            settings.pipeline_fallback_model,
            prompt,
            SummaryOutput,
            system=system,
            prompt_version="summarization-v1",
            step="summarization",
        )
        return response.parsed  # type: ignore[return-value]

    async def judge_analyze(
        self,
        trace_data: dict[str, Any],
        document_text: str,
    ) -> dict[str, Any]:
        """Call judge model for analysis (Phase 3)."""
        settings = get_settings()
        prompt = f"""Analyze this pipeline trace for failures.

Document: {document_text[:3000]}

Trace steps:
{json.dumps(trace_data, indent=2, default=str)}

For each step, score:
- local_fidelity: Is output a faithful transformation of input? (0-1)
- global_fidelity: Is output correct relative to source document? (0-1)
- evidence: Specific evidence for scores

Return JSON with step_scores array."""

        # For now, use chat_completion since we don't have a proper judge response model yet
        response = await self.openrouter.chat_completion(
            settings.judge_model,
            [{"role": "user", "content": prompt}],
        )
        try:
            result = json.loads(response.content)
            if isinstance(result, dict):
                return result
            return {"error": "Invalid response format", "raw": response.content}
        except Exception:
            return {"error": "Failed to parse judge response", "raw": response.content}


async def create_llm_client(
    cache_dir: Path | None = None,
    spend_file: Path | None = None,
    dry_run: bool | None = None,
) -> LLMClient:
    """Factory function for dependency injection."""
    settings = get_settings()
    dry_run = dry_run if dry_run is not None else settings.dry_run
    return LLMClient(cache_dir=cache_dir, spend_file=spend_file, dry_run=dry_run)
