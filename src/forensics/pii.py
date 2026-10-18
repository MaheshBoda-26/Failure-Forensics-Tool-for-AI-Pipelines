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
    # Accepts both underscore and hyphen separators: the spec's example
    # (`sk-or-abc123…`) and real OpenRouter keys (`sk-or-v1-…`) are hyphenated,
    # so a pattern requiring `sk_or_` silently redacted nothing.
    "api_key": re.compile(
        r"\b(?:nvapi-[A-Za-z0-9_-]{16,}|(?:sk|pk|rk)[-_](?:or|live|test)[-_][A-Za-z0-9_-]{16,})\b"
    ),
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
        self._patterns: dict[str, re.Pattern[str]] = {
            k: v for k, v in PATTERNS.items() if k in self.entity_types
        }
        # Custom patterns may pin an explicit replacement string; without one
        # they fall back to the block character like the built-ins.
        self._custom_replacements: dict[str, str] = {}
        if custom_patterns:
            for cp in custom_patterns:
                name = cp["name"]
                pattern = cp["pattern"]
                self._patterns[name] = re.compile(pattern)
                replacement = cp.get("replacement")
                if replacement:
                    self._custom_replacements[name] = replacement

    def redact(self, text: str) -> tuple[str, list[Redaction]]:
        """Redact PII from a string.

        Returns (redacted_text, list_of_redactions).

        Every pattern is matched against the *original* text, all match spans
        are collected, overlaps are resolved, and the replacements are spliced
        from the end of the string backwards. A running character offset is
        deliberately not used: patterns are visited in a fixed order that has
        nothing to do with where they match, so a match found earlier in that
        order but later in the string shifted every subsequent replacement to
        the wrong index — leaving part of the secret in plaintext and corrupting
        the surrounding JSON whenever a replacement's length differed from the
        text it replaced (which is exactly what a custom replacement does).
        """
        if not self.enabled or not text:
            return text, []

        # (start, end, entity_type, match) for every enabled pattern.
        found: list[tuple[int, int, str, re.Match[str]]] = []
        for entity_type, pattern in self._patterns.items():
            for match in pattern.finditer(text):
                found.append((match.start(), match.end(), entity_type, match))

        # Deterministic, position-first order: earliest start wins; between two
        # spans that begin together, the longer one wins; the entity type breaks
        # a remaining tie so the result does not depend on dict iteration order.
        found.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2]))

        selected: list[tuple[int, int, str, re.Match[str]]] = []
        cursor = -1
        for start, end, entity_type, match in found:
            if start < cursor:
                continue  # overlaps a span already chosen; the first wins
            selected.append((start, end, entity_type, match))
            cursor = end

        redactions: list[Redaction] = []
        redacted = text
        for start, end, entity_type, match in sorted(
            selected, key=lambda item: item[0], reverse=True
        ):
            original = match.group()
            custom = self._custom_replacements.get(entity_type)
            replacement = custom if custom else self.replacement_char * len(original)
            redactions.append(
                Redaction(
                    entity_type=entity_type,
                    start=start,
                    end=end,
                    original=original,
                    replacement=replacement,
                )
            )
            redacted = redacted[:start] + replacement + redacted[end:]

        # Report in positional order, which is the order the audit-list reader
        # expects (the splice above necessarily visits them backwards).
        redactions.reverse()
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


def redact_diagnosis(diagnosis: Any) -> bool:
    """Redact analyzer-derived free text on a diagnosis, in place.

    `analyze_trace` has to run against the *unredacted* trace — an entity check
    can only ground an entity it can still see — so its evidence strings can
    quote the document and the span payloads verbatim. Persisting that verdict
    would put the plaintext that the trace writer redacted back on disk under a
    different table, so the diagnosis is scrubbed at the same boundary.

    Returns True when a redactor ran (redaction enabled).
    """
    redactor = create_redactor_from_settings()
    if not redactor.enabled:
        return False

    def scrub(value: str) -> str:
        return redactor.redact(value)[0]

    diagnosis.evidence_chain = [scrub(item) for item in diagnosis.evidence_chain]
    for score in diagnosis.step_scores:
        score.evidence = [scrub(item) for item in score.evidence]
    if diagnosis.override_reason:
        diagnosis.override_reason = scrub(diagnosis.override_reason)

    analysis = getattr(diagnosis, "judge_analysis", None)
    if analysis is not None:
        for judge_score in analysis.step_scores:
            judge_score.evidence = [scrub(item) for item in judge_score.evidence]
            judge_score.semantic_issues = [scrub(item) for item in judge_score.semantic_issues]
    return True


__all__ = [
    "PIIRedactor",
    "Redaction",
    "create_redactor_from_settings",
    "redact_diagnosis",
]
