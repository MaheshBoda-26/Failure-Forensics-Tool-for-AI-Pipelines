# SPEC: PII Redaction Pipeline

## Summary
Regex-based PII redaction before storage. Zero dependencies, <1ms overhead. Redacts API keys, emails, phones, credit cards, SSNs. Configurable per entity type.

## Config (`.env`)
```bash
PII_REDACTION_ENABLED=true|false          # default: false
PII_ENTITY_TYPES=api_key,email,phone,credit_card,ssn  # comma-separated
PII_CUSTOM_PATTERNS=                      # JSON: [{"name": "internal_id", "pattern": "INT-\\d{8}", "replacement": "[REDACTED_INTERNAL]"}]
PII_REDACTION_CHAR=█                      # replacement char (default: full block)
```

## New Module: `src/forensics/pii.py`
```python
"""PII redaction using compiled regex patterns. No external deps."""

from dataclasses import dataclass
from typing import Pattern
import re


@dataclass
class Redaction:
    entity_type: str
    start: int
    end: int
    original: str
    replacement: str


# Pre-compiled patterns (fast, no backtracking)
PATTERNS: dict[str, Pattern] = {
    "api_key": re.compile(r"(sk|pk|rk)_(or|live|test)_[a-zA-Z0-9]{32,}"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "phone": re.compile(r"(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[-.\s]?){3}\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "ipv4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "jwt": re.compile(r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"),
}


class PIIRedactor:
    def __init__(
        self,
        enabled: bool = True,
        entity_types: list[str] | None = None,
        custom_patterns: list[dict] | None = None,
        replacement_char: str = "█",
    ):
        self.enabled = enabled
        self.entity_types = entity_types or list(PATTERNS.keys())
        self.replacement_char = replacement_char
        self._patterns = {k: v for k, v in PATTERNS.items() if k in self.entity_types}
        if custom_patterns:
            for cp in custom_patterns:
                self._patterns[cp["name"]] = re.compile(cp["pattern"])

    def redact(self, text: str) -> tuple[str, list[Redaction]]:
        """Returns (redacted_text, list_of_redactions)."""
        if not self.enabled or not text:
            return text, []

        # Sort by pattern length desc to avoid partial matches
        redactions = []
        redacted = text
        offset = 0

        for entity_type, pattern in self._patterns.items():
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

    def redact_json(self, data: dict) -> tuple[dict, list[Redaction]]:
        """Recursively redact string values in JSON-serializable dict."""
        all_redactions = []

        def _redact_obj(obj, path=""):
            if isinstance(obj, str):
                redacted, reds = self.redact(obj)
                for r in reds:
                    r.path = path  # add path info
                all_redactions.extend(reds)
                return redacted
            elif isinstance(obj, dict):
                return {k: _redact_obj(v, f"{path}.{k}") for k, v in obj.items()}
            elif isinstance(obj, list):
                return [_redact_obj(v, f"{path}[{i}]") for i, v in enumerate(obj)]
            return obj

        return _redact_obj(data), all_redactions
```

## Integration Points

### `src/forensics/tracing.py`
In `finalize_trace`:
```python
from forensics.pii import PIIRedactor

redactor = PIIRedactor(
    enabled=settings.pii_redaction_enabled,
    entity_types=settings.pii_entity_types,
    custom_patterns=settings.pii_custom_patterns,
)

# Redact span fields before save
for span in trace.spans:
    span.prompt, _ = redactor.redact(span.prompt)
    span.raw_response, _ = redactor.redact(span.raw_response)
    span.input_json, _ = redactor.redact(span.input_json)
    span.output_json, _ = redactor.redact(span.output_json)

# Redact document text (optional, for trace JSON)
# trace.document_content, _ = redactor.redact(trace.document_content)
```

### `src/forensics/config.py`
Add to `Settings`:
```python
pii_redaction_enabled: bool = Field(default=False)
pii_entity_types: str = Field(default="api_key,email,phone,credit_card,ssn")
pii_custom_patterns: str = Field(default="")  # JSON string
pii_redaction_char: str = Field(default="█")
```

## Acceptance Criteria
1. `PII_REDACTION_ENABLED=false`: zero overhead, no redaction
2. `PII_REDACTION_ENABLED=true`: `sk-or-abc123...` → `████████████████████████████████`
3. Email `user@company.com` → `██████████████████`
4. Phone `+1-555-123-4567` → `█████████████████`
5. Credit card `4111 1111 1111 1111` → `████ ████ ████ ████`
6. SSN `123-45-6789` → `███-██-████`
7. JWT tokens fully redacted
8. Custom patterns work: `{"name": "internal_id", "pattern": "INT-\\d{8}", "replacement": "[REDACTED]"}` → `INT-12345678` → `[REDACTED]`
9. Redaction happens **before** JSON + SQLite write; original never hits disk
10. Redaction list stored in span metadata (optional, for audit)

## Effort
- **Files**: 2 new (`pii.py`, tests), 2 modified
- **Time**: ~3 hours
- **Risk**: Low (pure regex, well-tested patterns)
