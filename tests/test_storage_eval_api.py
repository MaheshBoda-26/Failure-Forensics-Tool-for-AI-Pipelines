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
from forensics.faults import FAULTS, fault_by_name, inject, score_all_faults, score_fault
from forensics.models import (
    Diagnosis,
    DocumentType,
    EvalCase,
    EvalRun,
    FailureCategory,
    Flag,
    JudgeAnalysis,
    Span,
    StepJudgeScore,
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


# ─── Diagnosis persistence: judge verdict must survive a round trip ─────────


def _judge_analysis() -> JudgeAnalysis:
    return JudgeAnalysis(
        step_scores=[
            StepJudgeScore(
                step_name="summarization",
                local_fidelity=0.2,
                global_fidelity=0.1,
                evidence=["summary drops the invoice total"],
                semantic_issues=["omits the payment terms"],
            )
        ],
        root_cause_step="summarization",
        category=FailureCategory.PROPAGATION_ERROR,
        confidence=0.77,
        cost_usd=0.0042,
        model_id="google/judge-model",
        provider="google",
    )


def test_judge_analysis_and_fallback_survive_persistence(storage: Storage) -> None:
    """`judge_analysis` was on the model but had no column, so it vanished.

    Persisting a diagnosis dropped the judge's verdict and the
    `analyzer_fallback` flag; `GET /traces/{id}/diagnosis` then reported a
    judge-less diagnosis even when the judge had overridden the deterministic one.
    """
    storage.save_trace(_clean_trace())
    diagnosis = analyze_trace(_clean_trace(), DOC)
    diagnosis.judge_analysis = _judge_analysis()
    diagnosis.analyzer_fallback = True

    storage.create_diagnosis(diagnosis)

    loaded = storage.get_diagnosis(diagnosis.diagnosis_id)
    assert loaded is not None
    assert loaded.analyzer_fallback is True
    assert loaded.judge_analysis is not None
    assert loaded.judge_analysis.root_cause_step == "summarization"
    assert loaded.judge_analysis.category is FailureCategory.PROPAGATION_ERROR
    assert loaded.judge_analysis.cost_usd == pytest.approx(0.0042)
    assert loaded.judge_analysis.step_scores[0].semantic_issues == ["omits the payment terms"]


def test_diagnosis_without_judge_analysis_round_trips_as_none(storage: Storage) -> None:
    """The deterministic path must not invent a judge verdict."""
    storage.save_trace(_clean_trace())
    diagnosis = analyze_trace(_clean_trace(), DOC)
    storage.create_diagnosis(diagnosis)

    loaded = storage.get_diagnosis(diagnosis.diagnosis_id)
    assert loaded is not None
    assert loaded.judge_analysis is None
    assert loaded.analyzer_fallback is False


def test_legacy_diagnoses_table_is_migrated_in_place(tmp_path: Path) -> None:
    """A database written before the columns existed is widened, not abandoned."""
    import sqlite3

    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db)
    # The exact `diagnoses` shape from before this revision: no judge columns.
    conn.execute(
        """
        CREATE TABLE diagnoses (
            diagnosis_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            root_cause_step TEXT NOT NULL,
            category TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_chain TEXT NOT NULL,
            step_scores TEXT NOT NULL,
            analyzer_version TEXT,
            model_id TEXT,
            provider TEXT,
            created_at TEXT NOT NULL,
            confirmed BOOLEAN DEFAULT 0,
            overridden BOOLEAN DEFAULT 0,
            override_category TEXT,
            override_reason TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default'
        )
        """
    )
    conn.execute(
        "INSERT INTO diagnoses (diagnosis_id, trace_id, root_cause_step, category, "
        "confidence, evidence_chain, step_scores, analyzer_version, model_id, provider, "
        "created_at) "
        "VALUES ('d1', 't1', 'summarization', 'propagation_error', 0.9, '[]', '[]', "
        "'0.1.0', 'test-model', 'test', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    storage = Storage(traces_dir=tmp_path / "traces", db_path=db)
    loaded = storage.get_diagnosis("d1")

    assert loaded is not None
    assert loaded.analyzer_fallback is False
    assert loaded.judge_analysis is None

    with sqlite3.connect(db) as check:
        columns = {row[1] for row in check.execute("PRAGMA table_info(diagnoses)")}
    assert {"judge_analysis", "analyzer_fallback"} <= columns


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


def test_analytics_payload_matches_the_frontend_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extended analytics fields must exist, typecheck, and serialize.

    `frontend/src/api/types.ts` declares these keys on `AnalyticsSnapshot`. The
    dashboard reads them directly, so a renamed or missing field degrades to an
    empty panel instead of an error — which is how the cost and latency
    sections could look "built" while being fed nothing.
    """
    from forensics import api

    st = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", st)

    trace = _clean_trace()
    st.save_trace(trace)
    st.create_diagnosis(analyze_trace(st.get_trace("t1"), DOC))  # type: ignore[arg-type]

    snap = api.get_analytics()
    # `mode="json"` is the wire format FastAPI sends (and what the dashboard sees).
    payload = snap.model_dump(mode="json")

    expected = {
        "total_cost_usd",
        "cost_by_model",
        "cost_by_step",
        "tokens_by_model",
        "tokens_by_step",
        "avg_latency_ms",
        "latency_by_model",
        "latency_by_step",
        "model_performance",
        "failure_correlation",
        "cost_trend",
    }
    assert expected <= payload.keys()

    assert isinstance(payload["total_cost_usd"], float)
    assert isinstance(payload["avg_latency_ms"], float)
    for key in ("cost_by_model", "cost_by_step", "tokens_by_model", "latency_by_model"):
        assert isinstance(payload[key], dict)

    for model, perf in payload["model_performance"].items():
        assert {"span_pass_rate", "avg_latency_ms", "avg_cost_usd"} <= perf.keys(), model

    # A list, not a mapping: this field is built as a list of cells, and a
    # `default_factory=dict` here silently produced `{}` whenever no failure had
    # been diagnosed yet.
    assert isinstance(payload["failure_correlation"], list)
    for cell in payload["failure_correlation"]:
        assert {"step", "category", "count"} <= cell.keys()

    for point in payload["cost_trend"]:
        assert {"timestamp", "cost_usd"} <= point.keys()

    json.dumps(payload)  # the endpoint must be JSON-serializable as-is


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


# ─── Eval case identity: exactly one per (trace, failing step) ──────────────
#
# A case means "this step is wrong on this trace". `confirm_diagnosis` and the
# `eval-cases` sweep both inserted a fresh row on every call, so re-confirming a
# diagnosis or re-running the sweep inflated the suite with copies and accuracy
# was measured over duplicates of the same failure.


def _eval_case(trace_id: str, step: str = "extraction") -> EvalCase:
    return EvalCase(
        trace_id=trace_id,
        failing_step=step,
        bad_output={"entities": []},
        corrected_output={"entities": []},
        category=FailureCategory.MISCLASSIFICATION,
        pipeline_version="0.1.0",
        prompt_version="v1",
    )


def _failing_trace() -> Trace:
    """A trace whose extraction hallucinates an entity (root cause: extraction)."""
    trace = _clean_trace()
    trace.spans[1].output_json = json.dumps(
        {
            "entities": [
                {"value": "__ghost__", "entity_type": "org", "start_offset": 0, "end_offset": 9}
            ]
        }
    )
    return trace


def test_create_eval_case_is_idempotent_per_trace_and_step(storage: Storage) -> None:
    storage.save_trace(_clean_trace())

    first = storage.create_eval_case(_eval_case("t1"))
    second = storage.create_eval_case(_eval_case("t1"))

    assert first.case_id == second.case_id
    assert len(storage.list_eval_cases(limit=100)) == 1


def test_eval_cases_for_different_steps_are_distinct(storage: Storage) -> None:
    storage.save_trace(_clean_trace())

    a = storage.create_eval_case(_eval_case("t1", "extraction"))
    b = storage.create_eval_case(_eval_case("t1", "summarization"))

    assert a.case_id != b.case_id
    assert len(storage.list_eval_cases(limit=100)) == 2


def test_eval_case_unique_index_rejects_a_raw_duplicate(storage: Storage) -> None:
    """The schema itself must refuse the duplicate, not only the helper."""
    import sqlite3

    storage.save_trace(_clean_trace())
    case = storage.create_eval_case(_eval_case("t1"))

    with sqlite3.connect(storage.db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO eval_cases (case_id, trace_id, failing_step, bad_output, "
            "corrected_output, category, created_at, tenant_id) VALUES "
            "('dup', 't1', ?, '{}', '{}', 'misclassification', "
            "'2026-01-01T00:00:00+00:00', 'default')",
            (case.failing_step,),
        )


def test_legacy_duplicate_eval_cases_are_collapsed_on_migration(tmp_path: Path) -> None:
    """A database that already holds duplicates is repaired, not rejected."""
    import sqlite3

    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE eval_cases (
            case_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            failing_step TEXT NOT NULL,
            bad_output TEXT NOT NULL,
            corrected_output TEXT NOT NULL,
            category TEXT NOT NULL,
            created_at TEXT NOT NULL,
            pipeline_version TEXT,
            prompt_version TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default'
        )
        """
    )
    for i in range(3):
        conn.execute(
            "INSERT INTO eval_cases (case_id, trace_id, failing_step, bad_output, "
            "corrected_output, category, created_at, pipeline_version, prompt_version) VALUES "
            "(?, 't1', 'extraction', '{}', '{}', 'misclassification', "
            "'2026-01-01T00:00:00+00:00', '0.1.0', 'v1')",
            (f"case{i}",),
        )
    conn.commit()
    conn.close()

    repaired = Storage(traces_dir=tmp_path / "traces", db_path=db)

    remaining = repaired.list_eval_cases(limit=100)
    assert len(remaining) == 1
    # The earliest row survives, so the original case id is stable.
    assert remaining[0].case_id == "case0"
    with sqlite3.connect(db) as check:
        indexes = {row[1] for row in check.execute("PRAGMA index_list(eval_cases)")}
    assert "idx_eval_cases_unique" in indexes


def test_eval_case_sweep_is_idempotent() -> None:
    """`forensics eval-cases` twice must not double the suite.

    `get_storage()` resolves the env-redirected paths the autouse fixture installs,
    so this exercises the same code path the CLI does.
    """
    from forensics.eval_simple import create_eval_cases_from_confirmed_flags
    from forensics.storage import get_storage, reset_storage

    reset_storage()
    storage = get_storage()
    trace = _failing_trace()
    storage.save_trace(trace)
    diagnosis = analyze_trace(trace, DOC, "contract")
    assert diagnosis.root_cause_step == "extraction"
    storage.create_diagnosis(diagnosis)
    storage.update_diagnosis_confirmation(diagnosis.diagnosis_id, confirmed=True)
    storage.create_flag(Flag(trace_id=trace.trace_id, note="bad extraction"))

    first = create_eval_cases_from_confirmed_flags()
    second = create_eval_cases_from_confirmed_flags()

    assert first == 1
    assert second == 0
    assert len(storage.list_eval_cases(limit=100)) == 1


def test_confirm_diagnosis_twice_creates_one_eval_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirming the same diagnosis twice must not duplicate its eval case."""
    from forensics import api

    st = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", st)
    st.save_trace(_failing_trace())

    diagnosis = api.diagnose_trace("t1")
    request = api.ConfirmDiagnosisRequest(confirmed=True)
    api.confirm_diagnosis(diagnosis.diagnosis_id, request)
    api.confirm_diagnosis(diagnosis.diagnosis_id, request)

    assert len(st.list_eval_cases(limit=100)) == 1


# ─── The eval loop must judge against the dataset label ─────────────────────


class _StubPipeline:
    """Emits a fixed trace so `run_eval_case` runs without a model call."""

    def __init__(self, steps: list[tuple[str, dict]]) -> None:
        self.steps = steps
        self.seen_document_ids: list[str] = []

    async def run(self, document: object, document_id: str = "") -> None:
        from forensics.tracing import get_current_trace

        self.seen_document_ids.append(document_id)
        trace = get_current_trace()
        assert trace is not None
        for step_name, output in self.steps:
            trace.spans.append(
                Span(
                    trace_id=trace.trace_id,
                    step_name=step_name,
                    input_json="{}",
                    output_json=json.dumps(output),
                    model_id="stub-model",
                    provider="stub",
                    prompt="stub",
                    raw_response="stub",
                    status="success",
                )
            )


async def test_run_eval_case_records_the_original_document_id() -> None:
    """`case.trace_id` is a trace id, not a document id."""
    from forensics.eval_simple import run_eval_case

    pipeline = _StubPipeline([("classification", {"document_type": "contract", "confidence": 0.9})])
    case = _eval_case("t_trace", "classification")

    await run_eval_case(case, pipeline, DOC, document_id="contract_001")  # type: ignore[arg-type]

    assert pipeline.seen_document_ids == ["contract_001"]


async def test_expected_type_decides_whether_a_misclassification_is_a_failure() -> None:
    """The bug: without the label, a wrong classification passed every check.

    `check_label_grounding` only runs when `expected_type` is supplied, so a
    `misclassification` case was reported "OK FIXED" by construction.
    """
    from forensics.eval_simple import run_eval_case

    # DOC is a contract; the stub pipeline calls it an invoice.
    steps = [("classification", {"document_type": "invoice", "confidence": 0.9})]
    case = _eval_case("t_trace", "classification")

    ungrounded = await run_eval_case(
        case,
        _StubPipeline(steps),
        DOC,
        document_id="contract_001",  # type: ignore[arg-type]
    )
    grounded = await run_eval_case(
        case,
        _StubPipeline(steps),
        DOC,
        document_id="contract_001",
        expected_type="contract",
    )

    assert ungrounded.fixed is True, "negative control: the label check was skipped"
    assert grounded.fixed is False
    assert grounded.details["category"] == FailureCategory.MISCLASSIFICATION.value


def test_document_ground_truth_reads_the_dataset_labels() -> None:
    from forensics.eval_simple import document_ground_truth

    assert document_ground_truth("contract_001") == "contract"
    assert document_ground_truth("invoice_001") == "invoice"
    assert document_ground_truth("not_in_the_dataset") is None


def test_analytics_cost_is_config_priced_and_reconciles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cost must come from configured prices, not a hardcoded table.

    The previous implementation multiplied tokens by 0.00000015/0.0000006 —
    numbers no configured price and no invoice supported — so the per-model
    breakdown disagreed with `trace.total_cost_usd` sitting next to it.
    """
    from forensics import api
    from forensics.config import get_settings, reset_settings

    monkeypatch.setenv("PIPELINE_MODEL_PRICE_IN", "3.0")
    monkeypatch.setenv("PIPELINE_MODEL_PRICE_OUT", "6.0")
    reset_settings()
    pipeline_model = get_settings().pipeline_model.model_id

    st = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", st)

    trace = _clean_trace()
    for span in trace.spans:
        span.model_id = pipeline_model
        span.prompt_tokens = 1000
        span.completion_tokens = 500
        span.latency_ms = 100
    st.save_trace(trace)

    snap = api.get_analytics()
    per_span = 1000 / 1_000_000 * 3.0 + 500 / 1_000_000 * 6.0
    assert snap.cost_by_model[pipeline_model] == pytest.approx(per_span * len(trace.spans))

    stored = st.get_trace("t1")
    assert stored is not None
    assert snap.total_cost_usd == pytest.approx(stored.total_cost_usd)
    assert snap.total_cost_usd == pytest.approx(sum(snap.cost_by_model.values()))
    assert snap.total_cost_usd == pytest.approx(sum(snap.cost_by_step.values()))


def test_analytics_span_pass_rate_blames_only_the_root_cause_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`span_pass_rate` is per span, not per trace.

    A trace-level pass rate would be identical for every model, because every
    trace runs every step — that is why the field was renamed from `pass_rate`.
    """
    from forensics import api

    st = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", st)

    trace = _clean_trace()
    for span in trace.spans:
        span.latency_ms = 250
    st.save_trace(trace)
    st.create_diagnosis(
        Diagnosis(
            trace_id="t1",
            root_cause_step="summarization",
            category=FailureCategory.CONTEXT_LOSS,
            confidence=0.8,
            analyzer_version="0.1.0",
            model_id="test-model",
            provider="test",
        )
    )

    snap = api.get_analytics()
    perf = snap.model_performance["test-model"]
    assert perf["span_pass_rate"] == pytest.approx(3 / 4)
    assert perf["avg_latency_ms"] == pytest.approx(250.0)
    assert snap.failures_by_step == {"summarization": 1}


def test_unscoreable_fault_is_reported_not_counted_as_a_miss() -> None:
    """`context_loss` needs entities to lose; without them it is not a failure.

    The offline fixture leaves most documents with an empty extraction, so this
    is the common case in a dry run. Scoring it as 0% made `benchmark-analyzer`
    report an analyzer defect that the analyzer does not have.
    """
    trace = _clean_trace()
    for span in trace.spans:
        if span.step_name == "extraction":
            span.output_json = json.dumps({"entities": []})

    result = score_fault(trace, fault_by_name("context_loss"), DOC, expected_type="contract")

    assert result["scoreable"] is False
    assert result["actual_step"] == "unscored"
    assert result["expected_step"] == "summarization"
    assert "Not scoreable" in result["evidence"][0]


def test_score_all_faults_excludes_unscoreable_faults_from_the_denominator() -> None:
    trace = _clean_trace()
    for span in trace.spans:
        if span.step_name == "extraction":
            span.output_json = json.dumps({"entities": []})

    card = score_all_faults(trace, DOC, expected_type="contract")

    assert card["unscoreable"] == 1
    assert card["total"] == len(FAULTS) - 1
    assert card["total"] + card["unscoreable"] == len(FAULTS)
    unscoreable = [r for r in card["results"] if not r["scoreable"]]
    assert [r["fault"] for r in unscoreable] == ["context_loss"]


def test_a_baseline_with_entities_keeps_context_loss_scoreable() -> None:
    """The precondition must not switch the fault off where it is meaningful."""
    card = score_all_faults(_clean_trace(), DOC, expected_type="contract")

    assert card["unscoreable"] == 0
    assert card["total"] == len(FAULTS)
    context_loss = next(r for r in card["results"] if r["fault"] == "context_loss")
    assert context_loss["scoreable"] is True
    assert context_loss["step_correct"] is True


# ─── run_eval is tenant-scoped ──────────────────────────────────────────────


class _NullClientContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


def _stub_run_eval(monkeypatch: pytest.MonkeyPatch, storage: Storage) -> None:
    """Point `run_eval` at a test storage and a stub pipeline.

    `run_eval` is the seam the scheduler now relies on for per-tenant runs, so it
    is exercised for real here: the case filter, the run record's tenant, and the
    summary update are the code under test, not a mock of them.
    """
    from forensics import eval_simple

    monkeypatch.setattr(eval_simple, "get_storage", lambda: storage)
    monkeypatch.setattr(
        eval_simple,
        "Pipeline",
        lambda client: _StubPipeline(
            [("classification", {"document_type": "contract", "confidence": 0.9})]
        ),
    )
    monkeypatch.setattr(eval_simple, "using_llm_client", lambda **kw: _NullClientContext())
    monkeypatch.setattr(eval_simple, "resolve_document_text", lambda trace: DOC)


async def test_run_eval_scopes_cases_and_the_run_record_to_one_tenant(
    storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scheduled run for one tenant must not evaluate another tenant's cases."""
    from forensics.eval_simple import run_eval

    _stub_run_eval(monkeypatch, storage)

    for tenant_id, trace_id in (("acme", "t_acme"), ("globex", "t_globex")):
        trace = _clean_trace()
        trace.trace_id = trace_id
        trace.tenant_id = tenant_id
        for span in trace.spans:
            span.trace_id = trace_id
            span.tenant_id = tenant_id
        storage.save_trace(trace)

        case = _eval_case(trace_id, "classification")
        case.tenant_id = tenant_id
        storage.create_eval_case(case)

    run = await run_eval(tenant_id="acme")

    assert run is not None
    # The run belongs to the tenant that asked for it, not to `default`.
    assert run.tenant_id == "acme"
    assert run.total_cases == 1
    assert run.completed_at is not None

    results = storage.get_eval_results_for_run(run.run_id, tenant_id="acme")
    assert len(results) == 1
    assert results[0].tenant_id == "acme"

    # Scoped audit: globex's case was never touched.
    assert storage.get_eval_results_for_run(run.run_id, tenant_id="globex") == []
    assert storage.list_eval_runs(tenant_id="globex") == []


async def test_run_eval_without_a_tenant_evaluates_every_case(
    storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI/dev path stays instance-wide and lands in `default`."""
    from forensics.eval_simple import run_eval

    _stub_run_eval(monkeypatch, storage)

    for tenant_id, trace_id in (("acme", "t_acme"), ("globex", "t_globex")):
        trace = _clean_trace()
        trace.trace_id = trace_id
        trace.tenant_id = tenant_id
        for span in trace.spans:
            span.trace_id = trace_id
            span.tenant_id = tenant_id
        storage.save_trace(trace)

        case = _eval_case(trace_id, "classification")
        case.tenant_id = tenant_id
        storage.create_eval_case(case)

    run = await run_eval()

    assert run is not None
    assert run.total_cases == 2
    assert {r.tenant_id for r in storage.get_eval_results_for_run(run.run_id)} == {
        "acme",
        "globex",
    }


async def test_run_eval_returns_none_when_a_tenant_has_no_cases(
    storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No cases must not create an empty run that pollutes the accuracy history."""
    from forensics.eval_simple import run_eval

    _stub_run_eval(monkeypatch, storage)

    assert await run_eval(tenant_id="empty") is None
    assert storage.list_eval_runs(tenant_id="empty") == []


def test_trace_endpoints_expose_expected_type_from_labels_and_custom_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /traces and GET /traces/{id} expose ground-truth expected_type."""
    from forensics import api

    st = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", st)

    trace = _clean_trace()
    trace.trace_id = "trace_gt_label"
    trace.document_id = "contract_001"
    for span in trace.spans:
        span.trace_id = trace.trace_id
    st.save_trace(trace)

    custom = _clean_trace()
    custom.trace_id = "trace_gt_custom"
    custom.document_id = "custom_upload_01"
    custom.expected_type = DocumentType.INVOICE
    for span in custom.spans:
        span.trace_id = custom.trace_id
    st.save_trace(custom)

    res_label = api.get_trace("trace_gt_label")
    assert res_label.expected_type is DocumentType.CONTRACT

    res_custom = api.get_trace("trace_gt_custom")
    assert res_custom.expected_type is DocumentType.INVOICE

    listed = {row.trace_id: row.expected_type for row in api.list_traces()}
    assert listed["trace_gt_label"] is DocumentType.CONTRACT
    assert listed["trace_gt_custom"] is DocumentType.INVOICE


def test_analytics_benchmark_endpoint_scores_clean_baselines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/analytics/benchmark runs all 7 deterministic faults against eligible clean traces."""
    from forensics import api

    st = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", st)

    trace = _clean_trace()
    trace.trace_id = "trace_bench_01"
    trace.document_id = "custom_clean_contract"
    trace.expected_type = DocumentType.CONTRACT
    for span in trace.spans:
        span.trace_id = trace.trace_id
    st.save_trace(trace)

    body = api.get_benchmark_endpoint()
    assert body["baselines_scored"] == 1
    assert cast_int(body["total"]) == 7
    assert body["step_accuracy"] == 1.0
    assert body["category_accuracy"] == 1.0
    assert len(body["results"]) == 7  # type: ignore[arg-type]


def cast_int(value: object) -> int:
    assert isinstance(value, int)
    return value
