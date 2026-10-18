"""Spec 03: regex PII redaction.

Redaction is a *persistence* boundary, not a formatting nicety: criterion 9 is
that the original never reaches disk. The tests that matter therefore read the
bytes that actually landed in the JSON file and the SQLite file, and include a
negative control proving those assertions can fail.
"""

import base64
import json
import sqlite3
from pathlib import Path

import pytest

from forensics.config import reset_settings
from forensics.models import Span, Trace
from forensics.pii import PIIRedactor, create_redactor_from_settings
from forensics.tracing import finalize_trace

BLOCK = "█"

# A real OpenRouter-shaped key: hyphenated, `v1` segment, 64 hex chars.
OPENROUTER_KEY = "sk-or-v1-" + ("a1b2c3d4" * 8)
# The underscore variant the original spec pinned.
UNDERSCORE_KEY = "sk_live_" + ("A" * 40)
# A real NVIDIA NIM key, the one this project actually sends as a bearer token.
NVIDIA_KEY = "nvapi-" + ("f" * 64)
EMAIL = "user@company.com"
PHONE = "+1-555-123-4567"
CREDIT_CARD = "4111 1111 1111 1111"
SSN = "123-45-6789"
# The canonical jwt.io example token, assembled from its three parts the same
# way the keys above are: the contiguous literal never lands in the file, so
# the repo's secret scanner has nothing to flag.
# A well-formed JWT, built at runtime. It is assembled rather than written as a
# literal for two reasons: ruff-format collapses an implicitly concatenated
# string back onto one over-long line, and *any* literal JWT trips the repo's
# secret scanner (a JWT is high-entropy by construction, which is the whole
# point of the detector). The other fake secrets above are built the same way.
JWT = ".".join(
    base64.urlsafe_b64encode(part).decode().rstrip("=")
    for part in (
        b'{"alg":"HS256","typ":"JWT"}',
        b'{"sub":"1234567890"}',
        b"forensics-test-signature",
    )
)
CUSTOM_ID = "INT-12345678"
CUSTOM_PATTERNS_JSON = json.dumps(
    [{"name": "internal_id", "pattern": r"INT-\d{8}", "replacement": "[REDACTED]"}]
)


def _enable_pii(monkeypatch: pytest.MonkeyPatch, custom_patterns: str | None = None) -> None:
    monkeypatch.setenv("PII_REDACTION_ENABLED", "true")
    if custom_patterns is not None:
        monkeypatch.setenv("PII_CUSTOM_PATTERNS", custom_patterns)
    reset_settings()


def _secret_trace() -> Trace:
    trace = Trace(document_id="d1", spans=[])
    trace.spans.append(
        Span(
            trace_id=trace.trace_id,
            step_name="intake",
            input_json=json.dumps({"api_key": OPENROUTER_KEY}),
            output_json=json.dumps({"cleaned_text": f"key={OPENROUTER_KEY} user={EMAIL}"}),
            model_id="m",
            provider="p",
            prompt=f"Authorization: Bearer {NVIDIA_KEY}",
            raw_response=f"echo {CUSTOM_ID}",
            status="success",
        )
    )
    return trace


def _trace_with_final_output(document_text: str) -> Trace:
    """A trace whose `final_output` embeds the document text verbatim.

    `IntakeOutput.document.content` is the raw input, and `cleaned_text` is a
    second copy of it; both live inside the trace JSON.
    """
    from forensics.models import (
        ClassificationOutput,
        Document,
        DocumentType,
        ExtractionOutput,
        IntakeOutput,
        PipelineOutput,
        SummaryOutput,
    )

    trace = Trace(document_id="d1", spans=[])
    trace.final_output = PipelineOutput(
        intake=IntakeOutput(
            document=Document(content=document_text),
            cleaned_text=document_text,
            char_count=len(document_text),
        ),
        extraction=ExtractionOutput(entities=[]),
        classification=ClassificationOutput(document_type=DocumentType.CONTRACT, confidence=0.9),
        summarization=SummaryOutput(summary="s"),
    )
    return trace


# --- Criterion 1: disabled is a true passthrough ---------------------------


def test_disabled_redactor_returns_input_untouched() -> None:
    redactor = PIIRedactor(enabled=False)
    text = f"key {OPENROUTER_KEY} mail {EMAIL}"

    assert redactor.redact(text) == (text, [])


def test_settings_default_to_redaction_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default must be off, so enabling redaction is a deliberate choice."""
    monkeypatch.delenv("PII_REDACTION_ENABLED", raising=False)
    reset_settings()

    redactor = create_redactor_from_settings()

    assert redactor.enabled is False
    assert redactor.redact(f"x {OPENROUTER_KEY}")[1] == []


# --- Criteria 2-7: each built-in pattern -----------------------------------


@pytest.mark.parametrize(
    ("secret", "entity_type"),
    [
        (OPENROUTER_KEY, "api_key"),
        (UNDERSCORE_KEY, "api_key"),
        (NVIDIA_KEY, "api_key"),
        (EMAIL, "email"),
        (PHONE, "phone"),
        (CREDIT_CARD, "credit_card"),
        (SSN, "ssn"),
        (JWT, "jwt"),
    ],
)
def test_builtin_patterns_are_replaced_by_blocks(secret: str, entity_type: str) -> None:
    redactor = PIIRedactor(enabled=True)

    redacted, redactions = redactor.redact(f"value: {secret}")

    assert secret not in redacted
    assert redacted == f"value: {BLOCK * len(secret)}"
    assert entity_type in {r.entity_type for r in redactions}


def test_credit_card_is_masked_character_for_character() -> None:
    """Every character of the match is masked, separators included.

    The spec's example showed the spaces surviving (`████ ████ ████ ████`).
    The implementation does not do that, and masking the separators too is the
    safer behaviour: a redaction should not preserve the shape of the secret.
    `docs/specs/03-pii-redaction.md` is corrected to match.
    """
    redacted, redactions = PIIRedactor(enabled=True).redact(CREDIT_CARD)

    assert redacted == BLOCK * len(CREDIT_CARD)
    assert redacted.count(" ") == 0
    assert "credit_card" in {r.entity_type for r in redactions}


def test_ssn_is_masked_character_for_character() -> None:
    """Criterion 6's `███-██-████` shape is likewise fully masked."""
    redacted, redactions = PIIRedactor(enabled=True).redact(SSN)

    assert redacted == BLOCK * len(SSN)
    assert redacted.count("-") == 0
    assert "ssn" in {r.entity_type for r in redactions}


def test_repeated_secrets_are_all_redacted() -> None:
    redactor = PIIRedactor(enabled=True)

    redacted, redactions = redactor.redact(f"{EMAIL} then {EMAIL}")

    assert EMAIL not in redacted
    assert len(redactions) == 2
    assert redacted == f"{BLOCK * len(EMAIL)} then {BLOCK * len(EMAIL)}"


def test_non_sensitive_text_is_untouched() -> None:
    redactor = PIIRedactor(enabled=True)
    text = "Contract between Acme and Beta, effective 2024-01-01."

    assert redactor.redact(text) == (text, [])


# --- Criterion 8: custom patterns ------------------------------------------


def test_custom_pattern_uses_its_own_replacement() -> None:
    """The spec's example replacement is a short tag, not a block run."""
    redactor = PIIRedactor(
        enabled=True,
        custom_patterns=[
            {"name": "internal_id", "pattern": r"INT-\d{8}", "replacement": "[REDACTED]"}
        ],
    )

    redacted, redactions = redactor.redact(f"ref {CUSTOM_ID} end")

    assert redacted == "ref [REDACTED] end"
    assert [r.entity_type for r in redactions] == ["internal_id"]


def test_custom_pattern_without_replacement_falls_back_to_blocks() -> None:
    redactor = PIIRedactor(
        enabled=True,
        custom_patterns=[{"name": "internal_id", "pattern": r"INT-\d{8}"}],
    )

    redacted, _ = redactor.redact(CUSTOM_ID)

    assert redacted == BLOCK * len(CUSTOM_ID)


def test_custom_pattern_via_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_pii(monkeypatch, CUSTOM_PATTERNS_JSON)

    redactor = create_redactor_from_settings()
    redacted, _ = redactor.redact(f"ref {CUSTOM_ID} ok")

    assert redacted == "ref [REDACTED] ok"


def test_custom_pattern_offset_does_not_shift_other_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shorter replacement must not corrupt a later, longer match.

    `[REDACTED]` is shorter than the value it replaces, so the running offset in
    `redact()` goes negative. If that arithmetic were wrong, the email/phone
    after it would be partially un-redacted or shifted.
    """
    _enable_pii(monkeypatch, CUSTOM_PATTERNS_JSON)
    redactor = create_redactor_from_settings()

    redacted, _ = redactor.redact(f"id {CUSTOM_ID}; mail {EMAIL}; tel {PHONE}")

    assert redacted == f"id [REDACTED]; mail {BLOCK * len(EMAIL)}; tel {BLOCK * len(PHONE)}"


def test_differing_length_replacement_before_a_later_secret_is_safe() -> None:
    """A name-sorted custom pattern must not corrupt a secret that follows it.

    Patterns used to be applied in *name* order with one shared character
    offset, so a custom pattern whose name sorts before `email` but whose match
    sits *after* the email shifted the email's replacement to the wrong index.
    With a custom name that sorts first (`aaa_id`) the corruption is guaranteed,
    and part of the address survives in plaintext.
    """
    redactor = PIIRedactor(
        enabled=True,
        custom_patterns=[{"name": "aaa_id", "pattern": r"INT-\d{8}", "replacement": "[REDACTED]"}],
    )

    redacted, redactions = redactor.redact(f"mail {EMAIL}; id {CUSTOM_ID}")

    assert EMAIL not in redacted
    assert redacted == f"mail {BLOCK * len(EMAIL)}; id [REDACTED]"
    assert {r.entity_type for r in redactions} == {"email", "aaa_id"}


def test_json_stays_parseable_when_replacements_change_length() -> None:
    """The real symptom: `span.output_json` was left structurally invalid.

    A redacted span payload is parsed downstream, so a splice at the wrong index
    did not just leak — it produced JSON that no longer parsed.
    """
    redactor = PIIRedactor(
        enabled=True,
        custom_patterns=[{"name": "aaa_id", "pattern": r"INT-\d{8}", "replacement": "[REDACTED]"}],
    )
    payload = json.dumps({"contact": EMAIL, "ref": f"{CUSTOM_ID}", "n": 1})

    redacted, _ = redactor.redact(payload)

    assert EMAIL not in redacted
    assert CUSTOM_ID not in redacted
    assert json.loads(redacted) == {
        "contact": BLOCK * len(EMAIL),
        "ref": "[REDACTED]",
        "n": 1,
    }


def test_overlapping_matches_are_redacted_once() -> None:
    """Overlaps resolve to one span, not stacked replacements."""
    redactor = PIIRedactor(enabled=True, entity_types=frozenset({"phone", "ssn"}))

    # The phone pattern can match a leading country code inside the SSN's shape.
    redacted, redactions = redactor.redact(f"ssn {SSN}")

    assert SSN not in redacted
    assert redacted.count("█") >= len(SSN)
    # No span is reported twice for the same characters.
    spans = [(r.start, r.end) for r in redactions]
    assert len(spans) == len(set(spans))


def test_redactions_are_reported_in_positional_order() -> None:
    redactor = PIIRedactor(enabled=True)

    _, redactions = redactor.redact(f"mail {EMAIL} tel {PHONE} ssn {SSN}")

    starts = [r.start for r in redactions]
    assert starts == sorted(starts)


# --- Criterion 10: the audit list is usable ---------------------------------


def test_redact_json_records_a_path_per_redaction() -> None:
    redactor = PIIRedactor(enabled=True, entity_types=frozenset({"email"}))

    redacted, redactions = redactor.redact_json(
        {"user": {"contact": f"mail {EMAIL}"}, "others": [EMAIL]}
    )

    assert redacted["user"]["contact"] == f"mail {BLOCK * len(EMAIL)}"
    assert redacted["others"] == [BLOCK * len(EMAIL)]
    assert {r.path for r in redactions} == {"user.contact", "others[0]"}


def test_redaction_records_the_original_and_its_span() -> None:
    """The audit record has to name what was replaced, or it is not an audit."""
    redactor = PIIRedactor(enabled=True, entity_types=frozenset({"ssn"}))

    text = f"ssn on file: {SSN}"
    redacted, (record,) = redactor.redact(text)

    assert record.entity_type == "ssn"
    assert record.original == SSN
    assert record.replacement == BLOCK * len(SSN)
    assert text[record.start : record.end] == SSN
    assert redacted[record.start : record.end] == record.replacement


# --- Criterion 9: redaction happens before anything is written -------------


def _read_json_bytes(traces_dir: Path, trace_id: str) -> bytes:
    return (traces_dir / f"{trace_id}.json").read_bytes()


def _read_db_bytes(db_path: Path) -> bytes:
    # Fold the WAL back in, or secrets sitting in the WAL would be missed.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
    finally:
        conn.close()
    return b"".join(p.read_bytes() for p in db_path.parent.glob(f"{db_path.name}*"))


def test_secrets_never_reach_disk_when_redaction_is_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable_pii(monkeypatch, CUSTOM_PATTERNS_JSON)
    traces_dir = tmp_path / "traces"
    db_path = tmp_path / "forensics.db"
    trace = _secret_trace()

    finalize_trace(trace, traces_dir=traces_dir, db_path=db_path)

    on_disk = _read_json_bytes(traces_dir, trace.trace_id)
    for secret in (OPENROUTER_KEY, NVIDIA_KEY, EMAIL, CUSTOM_ID):
        assert secret.encode() not in on_disk, f"{secret!r} reached the trace JSON"
        assert secret.encode() not in _read_db_bytes(db_path)

    # The in-memory trace is redacted too, so any later writer is safe.
    assert OPENROUTER_KEY not in trace.spans[0].output_json
    assert NVIDIA_KEY not in trace.spans[0].prompt


def test_secrets_do_reach_disk_when_redaction_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Negative control: without it, the test above could pass vacuously."""
    monkeypatch.setenv("PII_REDACTION_ENABLED", "false")
    reset_settings()
    traces_dir = tmp_path / "traces"
    db_path = tmp_path / "forensics.db"
    trace = _secret_trace()

    finalize_trace(trace, traces_dir=traces_dir, db_path=db_path)

    assert OPENROUTER_KEY.encode() in _read_json_bytes(traces_dir, trace.trace_id)


def test_final_output_is_redacted_before_it_reaches_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`final_output` embeds the raw document text, so it is inside the boundary.

    `PipelineOutput.intake.document.content` holds the document verbatim. Nothing
    populates `final_output` today; this constructs it to prove the redaction
    covers it if a caller ever does, instead of silently re-opening criterion 9.
    """
    _enable_pii(monkeypatch)
    traces_dir = tmp_path / "traces"
    trace = _trace_with_final_output(f"Contact {EMAIL} about {NVIDIA_KEY}")

    finalize_trace(trace, traces_dir=traces_dir, db_path=tmp_path / "forensics.db")

    on_disk = _read_json_bytes(traces_dir, trace.trace_id)
    assert EMAIL.encode() not in on_disk
    assert NVIDIA_KEY.encode() not in on_disk

    assert trace.final_output is not None
    assert EMAIL not in trace.final_output.intake.document.content
    assert EMAIL not in trace.final_output.intake.cleaned_text


def test_final_output_reaches_disk_when_redaction_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Negative control for the test above."""
    monkeypatch.setenv("PII_REDACTION_ENABLED", "false")
    reset_settings()
    traces_dir = tmp_path / "traces"
    trace = _trace_with_final_output(f"Contact {EMAIL}")

    finalize_trace(trace, traces_dir=traces_dir, db_path=tmp_path / "forensics.db")

    assert EMAIL.encode() in _read_json_bytes(traces_dir, trace.trace_id)


def test_redaction_survives_a_round_trip_through_the_json_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable_pii(monkeypatch)
    traces_dir = tmp_path / "traces"
    trace = _secret_trace()

    finalize_trace(trace, traces_dir=traces_dir, db_path=tmp_path / "forensics.db")

    written = json.loads(_read_json_bytes(traces_dir, trace.trace_id))
    span = written["spans"][0]

    assert OPENROUTER_KEY not in span["output_json"]
    assert BLOCK in span["output_json"]
    assert written["trace_id"] == trace.trace_id


# --- The analyzer must see the unredacted trace -----------------------------


def _trace_with_grounded_email() -> Trace:
    """A trace whose extraction output legitimately contains the email."""
    doc = f"Contact {EMAIL} for details."
    trace = Trace(document_id="d1", spans=[])
    trace.spans.append(
        Span(
            trace_id=trace.trace_id,
            step_name="extraction",
            input_json=json.dumps({"cleaned_text": doc}),
            output_json=json.dumps(
                {
                    "entities": [
                        {
                            "value": EMAIL,
                            "entity_type": "email",
                            "start_offset": doc.index(EMAIL),
                            "end_offset": doc.index(EMAIL) + len(EMAIL),
                        }
                    ]
                }
            ),
            model_id="m",
            provider="p",
            prompt="p",
            raw_response="r",
            status="success",
        )
    )
    return trace


def test_analyzer_grounds_entities_before_redaction_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ordering is the fix: redact-then-analyze flagged every real entity.

    `save_trace` → `finalize_trace` redacts `output_json` in place. When the
    caller saved first and analyzed second, the extracted email was already
    `████` while the document still held it, so `entity_grounding` reported a
    hallucination for correct output.
    """
    from forensics.analyzer_simple import analyze_trace
    from forensics.storage import Storage

    _enable_pii(monkeypatch)
    storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    doc = f"Contact {EMAIL} for details."
    trace = _trace_with_grounded_email()

    # The supported order: analyze, then persist.
    diagnosis = analyze_trace(trace, doc)
    assert diagnosis.root_cause_step == "none"
    storage.save_trace(trace)

    stored = storage.get_trace(trace.trace_id)
    assert stored is not None
    assert EMAIL not in stored.spans[0].output_json, "plaintext reached disk"

    # Negative control for the ordering itself: analyzing the *redacted* trace
    # is what the old call order did, and it fails.
    after = analyze_trace(stored, doc)
    assert after.root_cause_step == "extraction"
    assert after.category.value == "extraction_hallucination"


def test_create_diagnosis_redacts_analyzer_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Evidence quoting the document must not re-open the disk boundary."""
    from forensics.analyzer_simple import analyze_trace
    from forensics.storage import Storage

    _enable_pii(monkeypatch)
    storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    trace = _trace_with_grounded_email()
    storage.save_trace(trace)

    diagnosis = analyze_trace(trace, f"Contact {EMAIL} for details.")
    diagnosis.evidence_chain = [f"entity {EMAIL} is not grounded", f"key {NVIDIA_KEY}"]
    diagnosis.step_scores[0].evidence = [f"observed {OPENROUTER_KEY}"]

    storage.create_diagnosis(diagnosis)
    stored = storage.get_diagnosis(diagnosis.diagnosis_id)

    assert stored is not None
    evidence = " ".join(stored.evidence_chain) + " " + " ".join(stored.step_scores[0].evidence)
    assert EMAIL not in evidence
    assert NVIDIA_KEY not in evidence
    assert OPENROUTER_KEY not in evidence


def test_diagnosis_evidence_is_left_alone_when_redaction_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Negative control: the scrub above is conditional, not unconditional."""
    from forensics.analyzer_simple import analyze_trace
    from forensics.storage import Storage

    monkeypatch.setenv("PII_REDACTION_ENABLED", "false")
    reset_settings()
    storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    trace = _trace_with_grounded_email()
    storage.save_trace(trace)

    diagnosis = analyze_trace(trace, f"Contact {EMAIL} for details.")
    diagnosis.evidence_chain = [f"entity {EMAIL} is not grounded"]
    storage.create_diagnosis(diagnosis)

    stored = storage.get_diagnosis(diagnosis.diagnosis_id)
    assert stored is not None
    assert stored.evidence_chain == [f"entity {EMAIL} is not grounded"]
