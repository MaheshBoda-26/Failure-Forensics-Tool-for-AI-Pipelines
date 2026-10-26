"""Custom check plugins for domain-specific validation.

These plugins demonstrate how to extend the deterministic analyzer with
domain-specific checks. ``PLUGIN_SPEC`` is the value to give ``CHECK_PLUGINS``
(get it with `python -c "from forensics.custom_checks import PLUGIN_SPEC; print(PLUGIN_SPEC)"`):

    CHECK_PLUGINS=<paste PLUGIN_SPEC here>

It lists every decorated check, so loading does not depend on the module having
been imported first. Naming an undecorated function (including
``register_custom_checks``) is rejected by the loader — it is not a plugin, and
a spec that only *looks* like one fails at startup with "is not a @check_plugin".
"""

import re
from decimal import Decimal, InvalidOperation

from forensics.checks import CheckResult, check_plugin
from forensics.models import Span, Trace

#: Every check this module defines, in registration order.
CUSTOM_CHECK_NAMES = [
    "check_pii_in_summary",
    "check_pii_in_extraction",
    "check_financial_amounts",
    "check_invoice_totals",
    "check_contract_parties",
    "check_contract_dates",
    "check_report_structure",
    "check_correspondence_metadata",
    "check_mixed_consistency",
]

#: The ``CHECK_PLUGINS`` value that enables this module.
#:
#: One spec per check, deliberately: ``load_plugins_from_config`` registers only
#: the attribute it is given, so a single-entry spec would rely on the module's
#: import having registered the other eight — true on a fresh process, false for
#: any caller that rebuilt the registry. Listing all nine makes loading
#: deterministic instead of an import-order accident.
PLUGIN_SPEC = ",".join(f"{__name__}:{name}" for name in CUSTOM_CHECK_NAMES)

# ============================================================================
# PII Detection Checks
# ============================================================================


@check_plugin(
    "check_pii_in_summary",
    step="summarization",
    description="Detect PII leaked into summarization output (emails, phones, SSNs, API keys)",
)
def check_pii_in_summary(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    """Check that summarization doesn't leak PII from the source document."""
    import json

    results: list[CheckResult] = []

    if span.step_name != "summarization":
        return results

    try:
        output_data = json.loads(span.output_json) if span.output_json else {}
    except (json.JSONDecodeError, TypeError):
        return results

    summary = output_data.get("summary", "")
    key_points = output_data.get("key_points", [])
    searchable = f"{summary} {' '.join(key_points) if isinstance(key_points, list) else ''}"

    if not searchable.strip():
        return results

    # PII patterns to detect
    pii_patterns = {
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "phone_us": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        "api_key": r"\b(?:sk-|api_|key_)[A-Za-z0-9_-]{20,}\b",
    }

    for pii_type, pattern in pii_patterns.items():
        matches = re.findall(pattern, searchable)
        if matches:
            results.append(
                CheckResult(
                    check_name="pii_in_summary",
                    passed=False,
                    evidence=(
                        f"PII detected in summary: {pii_type} found ({len(matches)} occurrence(s))"
                    ),
                    step_name=span.step_name,
                )
            )
        else:
            results.append(
                CheckResult(
                    check_name="pii_in_summary",
                    passed=True,
                    evidence=f"No {pii_type} found in summary",
                    step_name=span.step_name,
                )
            )

    return results


@check_plugin(
    "check_pii_in_extraction",
    step="extraction",
    description="Detect PII extracted as entities when it shouldn't be (e.g., API keys, passwords)",
)
def check_pii_in_extraction(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    """Check that extraction doesn't extract sensitive PII as entities."""
    import json

    results: list[CheckResult] = []

    if span.step_name != "extraction":
        return results

    try:
        output_data = json.loads(span.output_json) if span.output_json else {}
    except (json.JSONDecodeError, TypeError):
        return results

    entities = output_data.get("entities", [])
    if not isinstance(entities, list):
        return results

    sensitive_types = {
        "api_key",
        "password",
        "secret",
        "token",
        "private_key",
        "ssn",
        "credit_card",
    }

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_type = entity.get("entity_type", "").lower()
        entity_value = entity.get("value", "")

        # Check if entity type is explicitly sensitive
        if any(st in entity_type for st in sensitive_types):
            results.append(
                CheckResult(
                    check_name="pii_in_extraction",
                    passed=False,
                    evidence=(
                        f"Sensitive entity type extracted: '{entity_type}' = "
                        f"'{entity_value[:20]}...'"
                    ),
                    step_name=span.step_name,
                )
            )
        # Check if value looks like sensitive data regardless of type
        elif re.search(r"\b(?:sk-|api_|key_)[A-Za-z0-9_-]{20,}\b", entity_value):
            results.append(
                CheckResult(
                    check_name="pii_in_extraction",
                    passed=False,
                    evidence=f"API key pattern found in entity value: '{entity_value[:20]}...'",
                    step_name=span.step_name,
                )
            )
        elif re.search(r"\b\d{3}-\d{2}-\d{4}\b", entity_value):
            results.append(
                CheckResult(
                    check_name="pii_in_extraction",
                    passed=False,
                    evidence=f"SSN pattern found in entity value: '{entity_value}'",
                    step_name=span.step_name,
                )
            )

    if not results:
        results.append(
            CheckResult(
                check_name="pii_in_extraction",
                passed=True,
                evidence="No sensitive PII entities detected in extraction",
                step_name=span.step_name,
            )
        )

    return results


# ============================================================================
# Financial Validation Checks
# ============================================================================


@check_plugin(
    "check_financial_amounts",
    step="extraction",
    description="Validate that extracted financial amounts are consistent and properly formatted",
)
def check_financial_amounts(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    """Check extracted financial amounts for consistency."""
    import json
    from decimal import Decimal, InvalidOperation

    results: list[CheckResult] = []

    if span.step_name != "extraction":
        return results

    try:
        output_data = json.loads(span.output_json) if span.output_json else {}
    except (json.JSONDecodeError, TypeError):
        return results

    entities = output_data.get("entities", [])
    if not isinstance(entities, list):
        return results

    amounts = []
    currencies = set()

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_type = entity.get("entity_type", "").lower()
        value = entity.get("value", "")

        if "currency" in entity_type:
            # A currency entity carries a symbol or code ("$", "USD"), not an
            # amount. "currency" used to be in the amount-type list below, so a
            # plain "$" was fed to Decimal() and every invoice that extracted a
            # currency was flagged "Unparseable financial amount".
            if value:
                currencies.add(value.strip())
            continue

        if any(
            t in entity_type
            for t in ["amount", "money", "total", "price", "cost", "subtotal", "tax"]
        ):
            # Normalize amount string
            normalized = (
                value.replace(",", "").replace("$", "").replace("€", "").replace("£", "").strip()
            )

            # Extract currency symbol
            currency_match = re.search(r"^([$€£¥])", value)
            if currency_match:
                currencies.add(currency_match.group(1))

            # Try to parse as decimal
            try:
                amount = Decimal(normalized)
                amounts.append((amount, entity.get("entity_type", "unknown"), value))
            except (InvalidOperation, ValueError):
                results.append(
                    CheckResult(
                        check_name="financial_amounts",
                        passed=False,
                        evidence=(
                            f"Unparseable financial amount: '{value}' "
                            f"(type: {entity.get('entity_type')})"
                        ),
                        step_name=span.step_name,
                    )
                )

    # Check for consistency if multiple amounts
    if len(amounts) > 1:
        # Check if subtotal + tax ≈ total (common pattern)
        subtotals = [a for a, t, _ in amounts if "subtotal" in t.lower()]
        taxes = [a for a, t, _ in amounts if "tax" in t.lower()]
        totals = [a for a, t, _ in amounts if "total" in t.lower() and "sub" not in t.lower()]

        if subtotals and taxes and totals:
            expected_total = sum(subtotals) + sum(taxes)
            actual_total = totals[0]
            if abs(expected_total - actual_total) > Decimal("0.01"):
                results.append(
                    CheckResult(
                        check_name="financial_amounts",
                        passed=False,
                        evidence=(
                            f"Financial inconsistency: subtotal+tax "
                            f"({expected_total}) != total ({actual_total})"
                        ),
                        step_name=span.step_name,
                    )
                )
            else:
                results.append(
                    CheckResult(
                        check_name="financial_amounts",
                        passed=True,
                        evidence=(
                            f"Financial amounts consistent: subtotal+tax = total ({actual_total})"
                        ),
                        step_name=span.step_name,
                    )
                )

        # Check for duplicate amounts with different labels
        seen = {}
        for amount, etype, orig in amounts:
            key = (amount, etype)
            if key in seen:
                results.append(
                    CheckResult(
                        check_name="financial_amounts",
                        passed=False,
                        evidence=(
                            f"Duplicate amount with same label: {amount} "
                            f"({etype}) appears multiple times"
                        ),
                        step_name=span.step_name,
                    )
                )
            seen[key] = orig

    if not any(not r.passed for r in results):
        results.append(
            CheckResult(
                check_name="financial_amounts",
                passed=True,
                evidence=f"All {len(amounts)} financial amounts parsed successfully",
                step_name=span.step_name,
            )
        )

    return results


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _amounts_mentioned(text: str) -> set[Decimal]:
    """Every number in `text`, parsed the way the amounts were parsed.

    Commas are thousands separators in prose ("$10,000.00") but not in a
    `Decimal`, so both sides are normalised to the same representation before
    comparison.
    """
    found: set[Decimal] = set()
    for match in _NUMBER.findall(text):
        try:
            found.add(Decimal(match.replace(",", "")))
        except InvalidOperation:
            continue
    return found


@check_plugin(
    "check_invoice_totals",
    step="summarization",
    description="Verify invoice summary mentions the correct total amount",
)
def check_invoice_totals(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    """Check that invoice summary correctly references the total amount."""
    import json
    from decimal import Decimal, InvalidOperation

    results: list[CheckResult] = []

    if span.step_name != "summarization":
        return results

    # Find the invoice total from extraction
    extraction_span = next((s for s in trace.spans if s.step_name == "extraction"), None)
    if not extraction_span:
        return results

    try:
        extraction_data = (
            json.loads(extraction_span.output_json) if extraction_span.output_json else {}
        )
        summary_data = json.loads(span.output_json) if span.output_json else {}
    except (json.JSONDecodeError, TypeError):
        return results

    # Find total amount from extraction
    total_amount = None
    entities = extraction_data.get("entities", [])
    if isinstance(entities, list):
        for entity in entities:
            if isinstance(entity, dict) and entity.get("entity_type", "").lower() in (
                "total",
                "amount",
                "money",
            ):
                try:
                    val = entity.get("value", "").replace(",", "").replace("$", "").strip()
                    total_amount = Decimal(val)
                    break
                except (InvalidOperation, ValueError):
                    continue

    # `Decimal("0")` is falsy, so a zero total was silently skipped instead of
    # checked.
    if total_amount is None:
        return results

    # Check summary for the amount
    summary = summary_data.get("summary", "")
    key_points = summary_data.get("key_points", [])
    searchable = f"{summary} {' '.join(key_points) if isinstance(key_points, list) else ''}"

    # Numeric comparison, not a substring one. `str(Decimal("10000.00"))` is
    # "10000.00", so a correctly written "$10,000.00" summary was reported as an
    # omission; comparing raw substrings also let a total of 100 "match" 1,000.
    if total_amount in _amounts_mentioned(searchable):
        results.append(
            CheckResult(
                check_name="invoice_totals",
                passed=True,
                evidence=f"Invoice total ${total_amount} correctly referenced in summary",
                step_name=span.step_name,
            )
        )
    else:
        results.append(
            CheckResult(
                check_name="invoice_totals",
                passed=False,
                evidence=(
                    f"Invoice total ${total_amount} NOT found in summary "
                    "(possible omission or misreporting)"
                ),
                step_name=span.step_name,
            )
        )

    return results


# ============================================================================
# Contract/Legal Checks
# ============================================================================


@check_plugin(
    "check_contract_parties",
    step="extraction",
    description="Verify contract extraction identifies both parties",
)
def check_contract_parties(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    """Check that contract extraction identifies at least two parties."""
    import json

    results: list[CheckResult] = []

    if span.step_name != "extraction":
        return results

    try:
        output_data = json.loads(span.output_json) if span.output_json else {}
    except (json.JSONDecodeError, TypeError):
        return results

    entities = output_data.get("entities", [])
    if not isinstance(entities, list):
        return results

    parties = []
    orgs = []

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_type = entity.get("entity_type", "").lower()
        value = entity.get("value", "")

        if (
            "party" in entity_type
            or "organization" in entity_type
            or "company" in entity_type
            or "entity" in entity_type
        ):
            orgs.append(value)
        elif "person" in entity_type or "name" in entity_type or "signatory" in entity_type:
            parties.append(value)

    all_parties = parties + orgs
    unique_parties = set(all_parties)

    if len(unique_parties) >= 2:
        results.append(
            CheckResult(
                check_name="contract_parties",
                passed=True,
                evidence=(
                    f"Contract identifies {len(unique_parties)} parties: "
                    f"{', '.join(list(unique_parties)[:3])}"
                ),
                step_name=span.step_name,
            )
        )
    elif len(unique_parties) == 1:
        results.append(
            CheckResult(
                check_name="contract_parties",
                passed=False,
                evidence=(
                    f"Contract identifies only 1 party: "
                    f"{next(iter(unique_parties))} (contracts typically need 2+)"
                ),
                step_name=span.step_name,
            )
        )
    else:
        results.append(
            CheckResult(
                check_name="contract_parties",
                passed=False,
                evidence="No parties identified in contract extraction",
                step_name=span.step_name,
            )
        )

    return results


@check_plugin(
    "check_contract_dates",
    step="extraction",
    description="Verify contract extraction includes effective and expiration dates",
)
def check_contract_dates(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    """Check that contract extraction identifies key dates."""
    import json

    results: list[CheckResult] = []

    if span.step_name != "extraction":
        return results

    try:
        output_data = json.loads(span.output_json) if span.output_json else {}
    except (json.JSONDecodeError, TypeError):
        return results

    entities = output_data.get("entities", [])
    if not isinstance(entities, list):
        return results

    dates = {}
    date_types = {
        "effective": ["effective", "start", "commencement", "begin"],
        "expiration": ["expir", "end", "terminat", "expiry"],
        "signature": ["sign", "executed", "dated"],
    }

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_type = entity.get("entity_type", "").lower()
        value = entity.get("value", "")

        if "date" in entity_type:
            for dtype, keywords in date_types.items():
                if any(kw in entity_type for kw in keywords):
                    dates[dtype] = value
                    break
            else:
                dates.setdefault("other", []).append((entity_type, value))

    if "effective" in dates and "expiration" in dates:
        results.append(
            CheckResult(
                check_name="contract_dates",
                passed=True,
                evidence=(
                    f"Contract has both effective ({dates['effective']}) and "
                    f"expiration ({dates['expiration']}) dates"
                ),
                step_name=span.step_name,
            )
        )
    elif "effective" in dates or "expiration" in dates:
        missing = "expiration" if "effective" in dates else "effective"
        results.append(
            CheckResult(
                check_name="contract_dates",
                passed=False,
                evidence=f"Contract missing {missing} date (has {list(dates.keys())})",
                step_name=span.step_name,
            )
        )
    else:
        results.append(
            CheckResult(
                check_name="contract_dates",
                passed=False,
                evidence="No contract dates identified in extraction",
                step_name=span.step_name,
            )
        )

    return results


# ============================================================================
# Report Quality Checks
# ============================================================================


@check_plugin(
    "check_report_structure",
    step="summarization",
    description=(
        "Verify report summary includes required sections (findings, recommendations, methodology)"
    ),
)
def check_report_structure(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    """Check that report summary includes expected structural elements."""
    import json

    results: list[CheckResult] = []

    if span.step_name != "summarization":
        return results

    try:
        output_data = json.loads(span.output_json) if span.output_json else {}
    except (json.JSONDecodeError, TypeError):
        return results

    summary = output_data.get("summary", "")
    key_points = output_data.get("key_points", [])
    searchable = f"{summary} {' '.join(key_points) if isinstance(key_points, list) else ''}".lower()

    required_sections = {
        "findings": ["finding", "result", "observation", "discover"],
        "recommendations": ["recommend", "suggest", "propose", "should", "must"],
        "methodology": ["method", "approach", "analy", "evaluat", "assess"],
    }

    for section, keywords in required_sections.items():
        found = any(kw in searchable for kw in keywords)
        results.append(
            CheckResult(
                check_name="report_structure",
                passed=found,
                evidence=f"Report {'includes' if found else 'MISSING'} {section} section",
                step_name=span.step_name,
            )
        )

    # Check for executive summary indicator
    has_exec_summary = any(kw in searchable for kw in ["executive summary", "overview", "summary"])
    results.append(
        CheckResult(
            check_name="report_structure",
            passed=has_exec_summary,
            evidence=(
                f"Report {'has' if has_exec_summary else 'missing'} executive summary/overview"
            ),
            step_name=span.step_name,
        )
    )

    return results


# ============================================================================
# Correspondence Checks
# ============================================================================


def _entity_type_has(entity_type: str, *keywords: str) -> bool:
    """Whether `entity_type` names one of `keywords` as a whole word.

    Bare substring matching made `total` count as a correspondence *recipient*
    (it contains "to") and would make `token` a sender. Entity types are
    snake_case, so splitting on non-alphanumerics yields the words.
    """
    parts = set(re.split(r"[^a-z0-9]+", entity_type.lower()))
    return any(keyword in parts for keyword in keywords)


@check_plugin(
    "check_correspondence_metadata",
    step="extraction",
    description="Verify correspondence extraction captures sender, recipient, date, subject",
)
def check_correspondence_metadata(
    span: Span, trace: Trace, document_text: str
) -> list[CheckResult]:
    """Check that correspondence extraction captures key metadata."""
    import json

    results: list[CheckResult] = []

    if span.step_name != "extraction":
        return results

    try:
        output_data = json.loads(span.output_json) if span.output_json else {}
    except (json.JSONDecodeError, TypeError):
        return results

    entities = output_data.get("entities", [])
    if not isinstance(entities, list):
        return results

    metadata = {
        "sender": False,
        "recipient": False,
        "date": False,
        "subject": False,
    }

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_type = entity.get("entity_type", "").lower()

        if _entity_type_has(entity_type, "from", "sender", "author"):
            metadata["sender"] = True
        elif _entity_type_has(entity_type, "to", "recipient", "addressee"):
            metadata["recipient"] = True
        elif _entity_type_has(entity_type, "date"):
            metadata["date"] = True
        elif _entity_type_has(entity_type, "subject"):
            metadata["subject"] = True

    missing = [k for k, v in metadata.items() if not v]
    present = [k for k, v in metadata.items() if v]

    if not missing:
        results.append(
            CheckResult(
                check_name="correspondence_metadata",
                passed=True,
                evidence=f"All correspondence metadata present: {', '.join(present)}",
                step_name=span.step_name,
            )
        )
    else:
        results.append(
            CheckResult(
                check_name="correspondence_metadata",
                passed=False,
                evidence=(
                    f"Missing correspondence metadata: {', '.join(missing)} "
                    f"(present: {', '.join(present)})"
                ),
                step_name=span.step_name,
            )
        )

    return results


# ============================================================================
# Mixed Document Checks
# ============================================================================


@check_plugin(
    "check_mixed_consistency",
    step="classification",
    description="Verify mixed document classification is consistent with extraction content",
)
def check_mixed_consistency(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    """Check that mixed document classification matches multi-type content."""
    import json

    results: list[CheckResult] = []

    if span.step_name != "classification":
        return results

    try:
        class_data = json.loads(span.output_json) if span.output_json else {}
    except (json.JSONDecodeError, TypeError):
        return results

    doc_type = class_data.get("document_type", "")

    if doc_type != "mixed":
        return results

    # Check extraction for multiple entity types suggesting mixed content
    extraction_span = next((s for s in trace.spans if s.step_name == "extraction"), None)
    if not extraction_span:
        return results

    try:
        ext_data = json.loads(extraction_span.output_json) if extraction_span.output_json else {}
    except (json.JSONDecodeError, TypeError):
        return results

    entities = ext_data.get("entities", [])
    if not isinstance(entities, list):
        return results

    entity_types = set()
    for entity in entities:
        if isinstance(entity, dict):
            entity_types.add(entity.get("entity_type", "").lower())

    # Check for indicators of multiple document types
    type_indicators = {
        "contract": {"party", "clause", "agreement", "indemnif", "termination"},
        "invoice": {"invoice", "receipt", "subtotal", "amount due", "payment terms"},
        "report": {"finding", "methodology", "executive summary", "analysis"},
        "correspondence": {"dear", "sincerely", "regards", "from:", "to:"},
    }

    detected_types = set()
    for entity in entities:
        if isinstance(entity, dict):
            etype = entity.get("entity_type", "").lower()
            value = entity.get("value", "").lower()
            for dtype, indicators in type_indicators.items():
                if any(ind in etype or ind in value for ind in indicators):
                    detected_types.add(dtype)

    if len(detected_types) >= 2:
        results.append(
            CheckResult(
                check_name="mixed_consistency",
                passed=True,
                evidence=(
                    "Mixed classification consistent with content: detected "
                    f"{', '.join(sorted(detected_types))}"
                ),
                step_name=span.step_name,
            )
        )
    else:
        results.append(
            CheckResult(
                check_name="mixed_consistency",
                passed=False,
                evidence=(
                    "Mixed classification but content suggests single type: "
                    f"{', '.join(sorted(detected_types)) or 'none'}"
                ),
                step_name=span.step_name,
            )
        )

    return results


# ============================================================================
# Registry Function
# ============================================================================


def register_custom_checks() -> None:
    """Register all nine custom checks. Idempotent; safe to call more than once.

    Importing this module already registers them via the decorators, so this is
    a no-op in the common case. It exists for the cases where the registry was
    rebuilt after the import (a test fixture, a host that resets the registry),
    and it *actually* registers rather than relying on import side effects — a
    function named ``register_*`` that registers nothing is a trap.

    It deliberately does *not* work as a ``CHECK_PLUGINS`` value: that setting
    takes ``module:attr`` where ``attr`` is a ``@check_plugin``, and this is a
    plain function. Use ``PLUGIN_SPEC`` instead.
    """
    from forensics.checks import load_plugins_from_config

    # Every name is a decorated check, so the loader can coerce it. Registering an
    # already-present name is a no-op inside the loader, which is what makes a
    # second call safe.
    load_plugins_from_config(PLUGIN_SPEC)


__all__ = ["PLUGIN_SPEC", "register_custom_checks", *CUSTOM_CHECK_NAMES]
