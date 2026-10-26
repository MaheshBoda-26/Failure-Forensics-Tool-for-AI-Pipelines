"""The shipped custom check plugins: do they return the verdicts they claim?

`custom_checks.py` is 800 lines of domain checks that the analyzer can load, and
it had **no test and no import** anywhere in the suite — the module was at 0%
coverage. Only its registration was pinned (by `test_check_plugins.py`). A check
that always passes, always fails, or crashes is exactly the kind of thing that
looks fine in a demo and silently corrupts a diagnosis.

Each check gets a positive case (clean output must not be flagged) and a
negative case (the fault must be caught). Writing these found two real verdict
bugs, kept here as regressions:

* a `currency` entity ("$") was parsed as a Decimal, so any invoice that
  extracted a currency symbol failed with "Unparseable financial amount";
* an invoice total of `Decimal("10000.00")` was compared against the string
  "$10,000.00" in the summary, so a *correct* summary was reported as an
  omission.
"""

import json
from collections.abc import Iterator

import pytest

from forensics import checks, custom_checks
from forensics.checks import get_check
from forensics.custom_checks import (
    check_contract_dates,
    check_contract_parties,
    check_correspondence_metadata,
    check_financial_amounts,
    check_invoice_totals,
    check_mixed_consistency,
    check_pii_in_extraction,
    check_pii_in_summary,
    check_report_structure,
)
from forensics.models import Span, Trace

# Importing `forensics.custom_checks` registers its nine checks process-wide as a
# side effect of the decorators. That is correct for a deployment and wrong for
# a test session: pytest imports every test module before running any, so without
# this prune the nine checks would be live for the whole suite and silently change
# unrelated diagnoses (they did — 29 tests failed). Each test calls the decorated
# functions directly, which needs no registration; the fixture below restores
# whatever the registry held around any test that does register.
_BUILTIN_ONLY = {
    name: plugin
    for name, plugin in checks._CHECK_REGISTRY.items()
    if name in checks.BUILTIN_CHECK_NAMES
}
checks._CHECK_REGISTRY.clear()
checks._CHECK_REGISTRY.update(_BUILTIN_ONLY)


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    saved = dict(checks._CHECK_REGISTRY)
    yield
    checks._CHECK_REGISTRY.clear()
    checks._CHECK_REGISTRY.update(saved)


def _span(step: str, output: dict) -> Span:
    return Span(
        trace_id="t",
        step_name=step,
        input_json="{}",
        output_json=json.dumps(output),
        model_id="test-model",
        provider="test",
        prompt="p",
        raw_response="r",
        status="success",
    )


def _trace(*spans: Span) -> Trace:
    return Trace(trace_id="t", document_id="doc_001", spans=list(spans))


def _failed(results: list) -> list:
    return [r for r in results if not r.passed]


# ─── PII ────────────────────────────────────────────────────────────────────


def test_pii_in_summary_flags_a_leaked_email() -> None:
    clean = check_pii_in_summary(
        _span("summarization", {"summary": "An invoice for consulting work."}), _trace(), ""
    )
    leaked = check_pii_in_summary(
        _span("summarization", {"summary": "Contact jane.doe@acme.example for details."}),
        _trace(),
        "",
    )

    assert _failed(clean) == []
    assert len(_failed(leaked)) == 1
    assert "email" in _failed(leaked)[0].evidence


def test_pii_in_summary_reads_key_points_too() -> None:
    """PII in `key_points` must not slip through because only `summary` is read."""
    span = _span(
        "summarization",
        {"summary": "An invoice.", "key_points": ["Account 123-45-6789 is overdue"]},
    )

    assert [r.evidence for r in _failed(check_pii_in_summary(span, _trace(), ""))] == [
        "PII detected in summary: ssn found (1 occurrence(s))"
    ]


def test_pii_in_extraction_flags_sensitive_entity_types_and_values() -> None:
    clean = _span("extraction", {"entities": [{"value": "Acme", "entity_type": "organization"}]})
    by_type = _span("extraction", {"entities": [{"value": "hunter2", "entity_type": "password"}]})
    by_value = _span(
        "extraction",
        {"entities": [{"value": "sk-" + "a" * 24, "entity_type": "note"}]},
    )

    assert _failed(check_pii_in_extraction(clean, _trace(), "")) == []
    assert len(_failed(check_pii_in_extraction(by_type, _trace(), ""))) == 1
    assert "API key pattern" in _failed(check_pii_in_extraction(by_value, _trace(), ""))[0].evidence


# ─── Financial ──────────────────────────────────────────────────────────────


def test_financial_amounts_accepts_a_currency_entity() -> None:
    """Regression: "$" typed `currency` used to be parsed as a Decimal."""
    span = _span(
        "extraction",
        {
            "entities": [
                {"value": "$", "entity_type": "currency"},
                {"value": "$1,000.00", "entity_type": "subtotal"},
                {"value": "$90.00", "entity_type": "tax"},
                {"value": "$1,090.00", "entity_type": "total"},
            ]
        },
    )

    results = check_financial_amounts(span, _trace(), "")

    assert _failed(results) == []
    assert any("consistent" in r.evidence for r in results)


def test_financial_amounts_notes_an_inconsistent_total() -> None:
    span = _span(
        "extraction",
        {
            "entities": [
                {"value": "$1,000.00", "entity_type": "subtotal"},
                {"value": "$90.00", "entity_type": "tax"},
                {"value": "$1,200.00", "entity_type": "total"},
            ]
        },
    )

    failed = _failed(check_financial_amounts(span, _trace(), ""))

    assert [r.evidence for r in failed] == [
        "Financial inconsistency: subtotal+tax (1090.00) != total (1200.00)"
    ]


def test_financial_amounts_flags_an_unparseable_amount() -> None:
    span = _span(
        "extraction", {"entities": [{"value": "about ten dollars", "entity_type": "total"}]}
    )

    assert len(_failed(check_financial_amounts(span, _trace(), ""))) == 1


def test_invoice_totals_matches_a_formatted_total() -> None:
    """Regression: "$10,000.00" in the summary did not match Decimal("10000.00")."""
    extraction = _span(
        "extraction", {"entities": [{"value": "$10,000.00", "entity_type": "total"}]}
    )
    summary = _span(
        "summarization",
        {"summary": "The invoice total is $10,000.00, payable in 30 days.", "key_points": []},
    )

    results = check_invoice_totals(summary, _trace(extraction, summary), "")

    assert _failed(results) == []


def test_invoice_totals_flags_an_omitted_total() -> None:
    extraction = _span(
        "extraction", {"entities": [{"value": "$10,000.00", "entity_type": "total"}]}
    )
    summary = _span(
        "summarization", {"summary": "An invoice for consulting services.", "key_points": []}
    )

    failed = _failed(check_invoice_totals(summary, _trace(extraction, summary), ""))

    assert len(failed) == 1
    assert "NOT found" in failed[0].evidence


def test_invoice_totals_is_not_fooled_by_a_superstring() -> None:
    """A total of 100 must not "match" a summary that only mentions 1,000."""
    extraction = _span("extraction", {"entities": [{"value": "$100", "entity_type": "total"}]})
    summary = _span("summarization", {"summary": "The total is $1,000.", "key_points": []})

    assert len(_failed(check_invoice_totals(summary, _trace(extraction, summary), ""))) == 1


# ─── Contract ───────────────────────────────────────────────────────────────


def test_contract_parties_needs_two() -> None:
    two = _span(
        "extraction",
        {
            "entities": [
                {"value": "Acme Corp", "entity_type": "organization"},
                {"value": "Beta LLC", "entity_type": "organization"},
            ]
        },
    )
    one = _span("extraction", {"entities": [{"value": "Acme Corp", "entity_type": "organization"}]})

    assert _failed(check_contract_parties(two, _trace(), "")) == []
    assert "only 1 party" in _failed(check_contract_parties(one, _trace(), ""))[0].evidence


def test_contract_dates_needs_effective_and_expiration() -> None:
    both = _span(
        "extraction",
        {
            "entities": [
                {"value": "2024-01-15", "entity_type": "effective_date"},
                {"value": "2025-01-14", "entity_type": "expiration_date"},
            ]
        },
    )
    only_effective = _span(
        "extraction", {"entities": [{"value": "2024-01-15", "entity_type": "effective_date"}]}
    )

    assert _failed(check_contract_dates(both, _trace(), "")) == []
    assert (
        "missing expiration"
        in _failed(check_contract_dates(only_effective, _trace(), ""))[0].evidence
    )


# ─── Report / correspondence / mixed ────────────────────────────────────────


def test_report_structure_checks_all_four_sections() -> None:
    complete = _span(
        "summarization",
        {
            "summary": (
                "Executive summary: the analysis found three issues and recommends "
                "the following approach."
            ),
            "key_points": ["Findings", "Recommendations", "Methodology"],
        },
    )
    incomplete = _span(
        "summarization", {"summary": "Executive summary: the findings were good.", "key_points": []}
    )

    assert _failed(check_report_structure(complete, _trace(), "")) == []
    missing = " ".join(
        r.evidence for r in _failed(check_report_structure(incomplete, _trace(), ""))
    )
    assert "MISSING recommendations section" in missing
    assert "MISSING methodology section" in missing


def test_correspondence_metadata_flags_the_missing_field() -> None:
    complete = _span(
        "extraction",
        {
            "entities": [
                {"value": "alice", "entity_type": "sender"},
                {"value": "bob", "entity_type": "recipient"},
                {"value": "2024-01-15", "entity_type": "date"},
                {"value": "Renewal", "entity_type": "subject"},
            ]
        },
    )
    missing_subject = _span(
        "extraction",
        {
            "entities": [
                {"value": "alice", "entity_type": "sender"},
                {"value": "bob", "entity_type": "recipient"},
                {"value": "2024-01-15", "entity_type": "date"},
            ]
        },
    )

    assert _failed(check_correspondence_metadata(complete, _trace(), "")) == []
    assert (
        "subject"
        in _failed(check_correspondence_metadata(missing_subject, _trace(), ""))[0].evidence
    )


def test_mixed_consistency_needs_two_content_types() -> None:
    mixed_extraction = _span(
        "extraction",
        {
            "entities": [
                {"value": "Acme", "entity_type": "party"},
                {"value": "Invoice", "entity_type": "invoice_number"},
            ]
        },
    )
    single_extraction = _span(
        "extraction", {"entities": [{"value": "Acme", "entity_type": "party"}]}
    )
    classification = _span("classification", {"document_type": "mixed"})

    assert (
        _failed(
            check_mixed_consistency(classification, _trace(classification, mixed_extraction), "")
        )
        == []
    )
    assert (
        len(
            _failed(
                check_mixed_consistency(
                    classification, _trace(classification, single_extraction), ""
                )
            )
        )
        == 1
    )


def test_mixed_consistency_ignores_non_mixed_documents() -> None:
    classification = _span("classification", {"document_type": "invoice"})

    assert check_mixed_consistency(classification, _trace(classification), "") == []


# ─── The registry contract each check must honour ───────────────────────────


CHECKS = [
    (check_pii_in_summary, "summarization"),
    (check_pii_in_extraction, "extraction"),
    (check_financial_amounts, "extraction"),
    (check_invoice_totals, "summarization"),
    (check_contract_parties, "extraction"),
    (check_contract_dates, "extraction"),
    (check_report_structure, "summarization"),
    (check_correspondence_metadata, "extraction"),
    (check_mixed_consistency, "classification"),
]


@pytest.mark.parametrize(("check", "step"), CHECKS)
def test_each_check_is_silent_on_another_step(check, step: str) -> None:
    """A check registered for one step must not judge every span."""
    other = "intake" if step != "intake" else "classification"

    assert (
        check(
            _span(other, {"summary": "x", "entities": [], "document_type": "mixed"}), _trace(), ""
        )
        == []
    )


def test_every_registered_name_has_a_documented_check() -> None:
    """CUSTOM_CHECK_NAMES must match what the decorators actually register."""
    custom_checks.register_custom_checks()

    for name in custom_checks.CUSTOM_CHECK_NAMES:
        assert get_check(name) is not None, f"{name} is listed but not registered"
    assert len(custom_checks.CUSTOM_CHECK_NAMES) == len(CHECKS)


@pytest.mark.parametrize(("check", "step"), CHECKS)
def test_each_check_survives_unparseable_output(check, step: str) -> None:
    """Model output is untrusted: a malformed span must not crash the analyzer."""
    broken = Span(
        trace_id="t",
        step_name=step,
        input_json="{}",
        output_json="{not json",
        model_id="m",
        provider="p",
        prompt="",
        raw_response="",
        status="success",
    )

    assert check(broken, _trace(broken), "") == []


@pytest.mark.parametrize(("check", "step"), CHECKS)
def test_each_check_survives_wrongly_typed_fields(check, step: str) -> None:
    """`entities: "nope"` and `summary: 5` are shapes a model does return."""
    wrong = _span(
        step, {"entities": "nope", "summary": 5, "key_points": None, "document_type": None}
    )

    assert isinstance(check(wrong, _trace(wrong), ""), list)


def test_correspondence_metadata_is_not_fooled_by_a_substring() -> None:
    """`total` contains "to": it must not count as a recipient.

    The check matched entity types with bare `in`, so an invoice's `total_amount`
    satisfied the recipient requirement and a letter with no recipient passed.
    """
    span = _span(
        "extraction",
        {
            "entities": [
                {"value": "alice", "entity_type": "sender"},
                {"value": "$100", "entity_type": "total_amount"},
                {"value": "2024-01-15", "entity_type": "date"},
                {"value": "Renewal", "entity_type": "subject"},
            ]
        },
    )

    failed = _failed(check_correspondence_metadata(span, _trace(), ""))

    assert len(failed) == 1
    assert "recipient" in failed[0].evidence
