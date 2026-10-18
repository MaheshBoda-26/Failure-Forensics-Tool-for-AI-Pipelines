"""FastAPI application — REST feedback API and analytics endpoint.

Endpoints: run a pipeline, list/get traces, flag a trace, run/fetch diagnosis,
confirm or override a diagnosis, list eval cases, trigger and fetch eval runs
and trend data, fetch analytics.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from forensics.analyzer_simple import analyze_trace
from forensics.auth import AuthManager
from forensics.config import get_settings
from forensics.models import (
    DEFAULT_TENANT_ID,
    AnalyticsSnapshot,
    Diagnosis,
    DocumentType,
    EvalCase,
    EvalResult,
    EvalRun,
    FailureCategory,
    Flag,
    Tenant,
    Trace,
    TraceStatus,
)
from forensics.queue import Job, JobStatus, get_queue
from forensics.storage import Storage


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start-up and shutdown work that must not happen at import time.

    Loads custom check plugins so a bad CHECK_PLUGINS fails fast, and starts the
    scheduled eval when one is configured (a no-op otherwise).
    """
    from forensics.checks import load_plugins_from_config
    from forensics.scheduler import get_scheduler

    load_plugins_from_config(get_settings().check_plugins)
    scheduler = get_scheduler(get_storage())
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(title="Failure Forensics API", version="0.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_storage: Storage | None = None
_auth: AuthManager | None = None

#: Tenant used for every record written while auth is disabled. It matches the
#: SQLite migration default, so dev-mode data and pre-multi-tenancy data live in
#: the same tenant rather than two invisible halves.
DEFAULT_TENANT = Tenant(tenant_id=DEFAULT_TENANT_ID, name="default")


def get_storage() -> Storage:
    """Return the process-wide Storage, creating it on first use.

    Constructing Storage at import time opened (and created) the SQLite file as
    a side effect of `import forensics.api`, which broke test isolation and made
    the module unimportable without a writable data directory.
    """
    global _storage
    if _storage is None:
        _storage = Storage()
    return _storage


def get_auth() -> AuthManager:
    """Return the process-wide AuthManager, created on first use.

    `AuthManager(get_storage())` at import time would open the SQLite file as an
    import side effect, exactly as `get_storage` used to.
    """
    global _auth
    if _auth is None:
        settings = get_settings()
        _auth = AuthManager(
            get_storage(),
            prefix=settings.auth_api_key_prefix,
            rounds=settings.auth_bcrypt_rounds,
        )
    return _auth


def get_current_tenant(authorization: str | None = None) -> Tenant:
    """Resolve the calling tenant from the `Authorization` header.

    With `AUTH_ENABLED=false` this is the shared default tenant and every request
    succeeds — the documented dev mode, where the API behaves exactly as it did
    before auth existed.

    With auth on, a missing header, an unknown key, and a revoked key all answer
    401; an inactive tenant answers 403. The header is read by FastAPI itself
    (`authorization: Annotated[str | None, Header()]` on each endpoint) rather
    than through `Depends`, because this codebase's tests call endpoint functions
    directly and a `Depends` default would leak a `Depends` object into those
    calls.
    """
    if not get_settings().auth_enabled:
        return DEFAULT_TENANT

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    key = get_auth().validate_api_key(authorization[len("Bearer ") :].strip())
    if key is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    tenant = get_auth().get_tenant(key.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=403, detail="Tenant not found or inactive")
    return tenant


def tenant_scope(tenant: Tenant) -> str | None:
    """Tenant filter to apply on reads, or None for "no filter".

    Dev mode deliberately does *not* filter: with auth disabled the API must
    "work as before" (criterion 1), which means every trace, not just the ones
    stamped `default`. Writes always stamp a concrete tenant (`tenant.tenant_id`,
    which is `default` in dev mode).
    """
    return tenant.tenant_id if get_settings().auth_enabled else None


class FlagRequest(BaseModel):
    """Body for POST /traces/{trace_id}/flag.

    `trace_id` comes from the path, not the body; requiring it in both places
    meant a correct request failed validation with "Field required".
    """

    user_id: str = "api_user"
    note: str | None = None


class ConfirmDiagnosisRequest(BaseModel):
    """Body for POST /diagnoses/{diagnosis_id}/confirm.

    `diagnosis_id` comes from the path; requiring it in the body too made every
    correct request fail validation.
    """

    confirmed: bool
    override_category: FailureCategory | None = None
    override_reason: str | None = None


class PipelineRunRequest(BaseModel):
    """Body for POST /pipeline/run.

    `expected_type` is constrained to the known document types because it is
    ground truth for the analyzer's label check: an arbitrary string would be
    silently accepted and then never match.
    """

    doc_id: str = Field(..., min_length=1)
    doc_text: str = Field(..., min_length=1)
    expected_type: DocumentType | None = None
    priority: int = Field(default=0, description="Higher runs first")


class PipelineRunResponse(BaseModel):
    """Either a finished run (synchronous mode) or a queued one (`QUEUE_ENABLED`)."""

    status: str
    trace_id: str | None = None
    diagnosis_id: str | None = None
    root_cause_step: str | None = None
    job_id: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    """Unauthenticated liveness probe.

    Deliberately the one public route: a platform health check (and the deploy
    smoke test) has no API key to present.
    """
    return {"status": "ok"}


@app.get("/scheduler/status")
def scheduler_status(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Whether the scheduled eval is running, and when it fires next.

    Rejects unauthenticated callers like the data routes, and deliberately omits
    `job_store`: a filesystem path is not something every tenant needs.
    """
    get_current_tenant(authorization)
    from forensics.scheduler import get_scheduler

    status = get_scheduler(get_storage()).describe()
    return {key: value for key, value in status.items() if key != "job_store"}


@app.get("/traces", response_model=list[Trace])
def list_traces(
    status: TraceStatus | None = None,
    limit: int = 100,
    offset: int = 0,
    authorization: Annotated[str | None, Header()] = None,
) -> list[Trace]:
    tenant = get_current_tenant(authorization)
    return get_storage().list_traces(
        status=status, limit=limit, offset=offset, tenant_id=tenant_scope(tenant)
    )


@app.get("/traces/{trace_id}", response_model=Trace)
def get_trace(trace_id: str, authorization: Annotated[str | None, Header()] = None) -> Trace:
    tenant = get_current_tenant(authorization)
    trace = get_storage().get_trace(trace_id, tenant_id=tenant_scope(tenant))
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


@app.get("/traces/{trace_id}/diagnosis", response_model=Diagnosis)
def get_diagnosis_endpoint(
    trace_id: str, authorization: Annotated[str | None, Header()] = None
) -> Diagnosis:
    tenant = get_current_tenant(authorization)
    scope = tenant_scope(tenant)
    trace = get_storage().get_trace(trace_id, tenant_id=scope)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    diag = get_storage().get_diagnosis_for_trace(trace_id, tenant_id=scope)
    if diag:
        return diag
    return diagnose_trace(trace_id, authorization=authorization)


@app.post("/traces/{trace_id}/flag", response_model=Flag)
def create_flag(
    trace_id: str,
    request: FlagRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> Flag:
    tenant = get_current_tenant(authorization)
    trace = get_storage().get_trace(trace_id, tenant_id=tenant_scope(tenant))
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    flag = Flag(
        trace_id=trace_id,
        user_id=request.user_id,
        note=request.note,
        tenant_id=tenant.tenant_id,
    )
    return get_storage().create_flag(flag)


@app.post("/traces/{trace_id}/diagnose", response_model=Diagnosis)
def diagnose_trace(
    trace_id: str, authorization: Annotated[str | None, Header()] = None
) -> Diagnosis:
    tenant = get_current_tenant(authorization)
    trace = get_storage().get_trace(trace_id, tenant_id=tenant_scope(tenant))
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    dataset_dir = get_settings().dataset_dir
    doc_path = dataset_dir / "documents" / f"{trace.document_id}.txt"
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail="Document not found")

    doc_text = doc_path.read_text()

    # Ground-truth type, when the document has a label, so misclassification
    # is observable rather than assumed away.
    expected_type: str | None = None
    labels_path = dataset_dir / "labels.json"
    if labels_path.exists():
        try:
            labels = json.loads(labels_path.read_text())
            for entry in labels.get("documents", []):
                if entry.get("doc_id") == trace.document_id:
                    expected_type = entry.get("doc_type")
                    break
        except (OSError, ValueError):
            expected_type = None

    diagnosis = analyze_trace(trace, doc_text, expected_type)
    # `analyze_trace` cannot know who called it; the tenant is assigned by the
    # layer that does.
    diagnosis.tenant_id = tenant.tenant_id

    # Save diagnosis
    get_storage().create_diagnosis(diagnosis)
    return diagnosis


@app.post("/diagnoses/{diagnosis_id}/confirm")
def confirm_diagnosis(
    diagnosis_id: str,
    request: ConfirmDiagnosisRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> Diagnosis:
    tenant = get_current_tenant(authorization)
    scope = tenant_scope(tenant)
    storage = get_storage()

    diagnosis = storage.get_diagnosis(diagnosis_id, tenant_id=scope)
    if not diagnosis:
        raise HTTPException(status_code=404, detail="Diagnosis not found")

    override_category = request.override_category.value if request.override_category else None
    storage.update_diagnosis_confirmation(
        diagnosis_id,
        request.confirmed,
        overridden=bool(request.override_category),
        override_category=override_category,
        override_reason=request.override_reason,
    )

    # Auto-create flag if none exists for this trace
    flags = storage.list_flags_for_trace(diagnosis.trace_id, tenant_id=scope)
    if not flags:
        flag = Flag(
            trace_id=diagnosis.trace_id,
            user_id="api_user",
            note="Auto-created on diagnosis confirm",
            tenant_id=tenant.tenant_id,
        )
        storage.create_flag(flag)
        flags = [flag]

    # Auto-create eval case from confirmed diagnosis
    from forensics.eval_simple import flag_to_eval_case

    for flag in flags:
        case = flag_to_eval_case(flag, diagnosis, storage)
        if case:
            storage.create_eval_case(case)

    updated_diagnosis = storage.get_diagnosis(diagnosis_id, tenant_id=scope)
    if not updated_diagnosis:
        raise HTTPException(status_code=404, detail="Diagnosis not found after update")
    return updated_diagnosis


@app.get("/eval/cases")
def list_eval_cases(
    limit: int = 100,
    offset: int = 0,
    authorization: Annotated[str | None, Header()] = None,
) -> list[EvalCase]:
    tenant = get_current_tenant(authorization)
    return get_storage().list_eval_cases(limit=limit, offset=offset, tenant_id=tenant_scope(tenant))


@app.get("/eval/runs")
def list_eval_runs(
    limit: int = 100,
    offset: int = 0,
    authorization: Annotated[str | None, Header()] = None,
) -> list[EvalRun]:
    tenant = get_current_tenant(authorization)
    return get_storage().list_eval_runs(limit=limit, offset=offset, tenant_id=tenant_scope(tenant))


@app.get("/eval/runs/{run_id}")
def get_eval_run(run_id: str, authorization: Annotated[str | None, Header()] = None) -> EvalRun:
    tenant = get_current_tenant(authorization)
    run = get_storage().get_eval_run(run_id, tenant_id=tenant_scope(tenant))
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return run


@app.get("/eval/runs/{run_id}/results")
def get_eval_results(
    run_id: str, authorization: Annotated[str | None, Header()] = None
) -> list[EvalResult]:
    tenant = get_current_tenant(authorization)
    scope = tenant_scope(tenant)
    run = get_storage().get_eval_run(run_id, tenant_id=scope)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return get_storage().get_eval_results_for_run(run_id, tenant_id=scope)


@app.post("/eval/run")
async def trigger_eval_run(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    from forensics.eval_simple import create_eval_cases_from_confirmed_flags, run_eval

    tenant = get_current_tenant(authorization)
    scope = tenant_scope(tenant)
    storage = get_storage()
    create_eval_cases_from_confirmed_flags(scope)
    cases = storage.list_eval_cases(limit=100, tenant_id=scope)
    if not cases:
        # Seed eval cases from diagnosed failure traces
        traces = storage.list_traces(limit=20, tenant_id=scope)
        for t in traces:
            diag = storage.get_diagnosis_for_trace(t.trace_id, tenant_id=scope)
            if diag and diag.root_cause_step:
                flag = Flag(
                    trace_id=t.trace_id,
                    user_id="system",
                    note="Auto eval case",
                    tenant_id=tenant.tenant_id,
                )
                storage.create_flag(flag)
                storage.update_diagnosis_confirmation(diag.diagnosis_id, True)
        create_eval_cases_from_confirmed_flags(scope)

    run = await run_eval(scope)
    if run:
        return {
            "status": "ok",
            "run_id": run.run_id,
            "total_cases": run.total_cases,
            "passed_count": run.passed_count,
            "failed_count": run.failed_count,
            "current_accuracy": (
                (run.passed_count / run.total_cases) if run.total_cases > 0 else 0.0
            ),
        }
    return {
        "status": "ok",
        "run_id": "eval_benchmark_suite",
        "total_cases": 0,
        "passed_count": 0,
        "failed_count": 0,
        "current_accuracy": 0.0,
    }


@app.post("/pipeline/run-demo")
async def trigger_pipeline_demo(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Run the 4-step pipeline on all 15 dataset documents and save traces."""
    from forensics.llm import using_llm_client
    from forensics.models import Document
    from forensics.pipeline import Pipeline
    from forensics.tracing import trace_context

    tenant = get_current_tenant(authorization)
    settings = get_settings()
    dataset_dir = settings.dataset_dir
    labels_path = dataset_dir / "labels.json"
    if not labels_path.exists():
        raise HTTPException(status_code=404, detail="labels.json not found")

    with open(labels_path) as f:
        labels_data = json.load(f)

    docs = labels_data.get("documents", [])
    storage = get_storage()
    traces_saved = 0

    async with using_llm_client() as client:
        pipeline = Pipeline(client=client)
        for doc_info in docs:
            doc_id = doc_info["doc_id"]
            doc_path = dataset_dir / "documents" / f"{doc_id}.txt"
            if not doc_path.exists():
                continue
            doc_text = doc_path.read_text()
            document = Document(content=doc_text)
            # Traces written by this endpoint belong to the calling tenant, so a
            # run triggered by tenant A is not listed for tenant B.
            with trace_context(document_id=doc_id, tenant_id=tenant.tenant_id) as ctx:
                await pipeline.run(document)
                if ctx.trace:
                    storage.save_trace(ctx.trace)
                    expected_type = doc_info.get("doc_type")
                    diag = analyze_trace(ctx.trace, doc_text, expected_type)
                    diag.tenant_id = tenant.tenant_id
                    storage.create_diagnosis(diag)
                    traces_saved += 1

    return {"status": "ok", "traces_saved": traces_saved}


@app.post("/pipeline/run", response_model=PipelineRunResponse)
async def run_pipeline(
    request: PipelineRunRequest,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
) -> PipelineRunResponse:
    """Process one document — inline, or on a worker when `QUEUE_ENABLED` is true.

    With the queue disabled this is the previous behaviour: a synchronous run
    that returns the finished trace. With it enabled the document is queued and
    the request answers 202 with a `job_id` to poll.
    """
    tenant = get_current_tenant(authorization)
    expected_type = request.expected_type.value if request.expected_type else None

    if get_settings().queue_enabled:
        job = get_queue().enqueue(
            tenant.tenant_id,
            {
                "doc_id": request.doc_id,
                "doc_text": request.doc_text,
                "expected_type": expected_type,
            },
            priority=request.priority,
        )
        response.status_code = 202
        return PipelineRunResponse(status="queued", job_id=job.job_id)

    from forensics.llm import using_llm_client
    from forensics.pipeline import Pipeline
    from forensics.worker import process_document

    async with using_llm_client() as client:
        result = await process_document(
            Pipeline(client=client),
            get_storage(),
            doc_id=request.doc_id,
            doc_text=request.doc_text,
            expected_type=expected_type,
            tenant_id=tenant.tenant_id,
        )
    return PipelineRunResponse(
        status="completed",
        trace_id=result["trace_id"],
        diagnosis_id=result["diagnosis_id"],
        root_cause_step=result["root_cause_step"],
    )


@app.get("/jobs", response_model=list[Job])
def list_jobs(
    status: JobStatus | None = None,
    limit: int = 100,
    authorization: Annotated[str | None, Header()] = None,
) -> list[Job]:
    """List queued jobs, newest first."""
    tenant = get_current_tenant(authorization)
    return get_queue().list_jobs(tenant_id=tenant_scope(tenant), status=status, limit=limit)


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, authorization: Annotated[str | None, Header()] = None) -> Job:
    """Fetch one job. Another tenant's job is indistinguishable from a missing one."""
    tenant = get_current_tenant(authorization)
    job = get_queue().get_job(job_id, tenant_id=tenant_scope(tenant))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/analytics")
def get_analytics_endpoint(
    authorization: Annotated[str | None, Header()] = None,
) -> AnalyticsSnapshot:
    """Authenticated route; the aggregation itself lives in `get_analytics`."""
    tenant = get_current_tenant(authorization)
    return get_analytics(tenant_scope(tenant))


def get_analytics(tenant_id: str | None = None) -> AnalyticsSnapshot:
    """Get aggregated analytics for dashboard.

    A trace counts as a failure when its run status is FAILURE **or** the
    analyzer produced a real root cause for it. Counting only status=FAILURE
    reported 0% on a dataset whose seeded failures are semantic (the run
    completed, but the output was wrong), which is the number the dashboard
    exists to show.

    `tenant_id=None` aggregates every tenant; that is what the CLI and dev mode
    want.
    """
    storage = get_storage()
    traces = storage.list_traces(limit=10000, tenant_id=tenant_id)

    total_traces = len(traces)

    failures_by_category: dict[FailureCategory, int] = {}
    failures_by_step: dict[str, int] = {}
    failure_traces = 0
    root_cause_latencies: list[float] = []

    # Group traces by date for failure_rate_over_time
    traces_by_date: dict[str, dict[str, int]] = {}

    for trace in traces:
        diagnosis = storage.get_diagnosis_for_trace(trace.trace_id, tenant_id=tenant_id)
        has_semantic_failure = diagnosis is not None and diagnosis.root_cause_step not in (
            "",
            "none",
        )
        is_run_failure = trace.status == TraceStatus.FAILURE

        # Get trace date
        trace_date = trace.created_at.date().isoformat() if trace.created_at else "unknown"

        if trace_date not in traces_by_date:
            traces_by_date[trace_date] = {"total": 0, "failures": 0}

        traces_by_date[trace_date]["total"] += 1

        if not (is_run_failure or has_semantic_failure):
            continue

        failure_traces += 1
        traces_by_date[trace_date]["failures"] += 1

        if diagnosis is not None and diagnosis.root_cause_step not in ("", "none"):
            category = diagnosis.override_category or diagnosis.category
            failures_by_category[category] = failures_by_category.get(category, 0) + 1
            step = diagnosis.root_cause_step
            failures_by_step[step] = failures_by_step.get(step, 0) + 1

            # Time to root cause = latency accumulated up to and including the
            # failing step, from real span timings (never an invented average).
            elapsed_ms = 0
            for span in trace.spans:
                elapsed_ms += span.latency_ms
                if span.step_name == step:
                    root_cause_latencies.append(elapsed_ms / 1000.0)
                    break

    failure_rate = failure_traces / total_traces if total_traces > 0 else 0.0
    avg_time_to_root_cause = (
        sum(root_cause_latencies) / len(root_cause_latencies) if root_cause_latencies else None
    )

    # Build failure_rate_over_time sorted by date
    failure_rate_over_time = []
    for date_str in sorted(traces_by_date.keys()):
        day_data = traces_by_date[date_str]
        day_failure_rate = (
            day_data["failures"] / day_data["total"] if day_data["total"] > 0 else 0.0
        )
        failure_rate_over_time.append({"timestamp": date_str, "failure_rate": day_failure_rate})

    return AnalyticsSnapshot(
        total_traces=total_traces,
        failure_rate=failure_rate,
        failures_by_category=failures_by_category,
        failures_by_step=failures_by_step,
        failure_rate_over_time=failure_rate_over_time,
        avg_time_to_root_cause_seconds=avg_time_to_root_cause,
    )


# Mount static files if frontend/dist exists
def _static_dir() -> Path:
    """Directory holding the compiled SPA, resolved against the process CWD.

    `STATIC_DIR` is repository-relative (`frontend/dist`), and the process CWD is
    the repository root both on a dev machine and on the Render service. The
    previous `__file__`-relative path worked only for an editable install:
    `pip install ".[otel]"` puts `api.py` inside site-packages, where the path
    can never see the built frontend, so the deployed service answered 404 on
    `/` while `/health` stayed green (the blueprint's own build step still
    compiled the UI, which is what made the failure invisible).
    """
    try:
        configured = Path(get_settings().static_dir)
    except Exception:
        # `tests/test_imports.py` imports this module without a full environment.
        configured = Path("frontend/dist")
    if configured.is_absolute():
        return configured
    candidates = (
        Path.cwd() / configured,
        Path(__file__).resolve().parent.parent.parent / configured,
    )
    # A directory that does not exist is how the API says "no UI to serve" — the
    # mount below is skipped rather than failing, so a Node-less build still
    # answers the JSON API.
    return next((path for path in candidates if path.exists()), candidates[0])


_dist_path = _static_dir()
if _dist_path.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=_dist_path / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend_spa(full_path: str) -> FileResponse:
        target = _dist_path / full_path
        if target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(_dist_path / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
