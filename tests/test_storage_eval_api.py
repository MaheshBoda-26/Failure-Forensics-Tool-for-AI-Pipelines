"""Tests for Storage, the eval loop, the API, and the fault harness.

These four modules had zero coverage, which is how every regression they
contained shipped: a flag->diagnosis->eval case could never be produced, the
pipeline was never awaited inside the eval loop, and an incomplete eval run
could not be read back. The tests below pin the behaviour that was broken.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forensics.analyzer_simple import (
    aggregate_checks,
    analyze_trace,
    reachable_failure_pairs,
    run_deterministic_checks,
)
from forensics.faults import FAULTS, inject, score_fault
from forensics.models import (
    DocumentType,
    EvalRun,
    FailureCategory,
    Flag,
    Span,
    Trace,
    TraceStatus,
)
from forensics.storage import Storage

# ─── Helpers ────────────────────────────────────────────────────────────────


def _span(trace_id: str, step: str, output: dict, *, status: str = "success") -> Span:
    return Span(
        trace_id=trace_id,
        step_name=step,
        input_json="{}",
        output_json=json.dumps(output),
        model_id="test-model",
        provider="test",
        prompt="p",
        raw_response="r",
        status=status,  # type: ignore[arg-type]
    )


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")


DOC = "Contract between Acme Corp and Beta LLC effective 2024-01-15 for $10,000."


def _clean_trace() -> Trace:
    return Trace(
        trace_id="t1",
        document_id="contract_001",
        spans=[
            _span("t1", "intake", {"cleaned_text": DOC}),
            _span(
                "t1",
                "extraction",
                {
                    "entities": [
                        {
                            "value": "Acme",
                            "entity_type": "organization",
                            "start_offset": 18,
                            "end_offset": 22,
                        },
                        {
                            "value": "2024-01-15",
                            "entity_type": "date",
                            "start_offset": 62,
                            "end_offset": 72,
                        },
                    ]
                },
            ),
            _span("t1", "classification", {"document_type": "contract", "confidence": 0.9}),
            _span(
                "t1",
                "summarization",
                {
                    "summary": "Contract between Acme and Beta LLC effective 2024-01-15.",
                    "key_points": ["Acme", "2024-01-15"],
                },
            ),
        ],
    )


# ─── Storage ────────────────────────────────────────────────────────────────


def test_save_and_load_trace_roundtrip(storage: Storage) -> None:
    trace = _clean_trace()
    storage.save_trace(trace)
    loaded = storage.get_trace("t1")
    assert loaded is not None
    assert loaded.trace_id == "t1"
    assert len(loaded.spans) == 4


def test_incomplete_eval_run_is_readable(storage: Storage) -> None:
    """An eval run with completed_at=None must round-trip.

    The column stored "" rather than NULL, and loading it back raised a
    datetime ValidationError, so `/eval/runs` and the results table could not
    read an in-progress run.
    """
    run = EvalRun(pipeline_version="0.1.0", prompt_versions={})
    storage.create_eval_run(run)
    loaded = storage.get_eval_run(run.run_id)
    assert loaded is not None
    assert loaded.completed_at is None


def test_eval_run_listing(storage: Storage) -> None:
    a = storage.create_eval_run(EvalRun(pipeline_version="0.1.0", prompt_versions={}))
    b = storage.create_eval_run(EvalRun(pipeline_version="0.1.0", prompt_versions={}))
    runs = storage.list_eval_runs()
    assert {r.run_id for r in runs} == {a.run_id, b.run_id}


def test_update_eval_run_accepts_datetime(storage: Storage) -> None:
    """A datetime must be storable without relying on the deprecated adapter."""
    run = storage.create_eval_run(EvalRun(pipeline_version="0.1.0", prompt_versions={}))
    now = datetime.now(UTC)
    storage.update_eval_run(run.run_id, completed_at=now, passed_count=2, failed_count=1)
    loaded = storage.get_eval_run(run.run_id)
    assert loaded is not None
    assert loaded.passed_count == 2
    assert loaded.failed_count == 1
    assert loaded.completed_at is not None


def test_flag_roundtrip_and_diagnosis_link(storage: Storage) -> None:
    storage.save_trace(_clean_trace())
    flag = storage.create_flag(Flag(trace_id="t1", note="bad output", diagnosis_id="diag-1"))
    loaded = storage.get_flag(flag.flag_id)
    assert loaded is not None
    assert loaded.note == "bad output"
    assert loaded.diagnosis_id == "diag-1"
    assert [f.flag_id for f in storage.list_flags_for_trace("t1")] == [flag.flag_id]


def test_diagnosis_confirmation_roundtrip(storage: Storage) -> None:
    storage.save_trace(_clean_trace())
    d = analyze_trace(_clean_trace(), DOC)
    storage.create_diagnosis(d)
    storage.update_diagnosis_confirmation(
        d.diagnosis_id,
        confirmed=True,
        overridden=True,
        override_category="context_loss",
        override_reason="manual",
    )
    loaded = storage.get_diagnosis(d.diagnosis_id)
    assert loaded is not None
    assert loaded.confirmed is True
    assert loaded.overridden is True
    assert loaded.override_category is FailureCategory.CONTEXT_LOSS


def test_diagnosis_ids_do_not_collide(storage: Storage) -> None:
    """Two diagnoses in the same second must not collide on the primary key."""
    storage.save_trace(_clean_trace())
    d1 = analyze_trace(_clean_trace(), DOC)
    d2 = analyze_trace(_clean_trace(), DOC)
    storage.create_diagnosis(d1)
    storage.create_diagnosis(d2)  # must not raise
    assert d1.diagnosis_id != d2.diagnosis_id


# ─── Analyzer regression tests (integration path) ────────────────────────────


def test_aggregate_checks_uses_and_not_last_wins() -> None:
    from forensics.analyzer_simple import CheckResult

    results = [
        CheckResult("entity_grounding", False, "bad", "extraction"),
        CheckResult("entity_grounding", True, "good", "extraction"),
    ]
    # Order-independent: the passing result must not mask the failure.
    assert aggregate_checks(results)["entity_grounding"] is False
    assert aggregate_checks(list(reversed(results)))["entity_grounding"] is False


def test_hallucination_is_found_regardless_of_entity_order() -> None:
    ghost = {
        "value": "__ghost__",
        "entity_type": "org",
        "start_offset": 0,
        "end_offset": 9,
    }
    grounded = {
        "value": "Acme",
        "entity_type": "organization",
        "start_offset": 18,
        "end_offset": 22,
    }
    orders = {
        "ghost-first": [ghost, grounded],
        "good-first": [grounded, ghost],
    }
    for name, entities in orders.items():
        trace = _clean_trace()
        trace.spans[1].output_json = json.dumps({"entities": entities})
        d = analyze_trace(trace, DOC)
        assert d.root_cause_step == "extraction", name
        assert d.category is FailureCategory.EXTRACTION_HALLUCINATION, name


def test_unparseable_output_is_prompt_failure_not_hallucination() -> None:
    trace = _clean_trace()
    trace.spans[1].output_json = "NOT JSON AT ALL"
    d = analyze_trace(trace, DOC)
    assert d.category is FailureCategory.PROMPT_FAILURE


def test_repeated_step_names_do_not_share_a_verdict() -> None:
    """Two spans of the same step must be scored independently."""
    trace = _clean_trace()
    trace.spans.append(_span("t1", "classification", {"document_type": "nope", "confidence": 0.5}))
    results = run_deterministic_checks(trace, DOC)
    # Two classification spans -> two independent result buckets.
    class_buckets = [v for v in results.values() if any(c.step_name == "classification" for c in v)]
    assert len(class_buckets) == 2


def test_span_timestamps_bracket_the_work() -> None:
    import time

    from forensics.tracing import trace_context, traced_step

    with trace_context(document_id="d") as ctx:

        @traced_step("slow")
        def slow():
            time.sleep(0.05)
            return {"ok": True}

        slow()
    span = ctx.trace.spans[0]  # type: ignore[union-attr]
    delta = (span.ended_at - span.started_at).total_seconds()  # type: ignore[operator]
    assert delta >= 0.04


def test_finalize_preserves_exception_failure() -> None:
    """A failure raised outside any traced step must not be downgraded."""
    from forensics.tracing import TraceContext, finalize_trace

    ctx = TraceContext("t9", "d", Path("data/traces"), Path("data/forensics.db"))
    ctx.__enter__()
    try:
        raise RuntimeError("boom outside any traced step")
    except RuntimeError:
        ctx.__exit__(RuntimeError, RuntimeError("boom"), None)
    trace = ctx.trace
    assert trace is not None
    assert trace.status is TraceStatus.FAILURE
    finalize_trace(trace, traces_dir=Path("data/traces"), db_path=Path("data/forensics.db"))
    assert trace.status is TraceStatus.FAILURE


# ─── Fault harness ──────────────────────────────────────────────────────────


def test_every_fault_produces_its_declared_category() -> None:
    """The fault catalogue and the analyzer must agree, end to end."""
    trace = _clean_trace()
    for fault in FAULTS:
        result = score_fault(trace, fault, DOC, expected_type="contract")
        assert result["step_correct"], f"{fault.name}: step {result['actual_step']} != {fault.step}"
        assert result["category_correct"], (
            f"{fault.name}: category {result['actual_category']} != {fault.expected_category}"
        )


def test_clean_trace_has_no_root_cause() -> None:
    d = analyze_trace(_clean_trace(), DOC, expected_type="contract")
    assert d.root_cause_step == "none"


def test_inject_does_not_mutate_original() -> None:
    trace = _clean_trace()
    before = trace.spans[1].output_json
    inject(trace, FAULTS[0], DOC)
    assert trace.spans[1].output_json == before


def test_wrong_label_fault_always_changes_the_label() -> None:
    """A fault that re-sets the correct label is a no-op that blames the analyzer.

    Most demo documents never name their own type — "NON-DISCLOSURE AGREEMENT"
    contains no `contract` — so choosing "the first type absent from the text"
    could pick the type that is in fact correct, and the scorecard then reported a
    missed fault the analyzer had never been given.
    """
    from forensics.models import DocumentType

    fault = next(f for f in FAULTS if f.name == "wrong_label")
    valid = {member.value for member in DocumentType}

    for document_text in (
        "",
        "NON-DISCLOSURE AGREEMENT between the parties",
        "An invoice for services rendered",
        "A contract and a report and an invoice",
    ):
        for original_label in valid:
            output = {"document_type": original_label, "confidence": 0.9, "all_scores": {}}
            injected = fault.apply(output, document_text)
            assert injected["document_type"] != original_label, document_text
            assert injected["document_type"] in valid


# ─── Dataset reachability ───────────────────────────────────────────────────


def test_every_seeded_failure_is_reachable() -> None:
    """The shipped labels.json ground truth must be reproducible by the analyzer."""
    from forensics.config import get_settings

    labels_path = get_settings().dataset_dir / "labels.json"
    if not labels_path.exists():
        pytest.skip("dataset/labels.json not present")

    labels = json.loads(labels_path.read_text())
    reachable = reachable_failure_pairs()
    valid_types = {d.value for d in DocumentType}
    valid_cats = {c.value for c in FailureCategory}

    for entry in labels.get("documents", []):
        assert entry.get("doc_type") in valid_types, entry["doc_id"]
        if entry.get("should_fail"):
            pair = (entry["failure_step"], entry["failure_category"])
            assert entry["failure_category"] in valid_cats
            assert pair in reachable, f"{entry['doc_id']}: {pair} unreachable"


# ─── API ────────────────────────────────────────────────────────────────────


def test_analytics_counts_diagnosed_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Analytics must not report 0% when diagnoses exist on successful runs."""
    from forensics import api

    st = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", st)

    # A trace that RAN fine (status SUCCESS) but whose output was wrong.
    trace = _clean_trace()
    trace.spans[1].output_json = json.dumps(
        {
            "entities": [
                {"value": "__ghost__", "entity_type": "org", "start_offset": 0, "end_offset": 9}
            ]
        }
    )
    st.save_trace(trace)
    st.create_diagnosis(analyze_trace(st.get_trace("t1"), DOC))  # type: ignore[arg-type]

    snap = api.get_analytics()
    assert snap.total_traces == 1
    assert snap.failure_rate > 0.0


def test_list_eval_runs_endpoint_is_wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from forensics import api

    st = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", st)
    run = st.create_eval_run(EvalRun(pipeline_version="0.1.0", prompt_versions={}))
    assert [r.run_id for r in api.list_eval_runs()] == [run.run_id]


def test_confirm_validates_override_category() -> None:
    """An arbitrary override string must be rejected at the API boundary."""
    from pydantic import ValidationError

    from forensics.api import ConfirmDiagnosisRequest

    with pytest.raises(ValidationError):
        ConfirmDiagnosisRequest(confirmed=True, override_category="not_a_category")


def test_request_bodies_do_not_require_path_parameters() -> None:
    """Identifiers come from the path, so the body must not demand them again.

    Both bodies originally redeclared the path id, so a correct request failed
    with `Field required` on every call.
    """
    from forensics.api import ConfirmDiagnosisRequest, FlagRequest

    assert FlagRequest(note="x").user_id == "api_user"
    assert ConfirmDiagnosisRequest(confirmed=True).confirmed is True
    assert not {"trace_id"} & FlagRequest.model_fields.keys()
    assert not {"diagnosis_id"} & ConfirmDiagnosisRequest.model_fields.keys()


def test_flag_and_confirm_roundtrip_through_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forensics import api

    st = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", st)
    st.save_trace(_clean_trace())

    flag = api.create_flag("t1", api.FlagRequest(note="smoke"))
    assert flag.trace_id == "t1"

    diagnosis = api.diagnose_trace("t1")
    updated = api.confirm_diagnosis(
        diagnosis.diagnosis_id,
        api.ConfirmDiagnosisRequest(
            confirmed=True,
            override_category=FailureCategory.CONTEXT_LOSS,
            override_reason="manual",
        ),
    )
    assert updated.confirmed is True
    assert updated.overridden is True
    assert updated.override_category is FailureCategory.CONTEXT_LOSS
