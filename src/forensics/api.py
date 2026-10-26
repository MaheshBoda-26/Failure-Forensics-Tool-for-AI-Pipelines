"""FastAPI application — REST feedback API and analytics endpoint.

Endpoints: run a pipeline, list/get traces, flag a trace, run/fetch diagnosis,
confirm or override a diagnosis, list eval cases, trigger and fetch eval runs
and trend data, fetch analytics.
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, cast

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from forensics.analyzer_simple import analyze_trace
from forensics.auth import AuthManager
from forensics.config import get_settings
from forensics.documents import DOC_ID_REGEX, read_document, resolve_document_text
from forensics.models import (
    DEFAULT_TENANT_ID,
    AnalyticsSnapshot,
    APIKey,
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
from forensics.storage import get_storage as _get_storage_factory
from forensics.storage_base import StorageBase


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


def _cors_origins() -> list[str]:
    """Browser origins allowed to call the API, from `CORS_ORIGINS`.

    Never `["*"]`: the middleware below enables credentials, and a wildcard
    origin with credentials lets any page an operator visits drive the API with
    their authorization header. A literal `*` in the setting disables
    credentials instead, which is the only coherent combination.
    """
    raw = get_settings().cors_origins
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or ["http://localhost:5173", "http://127.0.0.1:8000"]


try:
    _CORS_ORIGINS = _cors_origins()
except Exception:
    # `tests/test_imports.py` imports this module without a full environment.
    _CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:8000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials="*" not in _CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


_storage: StorageBase | None = None
_auth: AuthManager | None = None

#: Tenant used for every record written while auth is disabled. It matches the
#: SQLite migration default, so dev-mode data and pre-multi-tenancy data live in
#: the same tenant rather than two invisible halves.
DEFAULT_TENANT = Tenant(tenant_id=DEFAULT_TENANT_ID, name="default")


async def get_current_tenant_from_request(request: Request) -> Tenant | APIKey:
    """Extract tenant from request without using Depends."""
    from forensics.auth import AuthManager
    from forensics.config import get_settings
    from forensics.storage import get_storage

    settings = get_settings()
    if not settings.auth_enabled:
        return DEFAULT_TENANT

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        # Will be handled by the endpoint's own auth check
        return DEFAULT_TENANT

    auth = AuthManager(
        get_storage(),
        prefix=settings.auth_api_key_prefix,
        rounds=settings.auth_bcrypt_rounds,
    )
    api_key = auth.validate_api_key(auth_header[len("Bearer ") :].strip())
    if api_key is None:
        return DEFAULT_TENANT

    return api_key


@app.middleware("http")
async def rate_limit_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Per-tenant token bucket rate limiting.

    Only applies when RATE_LIMIT_ENABLED=true and auth is enabled (so we have a tenant).
    Skips health checks and static files.
    """
    settings = get_settings()

    # Skip if rate limiting is disabled
    if not settings.rate_limit_enabled:
        return await call_next(request)

    # Skip health check and static files
    if request.url.path in ("/health", "/") or request.url.path.startswith("/static"):
        return await call_next(request)

    # Resolve tenant
    tenant = await get_current_tenant_from_request(request)

    # If no tenant (auth disabled), skip rate limiting
    if tenant.tenant_id == DEFAULT_TENANT_ID and not settings.auth_enabled:
        return await call_next(request)

    # Apply rate limit
    from forensics.rate_limit import get_rate_limiter

    limiter = get_rate_limiter()
    success, retry_after = limiter.try_consume(tenant.tenant_id)

    if not success:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    response = await call_next(request)
    # Add rate limit headers
    bucket = limiter.get_bucket_state(tenant.tenant_id)
    response.headers["X-RateLimit-Limit"] = str(int(bucket["capacity"]))
    response.headers["X-RateLimit-Remaining"] = str(int(bucket["tokens"]))
    return response


def get_storage() -> StorageBase:
    """Return the process-wide Storage, creating it on first use.

    Constructing Storage at import time opened (and created) the SQLite file as
    a side effect of `import forensics.api`, which broke test isolation and made
    the module unimportable without a writable data directory.
    """
    global _storage
    if _storage is None:
        _storage = _get_storage_factory()
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


def get_current_tenant(
    authorization: str | None = None, *, scope: str = "read", request: Request | None = None
) -> Tenant:
    """Resolve the calling tenant from the `Authorization` header.

    With `AUTH_ENABLED=false` this is the shared default tenant and every request
    succeeds — the documented dev mode, where the API behaves exactly as it did
    before auth existed. Scopes are an auth-mode concept and do not apply there.

    With auth on, a missing header, an unknown key, and a revoked key all answer
    401; an inactive tenant, or a key that does not hold `scope`, answers 403.
    The default scope is the *narrow* one, so a mutating route that forgets to
    ask for `write` fails closed rather than accepting a read-only key.

    The header is read by FastAPI itself
    (`authorization: Annotated[str | None, Header()]` on each endpoint) rather
    than through `Depends`, because this codebase's tests call endpoint functions
    directly and a `Depends` default would leak a `Depends` object into those
    calls.

    If `request` is provided and the middleware already validated the key, the
    cached result is reused — one bcrypt + DB write per request instead of two.
    """
    # Fast path: middleware already validated and cached the key
    if (
        request is not None
        and hasattr(request.state, "auth_key")
        and request.state.auth_key is not None
    ):
        key = request.state.auth_key
        if not key.allows(scope):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"API key is missing the '{scope}' scope "
                    f"(has: {', '.join(key.scopes) or 'none'})"
                ),
            )
        return cast(Tenant, request.state.auth_tenant)

    # Slow path: direct call (tests, non-middleware paths) or no cached value
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

    if not key.allows(scope):
        raise HTTPException(
            status_code=403,
            detail=(
                f"API key is missing the '{scope}' scope (has: {', '.join(key.scopes) or 'none'})"
            ),
        )
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
    note: str | None = Field(default=None, max_length=2_000)


class ConfirmDiagnosisRequest(BaseModel):
    """Body for POST /diagnoses/{diagnosis_id}/confirm.

    `diagnosis_id` comes from the path; requiring it in the body too made every
    correct request fail validation.

    `override_step` exists because a human can see the failing step when the
    deterministic analyzer reported `"none"`; without it, confirming an override
    on a missed failure could never create an eval case.
    """

    confirmed: bool
    override_category: FailureCategory | None = None
    override_step: str | None = Field(default=None, max_length=64)
    override_reason: str | None = Field(default=None, max_length=2_000)


class PipelineRunRequest(BaseModel):
    """Body for POST /pipeline/run.

    `expected_type` is constrained to the known document types because it is
    ground truth for the analyzer's label check: an arbitrary string would be
    silently accepted and then never match.

    `doc_text` is bounded: it is written to SQLite and to disk, so an unbounded
    field let one request persist an arbitrarily large payload.
    """

    doc_id: str = Field(..., min_length=1, pattern=DOC_ID_REGEX)
    doc_text: str = Field(..., min_length=1, max_length=1_000_000)
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
    get_current_tenant(authorization, scope="read")
    from forensics.scheduler import get_scheduler

    status = get_scheduler(get_storage()).describe()
    return {key: value for key, value in status.items() if key != "job_store"}


@app.get("/traces", response_model=list[Trace])
def list_traces(
    status: TraceStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    authorization: Annotated[str | None, Header()] = None,
) -> list[Trace]:
    tenant = get_current_tenant(authorization)
    scope = tenant_scope(tenant)
    storage = get_storage()
    traces = storage.list_traces(status=status, limit=limit, offset=offset, tenant_id=scope)

    # Attach the latest verdict in one query rather than one per trace. The run
    # status alone cannot distinguish a semantic failure (the pipeline finished
    # but produced wrong output) from a clean run, so a client that only sees
    # `status` paints every seeded failure green.
    verdicts = storage.latest_root_cause_steps([t.trace_id for t in traces], tenant_id=scope)
    for trace in traces:
        trace.root_cause_step = verdicts.get(trace.trace_id)
    return traces


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
    tenant = get_current_tenant(authorization, scope="write")
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
    tenant = get_current_tenant(authorization, scope="write")
    trace = get_storage().get_trace(trace_id, tenant_id=tenant_scope(tenant))
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    # Resolve the source text without ever building a path from an unvalidated
    # id: `read_document` applies an allowlist and a resolve-and-contain check,
    # so `doc_id="../../some_file"` can no longer read the host filesystem.
    # Uploads live outside the dataset, and a trace whose file is gone can still
    # be analyzed from its own intake span.
    doc_text = resolve_document_text(trace)
    if not doc_text:
        raise HTTPException(status_code=404, detail="Document not found")

    # Ground-truth type, when the document has a label, so misclassification is
    # observable rather than assumed away. One loader serves the CLI, the eval
    # loop, and this endpoint.
    from forensics.eval_simple import document_ground_truth

    diagnosis = analyze_trace(trace, doc_text, document_ground_truth(trace.document_id))
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
    tenant = get_current_tenant(authorization, scope="write")
    scope = tenant_scope(tenant)
    storage = get_storage()

    diagnosis = storage.get_diagnosis(diagnosis_id, tenant_id=scope)
    if not diagnosis:
        raise HTTPException(status_code=404, detail="Diagnosis not found")

    override_category = request.override_category.value if request.override_category else None
    storage.update_diagnosis_confirmation(
        diagnosis_id,
        request.confirmed,
        overridden=bool(request.override_category or request.override_step),
        override_category=override_category,
        override_reason=request.override_reason,
        override_step=request.override_step,
    )

    # Everything below reads the *updated* row. Passing the pre-update object
    # into `flag_to_eval_case` discarded the override the caller had just sent,
    # so a case could be built from the category and reason the human rejected.
    updated_diagnosis = storage.get_diagnosis(diagnosis_id, tenant_id=scope)
    if not updated_diagnosis:
        raise HTTPException(status_code=404, detail="Diagnosis not found after update")

    # A rejection is a verdict too: `confirmed=False` means "the analyzer is
    # wrong", which must not create a flag or an eval case that bakes the wrong
    # expectation into the suite. A clean trace ("none") is likewise not a
    # regression case — unless a human supplied `override_step`, which is
    # precisely the "the analyzer missed this" case.
    effective_step = updated_diagnosis.override_step or updated_diagnosis.root_cause_step
    if not updated_diagnosis.confirmed or effective_step in ("", "none"):
        return updated_diagnosis

    # Auto-create flag if none exists for this trace
    flags = storage.list_flags_for_trace(updated_diagnosis.trace_id, tenant_id=scope)
    if not flags:
        flag = Flag(
            trace_id=updated_diagnosis.trace_id,
            user_id="api_user",
            note="Auto-created on diagnosis confirm",
            tenant_id=tenant.tenant_id,
        )
        storage.create_flag(flag)
        flags = [flag]

    # Auto-create eval cases from the confirmed diagnosis, one per
    # `(trace, failing step)`. Confirming the same diagnosis twice used to
    # insert a duplicate case every time.
    from forensics.eval_simple import flag_to_eval_case

    for flag in flags:
        case = flag_to_eval_case(flag, updated_diagnosis, storage)
        if case:
            storage.create_eval_case(case)

    return updated_diagnosis


@app.get("/eval/cases")
def list_eval_cases(
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    authorization: Annotated[str | None, Header()] = None,
) -> list[EvalCase]:
    tenant = get_current_tenant(authorization)
    return get_storage().list_eval_cases(limit=limit, offset=offset, tenant_id=tenant_scope(tenant))


@app.get("/eval/runs")
def list_eval_runs(
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
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

    tenant = get_current_tenant(authorization, scope="write")
    scope = tenant_scope(tenant)
    storage = get_storage()
    create_eval_cases_from_confirmed_flags(scope)
    cases = storage.list_eval_cases(limit=100, tenant_id=scope)
    if not cases:
        # Seed eval cases from diagnosed failure traces. A passing trace stores
        # `root_cause_step="none"`, which is a non-empty string: the old
        # truthiness check therefore treated every clean run as a failure and
        # marked it confirmed, filling the suite with bogus cases.
        traces = storage.list_traces(limit=20, tenant_id=scope)
        for t in traces:
            diag = storage.get_diagnosis_for_trace(t.trace_id, tenant_id=scope)
            if not diag or (diag.override_step or diag.root_cause_step) in ("", "none"):
                continue
            # Don't stamp a second "Auto eval case" flag on a trace that a human
            # already flagged, and attribute the flag to the trace's own tenant
            # rather than to the caller's.
            if storage.list_flags_for_trace(t.trace_id, tenant_id=scope):
                pass
            else:
                flag = Flag(
                    trace_id=t.trace_id,
                    user_id="system",
                    note="Auto eval case",
                    tenant_id=t.tenant_id,
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
    # No run was created. `run_id` is null rather than a placeholder: the client
    # used to follow the invented id to `/eval/runs/.../results` and surface a
    # 404 as an error banner for a perfectly healthy, empty suite.
    return {
        "status": "empty",
        "run_id": None,
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

    tenant = get_current_tenant(authorization, scope="write")
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
            # Validated, contained lookup: labels.json is trusted, but the same
            # helper keeps this path from disagreeing with the request path
            # about what a safe document id is.
            doc_text = read_document(doc_id, tenant.tenant_id)
            if not doc_text:
                continue
            document = Document(content=doc_text)
            # Traces written by this endpoint belong to the calling tenant, so a
            # run triggered by tenant A is not listed for tenant B.
            with trace_context(document_id=doc_id, tenant_id=tenant.tenant_id) as ctx:
                await pipeline.run(document, document_id=doc_id)
                if ctx.trace:
                    # Diagnose before persisting: `save_trace` redacts the trace
                    # in place, and grounding a redacted entity always fails.
                    expected_type = doc_info.get("doc_type")
                    diag = analyze_trace(ctx.trace, doc_text, expected_type)
                    diag.tenant_id = tenant.tenant_id
                    storage.save_trace(ctx.trace)
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
    tenant = get_current_tenant(authorization, scope="write")
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
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
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

    Costs come from :func:`forensics.tracing.span_cost_usd` — the same helper
    that finalises ``trace.total_cost_usd`` — so ``total_cost_usd`` always
    equals ``sum(cost_by_model.values())`` and both are priced by configuration.
    This function previously multiplied tokens by a hardcoded per-token table
    (0.00000015 / 0.0000006), which invented spend that no configured price and
    no invoice supported, and made the per-model breakdown disagree with the
    trace totals it sat next to.

    `tenant_id=None` aggregates every tenant; that is what the CLI and dev mode
    want.
    """
    from forensics.tracing import span_cost_usd

    storage = get_storage()
    traces = storage.list_traces(limit=10000, tenant_id=tenant_id)

    total_traces = len(traces)

    failures_by_category: dict[FailureCategory, int] = {}
    failures_by_step: dict[str, int] = {}
    failure_traces = 0
    root_cause_latencies: list[float] = []

    total_cost_usd = 0.0
    cost_by_model: dict[str, float] = {}
    cost_by_step: dict[str, float] = {}
    tokens_by_model: dict[str, int] = {}
    tokens_by_step: dict[str, int] = {}

    latency_by_model: dict[str, float] = {}
    latency_by_step: dict[str, float] = {}
    latency_counts_by_model: dict[str, int] = {}
    latency_counts_by_step: dict[str, int] = {}

    # Span-level pass accounting, per model: a span "passes" when it is not the
    # step the diagnosis blames. This is deliberately not the trace-level pass
    # rate — every trace runs every step, so a per-model trace pass rate is
    # constant across models and measures nothing.
    model_span_totals: dict[str, int] = {}
    model_span_ok: dict[str, int] = {}
    model_cost_sums: dict[str, float] = {}
    model_latency_sums: dict[str, float] = {}

    traces_by_date: dict[str, dict[str, int]] = {}
    cost_by_date: dict[str, float] = {}
    failure_correlation: dict[str, dict[FailureCategory, int]] = {}

    for trace in traces:
        diagnosis = storage.get_diagnosis_for_trace(trace.trace_id, tenant_id=tenant_id)
        root_cause_step = (
            diagnosis.root_cause_step
            if diagnosis is not None and diagnosis.root_cause_step not in ("", "none")
            else None
        )
        has_semantic_failure = root_cause_step is not None
        is_run_failure = trace.status == TraceStatus.FAILURE
        # mypy cannot follow the ternary above; the later guards need the fact
        # that a blamed step implies a diagnosis object.
        diagnosed = diagnosis if has_semantic_failure else None

        trace_date = trace.created_at.date().isoformat() if trace.created_at else "unknown"
        day = traces_by_date.setdefault(trace_date, {"total": 0, "failures": 0})
        day["total"] += 1

        trace_cost = 0.0
        for span in trace.spans:
            model = span.model_id or "unknown"
            step = span.step_name

            total_tokens = span.prompt_tokens + span.completion_tokens + span.reasoning_tokens
            tokens_by_model[model] = tokens_by_model.get(model, 0) + total_tokens
            tokens_by_step[step] = tokens_by_step.get(step, 0) + total_tokens

            cost = span_cost_usd(span)
            trace_cost += cost
            cost_by_model[model] = cost_by_model.get(model, 0.0) + cost
            cost_by_step[step] = cost_by_step.get(step, 0.0) + cost
            model_cost_sums[model] = model_cost_sums.get(model, 0.0) + cost

            # Zero-latency spans (cache hits, deterministic steps) must not drag
            # the average down: they are excluded from both sum and count.
            if span.latency_ms:
                latency_by_model[model] = latency_by_model.get(model, 0.0) + span.latency_ms
                latency_by_step[step] = latency_by_step.get(step, 0.0) + span.latency_ms
                latency_counts_by_model[model] = latency_counts_by_model.get(model, 0) + 1
                latency_counts_by_step[step] = latency_counts_by_step.get(step, 0) + 1
                model_latency_sums[model] = model_latency_sums.get(model, 0.0) + span.latency_ms

            model_span_totals[model] = model_span_totals.get(model, 0) + 1
            if root_cause_step is None or step != root_cause_step:
                model_span_ok[model] = model_span_ok.get(model, 0) + 1

        total_cost_usd += trace_cost
        cost_by_date[trace_date] = cost_by_date.get(trace_date, 0.0) + trace_cost

        if not (is_run_failure or has_semantic_failure):
            continue

        failure_traces += 1
        day["failures"] += 1

        if root_cause_step is not None and diagnosed is not None:
            category = diagnosed.override_category or diagnosed.category
            failures_by_category[category] = failures_by_category.get(category, 0) + 1
            failures_by_step[root_cause_step] = failures_by_step.get(root_cause_step, 0) + 1
            failure_correlation.setdefault(root_cause_step, {})
            failure_correlation[root_cause_step][category] = (
                failure_correlation[root_cause_step].get(category, 0) + 1
            )

            # Time to root cause = latency accumulated up to and including the
            # failing step, from real span timings (never an invented average).
            elapsed_ms = 0
            for span in trace.spans:
                elapsed_ms += span.latency_ms
                if span.step_name == root_cause_step:
                    root_cause_latencies.append(elapsed_ms / 1000.0)
                    break

    failure_rate = failure_traces / total_traces if total_traces > 0 else 0.0
    avg_time_to_root_cause = (
        sum(root_cause_latencies) / len(root_cause_latencies) if root_cause_latencies else None
    )

    failure_rate_over_time = [
        {
            "timestamp": date_str,
            "failure_rate": (
                day_data["failures"] / day_data["total"] if day_data["total"] > 0 else 0.0
            ),
        }
        for date_str, day_data in sorted(traces_by_date.items())
    ]

    cost_trend = [
        {"timestamp": date_str, "cost_usd": round(cost, 8)}
        for date_str, cost in sorted(cost_by_date.items())
    ]

    total_latency = sum(latency_by_model.values())
    total_latency_samples = sum(latency_counts_by_model.values())
    avg_latency_ms = total_latency / total_latency_samples if total_latency_samples else 0.0

    latency_by_model_avg = {
        model: total / latency_counts_by_model.get(model, 1)
        for model, total in latency_by_model.items()
    }
    latency_by_step_avg = {
        step: total / latency_counts_by_step.get(step, 1) for step, total in latency_by_step.items()
    }

    model_performance: dict[str, dict[str, float]] = {}
    for model, span_count in model_span_totals.items():
        latency_samples = latency_counts_by_model.get(model, 0)
        model_performance[model] = {
            "span_pass_rate": model_span_ok.get(model, 0) / span_count if span_count else 0.0,
            "avg_latency_ms": (
                model_latency_sums.get(model, 0.0) / latency_samples if latency_samples else 0.0
            ),
            "avg_cost_usd": model_cost_sums.get(model, 0.0) / span_count if span_count else 0.0,
        }

    correlation_list = [
        {"step": step, "category": category.value, "count": count}
        for step, categories in failure_correlation.items()
        for category, count in categories.items()
    ]

    return AnalyticsSnapshot(
        total_traces=total_traces,
        failure_rate=failure_rate,
        failures_by_category=failures_by_category,
        failures_by_step=failures_by_step,
        failure_rate_over_time=failure_rate_over_time,
        avg_time_to_root_cause_seconds=avg_time_to_root_cause,
        total_cost_usd=round(total_cost_usd, 8),
        cost_by_model={model: round(cost, 8) for model, cost in cost_by_model.items()},
        cost_by_step={step: round(cost, 8) for step, cost in cost_by_step.items()},
        tokens_by_model=tokens_by_model,
        tokens_by_step=tokens_by_step,
        avg_latency_ms=avg_latency_ms,
        latency_by_model=latency_by_model_avg,
        latency_by_step=latency_by_step_avg,
        model_performance=model_performance,
        failure_correlation=correlation_list,
        cost_trend=cost_trend,
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
# A `dist` directory without a compiled `assets/` subdirectory (a partial or
# failed frontend build) must not crash the import: `StaticFiles(directory=...)`
# raises at construction, which took the whole API down before it could answer
# `/health`.
if (_dist_path / "assets").is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=_dist_path / "assets"), name="assets")


def _safe_static_target(full_path: str) -> Path | None:
    """Resolve a request path to a file inside the SPA directory, or None.

    This catch-all is unauthenticated, so a path that escapes `dist` must never
    be served: `..` segments — including the URL-encoded form a browser or proxy
    lets through — would otherwise expose `.env`, `data/forensics.db`, or any
    file the process can read. Both sides are resolved first, so a symlink
    inside `dist` that points outside it is rejected too.
    """
    try:
        base = _dist_path.resolve()
        target = (_dist_path / full_path).resolve()
    except OSError:  # pragma: no cover - resolve() only raises on a path loop
        return None
    if not target.is_relative_to(base) or not target.is_file():
        return None
    return target


# Registered unconditionally so the guard above is exercised (and testable)
# even when no frontend build exists; without an `index.html` it answers 404,
# which is what an unmatched route returned before the SPA was introduced.
@app.get("/{full_path:path}")
def serve_frontend_spa(full_path: str) -> FileResponse:
    target = _safe_static_target(full_path)
    if target is not None:
        return FileResponse(target)
    index = _dist_path / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(index)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
