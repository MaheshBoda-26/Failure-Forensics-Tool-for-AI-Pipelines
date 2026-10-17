"""Sample custom check plugins used by tests/test_check_plugins.py.

Not part of the shipped package. Each one exercises a different half of the
plugin contract: a plain function, a class with ``__call__``, and a function
that is deliberately *not* decorated so the loader's error path can be tested.
"""

import json

from forensics.checks import CheckResult, check_plugin
from forensics.models import Span, Trace


@check_plugin(
    "no_shouting",
    step="summarization",
    description="Summary must not be entirely upper-case",
)
def no_shouting(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    try:
        data = json.loads(span.output_json or "{}")
    except json.JSONDecodeError:
        return []
    summary = str(data.get("summary", ""))
    is_shouting = bool(summary) and summary.isupper()
    evidence = f"Summary is ALL CAPS: {summary[:60]!r}" if is_shouting else "Summary is normal case"
    return [
        CheckResult(
            check_name="no_shouting",
            passed=not is_shouting,
            evidence=evidence,
            step_name=span.step_name,
        )
    ]


@check_plugin(
    "requires_invoice_word",
    step="extraction",
    description="Document must literally contain the word 'invoice'",
)
class RequiresInvoiceWord:
    """A class-shaped plugin: the registry stores an instance and calls __call__."""

    def __call__(self, span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
        present = "invoice" in document_text.lower()
        return [
            CheckResult(
                check_name="requires_invoice_word",
                passed=present,
                evidence=(
                    "Found 'invoice' in document"
                    if present
                    else "Document does not contain 'invoice'"
                ),
                step_name=span.step_name,
            )
        ]


def undecorated(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    """Intentionally not a plugin: load_plugins_from_config must reject it."""
    return []
