"""FastAPI application — REST feedback API and analytics endpoint.

Endpoints: run a pipeline, list/get traces, flag a trace, run/fetch diagnosis,
confirm or override a diagnosis, list eval cases, trigger and fetch eval runs
and trend data, fetch analytics.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from forensics.analyzer_simple import analyze_trace
from forensics.models import (
    AnalyticsSnapshot,
    Diagnosis,
    EvalCase,
    EvalRun,
    FailureCategory,
    Flag,
    Trace,
    TraceStatus,
)
from forensics.storage import Storage

app = FastAPI(title="Failure Forensics API", version="0.1.0")

storage = Storage()


class FlagRequest(BaseModel):
    trace_id: str
    user_id: str = "api_user"
    note: str | None = None


class ConfirmDiagnosisRequest(BaseModel):
    diagnosis_id: str
    confirmed: bool
    override_category: str | None = None
    override_reason: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/traces", response_model=list[Trace])
def list_traces(
    status: TraceStatus | None = None, limit: int = 100, offset: int = 0
) -> list[Trace]:
    return storage.list_traces(status=status, limit=limit, offset=offset)


@app.get("/traces/{trace_id}", response_model=Trace)
def get_trace(trace_id: str) -> Trace:
    trace = storage.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


@app.post("/traces/{trace_id}/flag", response_model=Flag)
def create_flag(trace_id: str, request: FlagRequest) -> Flag:
    trace = storage.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    flag = Flag(trace_id=trace_id, user_id=request.user_id, note=request.note)
    return storage.create_flag(flag)


@app.post("/traces/{trace_id}/diagnose", response_model=Diagnosis)
def diagnose_trace(trace_id: str) -> Diagnosis:
    trace = storage.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    # Load document text
    from pathlib import Path

    doc_path = Path(f"dataset/documents/{trace.document_id}.txt")
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")

    doc_text = doc_path.read_text()
    diagnosis = analyze_trace(trace, doc_text)

    # Save diagnosis
    storage.create_diagnosis(diagnosis)
    return diagnosis


@app.post("/diagnoses/{diagnosis_id}/confirm")
def confirm_diagnosis(diagnosis_id: str, request: ConfirmDiagnosisRequest) -> Diagnosis:
    diagnosis = storage.get_diagnosis(diagnosis_id)
    if not diagnosis:
        raise HTTPException(status_code=404, detail="Diagnosis not found")

    storage.update_diagnosis_confirmation(
        diagnosis_id,
        request.confirmed,
        overridden=bool(request.override_category),
        override_category=request.override_category,
        override_reason=request.override_reason,
    )

    # If confirmed, create eval case
    if request.confirmed:
        # Find flag for this trace
        flags = storage.list_flags_for_trace(diagnosis.trace_id)
        if flags:
            # In a real app, we'd link the flag to the diagnosis
            pass

    updated_diagnosis = storage.get_diagnosis(diagnosis_id)
    if not updated_diagnosis:
        raise HTTPException(status_code=404, detail="Diagnosis not found after update")
    return updated_diagnosis


@app.get("/eval/cases")
def list_eval_cases(limit: int = 100, offset: int = 0) -> list[EvalCase]:
    return storage.list_eval_cases(limit=limit, offset=offset)


@app.get("/eval/runs")
def list_eval_runs() -> list[EvalRun]:
    # Would need to implement list_eval_runs in storage
    return []


@app.get("/analytics")
def get_analytics() -> AnalyticsSnapshot:
    """Get aggregated analytics for dashboard."""
    traces = storage.list_traces(limit=10000)

    total_traces = len(traces)
    failure_traces = [t for t in traces if t.status == TraceStatus.FAILURE]
    failure_rate = len(failure_traces) / total_traces if total_traces > 0 else 0.0

    # Failures by category (from diagnoses)
    failures_by_category: dict[FailureCategory, int] = {}
    failures_by_step: dict[str, int] = {}

    for trace in failure_traces:
        diagnosis = storage.get_diagnosis_for_trace(trace.trace_id)
        if diagnosis:
            cat = diagnosis.category
            failures_by_category[cat] = failures_by_category.get(cat, 0) + 1
            step = diagnosis.root_cause_step
            failures_by_step[step] = failures_by_step.get(step, 0) + 1

    return AnalyticsSnapshot(
        total_traces=total_traces,
        failure_rate=failure_rate,
        failures_by_category=failures_by_category,
        failures_by_step=failures_by_step,
        failure_rate_over_time=[],  # Would need time-series data
        avg_time_to_root_cause_seconds=None,  # Would need timing data
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
