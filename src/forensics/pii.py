"""PII redaction using compiled regex patterns. No external deps.

Redacts API keys, emails, phones, credit cards, SSNs, IPs, JWTs.
Configurable per entity type via environment variables.
"""

import contextlib
import json
import re
from dataclasses import dataclass
from typing import Any

# Pre-compiled patterns (fast, no backtracking)
# Ordered by specificity to avoid partial matches
PATTERNS: dict[str, re.Pattern[str]] = {
    "api_key": re.compile(r"\b(?:sk|pk|rk)_(?:or|live|test)_[a-zA-Z0-9]{32,}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "phone": re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[-.\s]?){3}\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "ipv4": re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b"
    ),
    "jwt": re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"),
}

# Default entity types to redact
DEFAULT_ENTITY_TYPES = frozenset(PATTERNS.keys())


@dataclass(slots=True)
class Redaction:
    """Record of a single redaction."""

    entity_type: str
    start: int
    end: int
    original: str
    replacement: str
    path: str = ""


class PIIRedactor:
    """Redact PII from text using compiled regex patterns.

    Zero external dependencies. All patterns are pre-compiled for speed.
    """

    def __init__(
        self,
        enabled: bool = True,
        entity_types: frozenset[str] | None = None,
        custom_patterns: list[dict[str, str]] | None = None,
        replacement_char: str = "█",
    ) -> None:
        self.enabled = enabled
        self.entity_types = entity_types or DEFAULT_ENTITY_TYPES
        self.replacement_char = replacement_char
        self._patterns: dict[str, re.Pattern] = {
            k: v for k, v in PATTERNS.items() if k in self.entity_types
        }
        if custom_patterns:
            for cp in custom_patterns:
                name = cp["name"]
                pattern = cp["pattern"]
                self._patterns[name] = re.compile(pattern)

    def redact(self, text: str) -> tuple[str, list[Redaction]]:
        """Redact PII from a string.

        Returns (redacted_text, list_of_redactions).
        """
        if not self.enabled or not text:
            return text, []

        redactions: list[Redaction] = []
        redacted = text
        offset = 0

        # Sort patterns by name for deterministic order
        for entity_type in sorted(self._patterns.keys()):
            pattern = self._patterns[entity_type]
            for match in pattern.finditer(text):
                start, end = match.span()
                original = match.group()
                replacement = self.replacement_char * len(original)
                redactions.append(
                    Redaction(
                        entity_type=entity_type,
                        start=start,
                        end=end,
                        original=original,
                        replacement=replacement,
                    )
                )
                redacted = redacted[: start + offset] + replacement + redacted[end + offset :]
                offset += len(replacement) - len(original)

        return redacted, redactions

    def redact_json(self, data: Any) -> tuple[Any, list[Redaction]]:
        """Recursively redact string values in JSON-serializable data.

        Returns (redacted_data, list_of_all_redactions_with_paths).
        """
        all_redactions: list[Redaction] = []

        def _redact_obj(obj: Any, path: str = "") -> Any:
            if isinstance(obj, str):
                redacted, reds = self.redact(obj)
                for r in reds:
                    # Add path info to redaction
                    r.path = path
                all_redactions.extend(reds)
                return redacted
            elif isinstance(obj, dict):
                return {k: _redact_obj(v, f"{path}.{k}" if path else k) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [_redact_obj(v, f"{path}[{i}]") for i, v in enumerate(obj)]
            return obj

        return _redact_obj(data), all_redactions


def create_redactor_from_settings() -> PIIRedactor:
    """Create a PIIRedactor from application settings."""
    from forensics.config import get_settings

    settings = get_settings()
    entity_types = (
        frozenset(t.strip() for t in settings.pii_entity_types.split(",") if t.strip())
        if settings.pii_redaction_enabled
        else frozenset()
    )

    custom_patterns = []
    if settings.pii_custom_patterns:
        with contextlib.suppress(json.JSONDecodeError):
            custom_patterns = json.loads(settings.pii_custom_patterns)

    return PIIRedactor(
        enabled=settings.pii_redaction_enabled,
        entity_types=entity_types,
        custom_patterns=custom_patterns,
        replacement_char=settings.pii_redaction_char,
    )


__all__ = [
    "PIIRedactor",
    "Redaction",
    "create_redactor_from_settings",
]
