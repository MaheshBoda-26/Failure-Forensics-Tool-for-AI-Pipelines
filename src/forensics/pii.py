"""PII redaction using compiled regex patterns. No external deps.

Redacts API keys, emails, phones, credit cards, SSNs, IPs, JWTs.
Configurable per entity type via environment variables.
"""

import contextlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Pre-compiled patterns (fast, no backtracking)
# Ordered by specificity to avoid partial matches
PATTERNS: dict[str, re.Pattern[str]] = {
    # Accepts both underscore and hyphen separators: the spec's example
    # (`sk-or-abc123…`) and real OpenRouter keys (`sk-or-v1-…`) are hyphenated,
    # so a pattern requiring `sk_or_` silently redacted nothing.
    #
    # The last two alternatives cover keys this tool itself mints (`ffk_…`) and
    # the widely-used OpenAI-style `sk-…` form. Without them a trace could store
    # the caller's own API key in plaintext while reporting redaction enabled.
    "api_key": re.compile(
        r"\b(?:"
        r"nvapi-[A-Za-z0-9_-]{16,}"
        r"|(?:sk|pk|rk)[-_](?:or|live|test)[-_][A-Za-z0-9_-]{16,}"
        r"|ffk_[A-Za-z0-9_-]{16,}"
        r"|sk-[A-Za-z0-9_-]{20,}"
        r")\b"
    ),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # At least one separator is required, and a leading non-word boundary is
    # enforced. The previous pattern made every separator optional, so any
    # contiguous 10-13 digit run matched: Unix timestamps, 10-digit invoice or
    # account numbers, and the tail of a longer digit string were all redacted
    # as "phone".
    "phone": re.compile(
        r"(?<!\w)(?:\+\d{1,3}[-.\s])?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
    ),
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
        custom_patterns: list[Any] | None = None,
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
        if isinstance(custom_patterns, list):
            for cp in custom_patterns:
                # Malformed entries are skipped rather than raised on: this
                # constructor runs while a finished trace is being persisted, so
                # raising here would lose the trace over a config typo.
                # `validate_startup` rejects the same value loudly at boot.
                if not isinstance(cp, dict):
                    continue
                name = cp.get("name")
                pattern = cp.get("pattern")
                if not isinstance(name, str) or not name.strip():
                    continue
                if not isinstance(pattern, str) or not pattern.strip():
                    continue
                try:
                    compiled = re.compile(pattern)
                except re.error:
                    logger.warning("Skipping invalid PII custom pattern %r", name)
                    continue
                self._patterns[name] = compiled
                replacement = cp.get("replacement")
                if isinstance(replacement, str) and replacement:
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


def parse_custom_patterns(raw: str) -> list[dict[str, str]]:
    """Parse `PII_CUSTOM_PATTERNS` into validated entries, or raise ValueError.

    One parser for the two callers with opposite needs: `validate_startup`
    wants a loud error naming the bad value, and `create_redactor_from_settings`
    wants to keep going. Invalid regexes, a non-array JSON value, and entries
    missing `name`/`pattern` are all rejected here instead of crashing mid-run.
    """
    if not raw or not raw.strip():
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"PII_CUSTOM_PATTERNS is not valid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise ValueError("PII_CUSTOM_PATTERNS must be a JSON array of {name, pattern} objects")

    patterns: list[dict[str, str]] = []
    for index, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            raise ValueError(f"PII_CUSTOM_PATTERNS[{index}] must be an object")
        name = entry.get("name")
        pattern = entry.get("pattern")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"PII_CUSTOM_PATTERNS[{index}] is missing a non-empty 'name'")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(f"PII_CUSTOM_PATTERNS[{index}] is missing a non-empty 'pattern'")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(
                f"PII_CUSTOM_PATTERNS[{index}] ('{name}') is not a valid regex: {exc}"
            ) from exc
        item = {"name": name, "pattern": pattern}
        replacement = entry.get("replacement")
        if isinstance(replacement, str) and replacement:
            item["replacement"] = replacement
        patterns.append(item)
    return patterns


def create_redactor_from_settings() -> PIIRedactor:
    """Create a PIIRedactor from application settings."""
    from forensics.config import get_settings

    settings = get_settings()
    entity_types = (
        frozenset(t.strip() for t in settings.pii_entity_types.split(",") if t.strip())
        if settings.pii_redaction_enabled
        else frozenset()
    )

    custom_patterns: list[dict[str, str]] = []
    if settings.pii_custom_patterns:
        # Non-fatal here by design: this runs while a finished trace is being
        # persisted. `validate_startup` is where a malformed value must fail.
        with contextlib.suppress(ValueError):
            custom_patterns = parse_custom_patterns(settings.pii_custom_patterns)

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
    "parse_custom_patterns",
    "redact_diagnosis",
]
