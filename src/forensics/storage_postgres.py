"""PostgreSQL storage implementation using asyncpg.

This is an async-first implementation suitable for high-concurrency scenarios.
Uses the same schema as SQLite but with PostgreSQL-specific types and features.
"""

import asyncio
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import asyncpg

from forensics.models import (
    APIKey,
    Diagnosis,
    EvalCase,
    EvalResult,
    EvalRun,
    Flag,
    PromptTemplate,
    Tenant,
    Trace,
    TraceStatus,
)
from forensics.storage_base import StorageBase


def _default_traces_dir() -> Path:
    from forensics.config import get_settings

    try:
        return get_settings().traces_dir
    except Exception:
        return Path("data/traces")


def _get_database_url() -> str:
    """Get PostgreSQL connection URL from environment or settings."""
    # First check environment variable (Render sets this)
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # Then check settings
    try:
        from forensics.config import get_settings

        settings = get_settings()
        if hasattr(settings, "database_url") and settings.database_url:
            return settings.database_url
    except Exception:
        pass
    # Default for local development
    return "postgresql://postgres:postgres@localhost:5432/forensics"  # pragma: allowlist secret


def _json_value(value: Any, default: Any) -> Any:
    """Decode a JSONB column that asyncpg hands back as text.

    `asyncpg` returns `json`/`jsonb` values as `str` unless a codec is
    registered on the pool, so every read path has to decode. The SQLite backend
    does the same with `json.loads`; skipping it here produced
    `Input should be a valid dictionary [input_value='{}']` on every eval case.
    """
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _row_to_api_key(row: dict[str, Any]) -> APIKey:
    """Decode an `api_keys` row into a model, expanding the comma-separated scopes."""
    raw = row.get("scopes")
    if isinstance(raw, str):
        row = {**row, "scopes": [part for part in raw.split(",") if part]}
    elif raw is None:
        row = {**row, "scopes": ["*"]}
    return APIKey.model_validate(row)


logger = logging.getLogger(__name__)

T = TypeVar("T")


class PostgresStorage(StorageBase):
    """PostgreSQL storage for traces (JSON files + PostgreSQL index)."""

    def __init__(
        self,
        traces_dir: Path | None = None,
        db_path: Path | None = None,  # Ignored for PostgreSQL, kept for interface compatibility
    ) -> None:
        self.traces_dir = traces_dir or _default_traces_dir()
        self.db_path = db_path  # Not used, but kept for interface
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self._pool: asyncpg.Pool | None = None
        self._database_url = _get_database_url()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_lock = threading.Lock()
        self._timeout = 60.0

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """The private event loop every operation on this backend runs on.

        A `StorageBase` method is synchronous, but `asyncpg` pools are bound to
        the loop that created them. Running each call through `asyncio.run()`
        created a *new* loop per call, so the second call reused a pool whose
        connections belonged to a closed loop — the backend could not survive
        two sequential operations. One long-lived loop in a daemon thread makes
        the pool usable for the lifetime of the process.
        """
        with self._loop_lock:
            if self._loop is not None and not self._loop.is_closed():
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="forensics-postgres", daemon=True
            )
            thread.start()
            self._loop = loop
            self._thread = thread
            return loop

    def _run_coro(self, coro: Coroutine[Any, Any, T]) -> T:
        """Block the calling thread until `coro` finishes on the storage loop."""
        loop = self._ensure_loop()
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=self._timeout)
        except Exception:
            # Never leak an un-awaited coroutine: a refused call would otherwise
            # print "coroutine was never awaited" instead of the real error.
            coro.close()
            raise

    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create the connection pool, creating the schema on first use.

        `__init__` used to leave the schema to the caller, so a fresh PostgreSQL
        database failed on its first query with "relation does not exist". The
        pool is the thing that knows when the schema must exist, so it ensures it.
        """
        if self._pool is None:
            from forensics.config import get_settings

            settings = get_settings()
            self._pool = await asyncpg.create_pool(
                self._database_url,
                min_size=settings.pg_pool_min,
                max_size=settings.pg_pool_max,
                command_timeout=30,
            )
            await self._init_schema()
        return self._pool

    async def _aclose(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    def close(self) -> None:
        """Close the pool and stop this backend's event-loop thread.

        `StorageBase.close` is synchronous, so shutting the backend down means
        stopping the thread the pool's loop lives on as well. Leaving it running
        leaked one daemon thread per closed backend — invisible in a short test
        run, visible as an ever-growing thread count in a setup that rebuilds
        the backend (the PostgreSQL test suite does).
        """
        if self._loop is None:
            return
        if self._pool is not None:
            try:
                self._run_coro(self._aclose())
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.exception("Closing the PostgreSQL pool failed")
        loop, thread = self._loop, self._thread
        self._loop, self._thread = None, None
        with self._loop_lock:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)
        if not loop.is_closed():
            loop.close()

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[asyncpg.Connection]:
        """Get a database connection from the pool."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            yield conn

    async def _init_schema(self) -> None:
        """Initialize all database tables in PostgreSQL (idempotent)."""
        pool = self._pool
        if pool is None:  # pragma: no cover - only reachable via a direct call
            pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Traces and spans
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ,
                    total_latency_ms INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    total_cost_usd REAL DEFAULT 0.0,
                    pipeline_version TEXT,
                    schema_version INTEGER DEFAULT 1,
                    tenant_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS spans (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    parent_span_id TEXT,
                    model_id TEXT,
                    provider TEXT,
                    latency_ms INTEGER DEFAULT 0,
                    status TEXT,
                    error TEXT,
                    started_at TIMESTAMPTZ,
                    ended_at TIMESTAMPTZ,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
                )
                """
            )

            # Flags table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flags (
                    flag_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    user_id TEXT DEFAULT 'anonymous',
                    note TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    diagnosis_id TEXT,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
                )
                """
            )

            # Diagnoses table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnoses (
                    diagnosis_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    root_cause_step TEXT NOT NULL,
                    category TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_chain JSONB NOT NULL,
                    step_scores JSONB NOT NULL,
                    analyzer_version TEXT,
                    model_id TEXT,
                    provider TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    confirmed BOOLEAN DEFAULT FALSE,
                    overridden BOOLEAN DEFAULT FALSE,
                    override_category TEXT,
                    override_step TEXT,
                    override_reason TEXT,
                    judge_analysis JSONB,
                    analyzer_fallback BOOLEAN DEFAULT FALSE,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
                )
                """
            )

            # Eval cases table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS eval_cases (
                    case_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    failing_step TEXT NOT NULL,
                    bad_output JSONB NOT NULL,
                    corrected_output JSONB NOT NULL,
                    category TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    pipeline_version TEXT,
                    prompt_version TEXT,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
                )
                """
            )

            # Eval runs table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS eval_runs (
                    run_id TEXT PRIMARY KEY,
                    pipeline_version TEXT NOT NULL,
                    prompt_versions JSONB NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ,
                    total_cases INTEGER DEFAULT 0,
                    passed_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    tenant_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )

            # Eval results table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS eval_results (
                    result_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    score REAL NOT NULL,
                    fixed BOOLEAN NOT NULL,
                    details JSONB,
                    evaluated_at TIMESTAMPTZ NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY (run_id) REFERENCES eval_runs(run_id),
                    FOREIGN KEY (case_id) REFERENCES eval_cases(case_id)
                )
                """
            )

            # Prompt templates table (spec 09)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_templates (
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    template_text TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    PRIMARY KEY (name, version),
                    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_templates_name ON prompt_templates(name)"
            )

            # Job queue table (for worker queue)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_queue (
                    job_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL,
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    result JSONB,
                    error TEXT,
                    worker_id TEXT,
                    attempts INTEGER DEFAULT 0,
                    max_attempts INTEGER DEFAULT 3,
                    claim_expires_at TIMESTAMPTZ
                )
                """
            )

            # Auth tables
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    active BOOLEAN DEFAULT TRUE
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    name TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    revoked_at TIMESTAMPTZ,
                    last_used_at TIMESTAMPTZ,
                    expires_at TIMESTAMPTZ,
                    scopes TEXT NOT NULL DEFAULT '*',
                    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
                )
                """
            )

            # Rate limit buckets
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_limit_buckets (
                    tenant_id TEXT PRIMARY KEY,
                    tokens REAL NOT NULL,
                    last_refill TIMESTAMPTZ NOT NULL
                )
                """
            )

            # Indexes
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id)")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_traces_document_id ON traces(document_id)"
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_tenant ON traces(tenant_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_flags_trace_id ON flags(trace_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_flags_tenant ON flags(tenant_id)")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_diagnoses_trace_id ON diagnoses(trace_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_diagnoses_tenant ON diagnoses(tenant_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_cases_trace_id ON eval_cases(trace_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_cases_tenant ON eval_cases(tenant_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_results_run_id ON eval_results(run_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_results_tenant ON eval_results(tenant_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_templates_tenant "
                "ON prompt_templates(tenant_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_templates_name ON prompt_templates(name)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_queue_status "
                "ON job_queue(status, priority DESC, created_at)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_queue_tenant ON job_queue(tenant_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_queue_claim_expires "
                "ON job_queue(claim_expires_at)"
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(prefix)")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id)"
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_tenants_active ON tenants(active)")

            # Unique index for eval_cases
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_cases_unique
                ON eval_cases(trace_id, failing_step, tenant_id)
                """
            )

            # Widening migrations for databases created before the columns existed.
            # `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table.
            await conn.execute(
                "ALTER TABLE diagnoses ADD COLUMN IF NOT EXISTS judge_analysis JSONB"
            )
            await conn.execute(
                "ALTER TABLE diagnoses "
                "ADD COLUMN IF NOT EXISTS analyzer_fallback BOOLEAN DEFAULT FALSE"
            )
            await conn.execute("ALTER TABLE diagnoses ADD COLUMN IF NOT EXISTS override_step TEXT")
            await conn.execute(
                "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ"
            )
            await conn.execute(
                "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS scopes TEXT NOT NULL DEFAULT '*'"
            )
            await conn.execute(
                "ALTER TABLE job_queue ADD COLUMN IF NOT EXISTS claim_expires_at TIMESTAMPTZ"
            )

            # Default tenant
            await conn.execute(
                """
                INSERT INTO tenants (tenant_id, name, created_at, active)
                VALUES ('default', 'default', NOW(), TRUE)
                ON CONFLICT (tenant_id) DO NOTHING
                """
            )

    # --- Trace operations ---

    async def _async_save_trace(self, trace: Trace) -> Path:
        """Save trace: finalize status/totals, then write JSON + PostgreSQL.

        The PostgreSQL index was never written here: `save_trace` only called
        `finalize_trace`, which writes the JSON payload and a *SQLite* index. A
        fresh PostgreSQL deployment therefore listed no traces at all. The index
        rows are now written on the same connection, and the SQLite side file is
        skipped.
        """
        from forensics.tracing import finalize_trace

        async with self._conn() as conn:
            existing = await conn.fetchrow(
                "SELECT tenant_id FROM traces WHERE trace_id = $1", trace.trace_id
            )
            if existing is not None and existing["tenant_id"] != trace.tenant_id:
                raise ValueError(f"trace_id {trace.trace_id!r} already belongs to another tenant")

            finalize_trace(
                trace,
                traces_dir=self.traces_dir,
                write_sqlite_index=False,
            )

            await conn.execute(
                """
                INSERT INTO traces (
                    trace_id, document_id, status, created_at, completed_at,
                    total_latency_ms, total_tokens, total_cost_usd, pipeline_version,
                    schema_version, tenant_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (trace_id) DO UPDATE SET
                    document_id = EXCLUDED.document_id,
                    status = EXCLUDED.status,
                    created_at = EXCLUDED.created_at,
                    completed_at = EXCLUDED.completed_at,
                    total_latency_ms = EXCLUDED.total_latency_ms,
                    total_tokens = EXCLUDED.total_tokens,
                    total_cost_usd = EXCLUDED.total_cost_usd,
                    pipeline_version = EXCLUDED.pipeline_version,
                    schema_version = EXCLUDED.schema_version,
                    tenant_id = EXCLUDED.tenant_id
                """,
                trace.trace_id,
                trace.document_id,
                trace.status.value,
                trace.created_at,
                trace.completed_at,
                trace.total_latency_ms,
                trace.total_tokens,
                trace.total_cost_usd,
                trace.pipeline_version,
                trace.schema_version,
                trace.tenant_id,
            )

            # A re-saved trace (retried step, re-analysis) generates fresh span
            # ids, so replace rather than accumulate orphans.
            await conn.execute("DELETE FROM spans WHERE trace_id = $1", trace.trace_id)
            await conn.executemany(
                """
                INSERT INTO spans (
                    span_id, trace_id, step_name, parent_span_id, model_id, provider,
                    latency_ms, status, error, started_at, ended_at, tenant_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                [
                    (
                        span.span_id,
                        span.trace_id,
                        span.step_name,
                        span.parent_span_id,
                        span.model_id,
                        span.provider,
                        span.latency_ms,
                        span.status,
                        span.error,
                        span.started_at,
                        span.ended_at,
                        span.tenant_id,
                    )
                    for span in trace.spans
                ],
            )

            # Archive the prompt templates referenced by this trace's spans, one
            # row per (name, version). Idempotent. `intake` is deterministic (no
            # LLM prompt), so it carries no template and is skipped.
            import hashlib

            seen: set[tuple[str, str]] = set()
            for span in trace.spans:
                name = span.prompt_template
                if not name or name == "intake":
                    continue
                key = (name, span.prompt)
                if key in seen:
                    continue
                seen.add(key)
                version = hashlib.sha256(span.prompt.encode()).hexdigest()
                await conn.execute(
                    """
                    INSERT INTO prompt_templates
                    (name, version, template_text, created_at, tenant_id)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (name, version) DO UPDATE SET
                        template_text = EXCLUDED.template_text,
                        created_at = EXCLUDED.created_at
                    """,
                    name,
                    version,
                    span.prompt,
                    trace.created_at,
                    trace.tenant_id,
                )

        return self.traces_dir / f"{trace.trace_id}.json"

    async def _async_load_trace(self, trace_id: str) -> Trace | None:
        """Load trace from JSON file."""
        from forensics.documents import contained_path

        json_path = contained_path(self.traces_dir / f"{trace_id}.json", self.traces_dir)
        if json_path is None or not json_path.exists():
            return None
        with open(json_path) as f:
            data = json.load(f)
        return Trace.model_validate(data)

    async def _async_list_traces(
        self,
        status: TraceStatus | None = None,
        limit: int = 100,
        offset: int = 0,
        tenant_id: str | None = None,
    ) -> list[Trace]:
        """List traces from PostgreSQL index."""
        clauses: list[str] = []
        params: list[Any] = []
        param_num = 1

        if tenant_id is not None:
            clauses.append(f"tenant_id = ${param_num}")
            params.append(tenant_id)
            param_num += 1
        if status:
            clauses.append(f"status = ${param_num}")
            params.append(status.value)
            param_num += 1

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT trace_id FROM traces{where} "
            f"ORDER BY created_at DESC LIMIT ${param_num} OFFSET ${param_num + 1}"
        )
        params.extend([limit, offset])

        async with self._conn() as conn:
            rows = await conn.fetch(sql, *params)

        traces = []
        for row in rows:
            trace = await self._async_load_trace(row["trace_id"])
            if trace:
                traces.append(trace)
        return traces

    async def _async_get_trace(self, trace_id: str, tenant_id: str | None = None) -> Trace | None:
        """Get a single trace by ID, optionally scoped to one tenant."""
        async with self._conn() as conn:
            row = await conn.fetchrow("SELECT tenant_id FROM traces WHERE trace_id = $1", trace_id)
        if row is None:
            return None
        if tenant_id is not None and row["tenant_id"] != tenant_id:
            return None
        return await self._async_load_trace(trace_id)

    async def _async_latest_root_cause_steps(
        self, trace_ids: list[str], tenant_id: str | None = None
    ) -> dict[str, str]:
        """Get latest root cause step for each trace ID."""
        if not trace_ids:
            return {}

        placeholders = ",".join(f"${i + 1}" for i in range(len(trace_ids)))
        params: list[Any] = list(trace_ids)
        param_offset = len(trace_ids) + 1

        sql = (
            f"SELECT trace_id, root_cause_step, created_at FROM diagnoses "
            f"WHERE trace_id IN ({placeholders})"
        )
        if tenant_id is not None:
            sql += f" AND tenant_id = ${param_offset}"
            params.append(tenant_id)
        sql += " ORDER BY created_at DESC"

        verdicts: dict[str, str] = {}
        async with self._conn() as conn:
            rows = await conn.fetch(sql, *params)
            for row in rows:
                trace_id = row["trace_id"]
                if trace_id not in verdicts:
                    verdicts[trace_id] = row["root_cause_step"]
        return verdicts

    # --- Flag operations ---

    async def _async_create_flag(self, flag: Flag) -> Flag:
        """Create a new flag."""
        from forensics.pii import create_redactor_from_settings

        redactor = create_redactor_from_settings()
        if redactor.enabled and flag.note:
            flag.note, _ = redactor.redact(flag.note)

        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO flags
                (flag_id, trace_id, user_id, note, created_at, diagnosis_id, tenant_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                flag.flag_id,
                flag.trace_id,
                flag.user_id,
                flag.note,
                flag.created_at,
                flag.diagnosis_id,
                flag.tenant_id,
            )
        return flag

    async def _async_get_flag(self, flag_id: str, tenant_id: str | None = None) -> Flag | None:
        """Get a flag by ID."""
        sql = "SELECT * FROM flags WHERE flag_id = $1"
        params: list[Any] = [flag_id]
        if tenant_id is not None:
            sql += " AND tenant_id = $2"
            params.append(tenant_id)
        async with self._conn() as conn:
            row = await conn.fetchrow(sql, *params)
            if not row:
                return None
            return Flag.model_validate(dict(row))

    async def _async_list_flags_for_trace(
        self, trace_id: str, tenant_id: str | None = None
    ) -> list[Flag]:
        """List all flags for a trace."""
        sql = "SELECT * FROM flags WHERE trace_id = $1"
        params: list[Any] = [trace_id]
        if tenant_id is not None:
            sql += " AND tenant_id = $2"
            params.append(tenant_id)
        sql += " ORDER BY created_at DESC"
        async with self._conn() as conn:
            rows = await conn.fetch(sql, *params)
            return [Flag.model_validate(dict(row)) for row in rows]

    # --- Diagnosis operations ---

    async def _async_create_diagnosis(self, diagnosis: Diagnosis) -> Diagnosis:
        """Create a new diagnosis."""
        from forensics.pii import redact_diagnosis

        redact_diagnosis(diagnosis)

        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO diagnoses
                (diagnosis_id, trace_id, root_cause_step, category, confidence,
                 evidence_chain, step_scores, analyzer_version, model_id, provider,
                 created_at, confirmed, overridden, override_category, override_step,
                 override_reason, judge_analysis, analyzer_fallback, tenant_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $13, $14, $15, $16, $17, $18, $19)
                """,
                diagnosis.diagnosis_id,
                diagnosis.trace_id,
                diagnosis.root_cause_step,
                diagnosis.category.value,
                diagnosis.confidence,
                json.dumps(diagnosis.evidence_chain),
                json.dumps([s.model_dump() for s in diagnosis.step_scores]),
                diagnosis.analyzer_version,
                diagnosis.model_id,
                diagnosis.provider,
                diagnosis.created_at,
                diagnosis.confirmed,
                diagnosis.overridden,
                diagnosis.override_category.value if diagnosis.override_category else None,
                diagnosis.override_step,
                diagnosis.override_reason,
                json.dumps(diagnosis.judge_analysis.model_dump(mode="json"))
                if diagnosis.judge_analysis
                else None,
                diagnosis.analyzer_fallback,
                diagnosis.tenant_id,
            )
        return diagnosis

    def _load_diagnosis_row(self, row: asyncpg.Record) -> Diagnosis:
        from forensics.models import StepScore

        data = dict(row)
        data["evidence_chain"] = _json_value(data.get("evidence_chain"), [])
        data["step_scores"] = [
            StepScore.model_validate(score) for score in _json_value(data.get("step_scores"), [])
        ]
        data["judge_analysis"] = _json_value(data.get("judge_analysis"), None)
        return Diagnosis.model_validate(data)

    async def _async_get_diagnosis(
        self, diagnosis_id: str, tenant_id: str | None = None
    ) -> Diagnosis | None:
        """Get a diagnosis by ID."""
        sql = "SELECT * FROM diagnoses WHERE diagnosis_id = $1"
        params: list[Any] = [diagnosis_id]
        if tenant_id is not None:
            sql += " AND tenant_id = $2"
            params.append(tenant_id)
        async with self._conn() as conn:
            row = await conn.fetchrow(sql, *params)
            if not row:
                return None
            return self._load_diagnosis_row(row)

    async def _async_get_diagnosis_for_trace(
        self, trace_id: str, tenant_id: str | None = None
    ) -> Diagnosis | None:
        """Get the diagnosis for a trace (latest)."""
        sql = "SELECT * FROM diagnoses WHERE trace_id = $1"
        params: list[Any] = [trace_id]
        if tenant_id is not None:
            sql += " AND tenant_id = $2"
            params.append(tenant_id)
        sql += " ORDER BY created_at DESC LIMIT 1"
        async with self._conn() as conn:
            row = await conn.fetchrow(sql, *params)
            if not row:
                return None
            return self._load_diagnosis_row(row)

    async def _async_update_diagnosis_confirmation(
        self,
        diagnosis_id: str,
        confirmed: bool,
        overridden: bool = False,
        override_category: str | None = None,
        override_reason: str | None = None,
        override_step: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Update diagnosis confirmation status."""
        from forensics.pii import create_redactor_from_settings

        if override_reason:
            redactor = create_redactor_from_settings()
            if redactor.enabled:
                override_reason, _ = redactor.redact(override_reason)

        sql = """
            UPDATE diagnoses
            SET confirmed = $1,
                overridden = (overridden OR $2),
                override_category = COALESCE($3, override_category),
                override_reason = COALESCE($4, override_reason),
                override_step = COALESCE($5, override_step)
            WHERE diagnosis_id = $6
        """
        params: list[Any] = [
            confirmed,
            overridden,
            override_category,
            override_reason,
            override_step,
            diagnosis_id,
        ]
        if tenant_id is not None:
            sql += " AND tenant_id = $7"
            params.append(tenant_id)

        async with self._conn() as conn:
            await conn.execute(sql, *params)

    # --- Prompt template operations ---

    async def _async_save_prompt_template(self, template: PromptTemplate) -> None:
        """Insert or update a prompt template by (name, version)."""
        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO prompt_templates
                (name, version, template_text, created_at, tenant_id)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (name, version) DO UPDATE SET
                    template_text = EXCLUDED.template_text,
                    created_at = EXCLUDED.created_at
                """,
                template.name,
                template.version,
                template.template_text,
                template.created_at,
                template.tenant_id,
            )

    async def _async_get_prompt_template(
        self, name: str, version: str, tenant_id: str | None = None
    ) -> PromptTemplate | None:
        """Get one prompt template by name and content hash."""
        sql = "SELECT * FROM prompt_templates WHERE name = $1 AND version = $2"
        params: list[Any] = [name, version]
        if tenant_id is not None:
            sql += " AND tenant_id = $" + str(len(params) + 1)
            params.append(tenant_id)
        async with self._conn() as conn:
            row = await conn.fetchrow(sql, *params)
            if not row:
                return None
            return self._load_prompt_template_row(row)

    async def _async_list_prompt_templates(
        self, tenant_id: str | None = None
    ) -> list[PromptTemplate]:
        """List every archived prompt template."""
        sql = "SELECT * FROM prompt_templates"
        params: list[Any] = []
        if tenant_id is not None:
            sql += " WHERE tenant_id = $1"
            params.append(tenant_id)
        sql += " ORDER BY name, version"
        async with self._conn() as conn:
            rows = await conn.fetch(sql, *params)
            return [self._load_prompt_template_row(row) for row in rows]

    @staticmethod
    def _load_prompt_template_row(row: Any) -> PromptTemplate:
        data = dict(row)
        data["created_at"] = data["created_at"].isoformat() if data["created_at"] else None
        return PromptTemplate.model_validate(data)

    # --- Eval Case operations ---

    async def _async_find_eval_case(
        self, trace_id: str, failing_step: str, tenant_id: str | None = None
    ) -> EvalCase | None:
        """Find an eval case by trace and failing step."""
        sql = "SELECT * FROM eval_cases WHERE trace_id = $1 AND failing_step = $2"
        params: list[Any] = [trace_id, failing_step]
        if tenant_id is not None:
            sql += " AND tenant_id = $3"
            params.append(tenant_id)
        sql += " LIMIT 1"
        async with self._conn() as conn:
            row = await conn.fetchrow(sql, *params)
            if not row:
                return None
            return self._load_eval_case_row(row)

    async def _async_create_eval_case(self, case: EvalCase) -> EvalCase:
        """Create a new eval case, or return the one already covering this step."""
        from forensics.pii import create_redactor_from_settings

        existing = await self._async_find_eval_case(
            case.trace_id, case.failing_step, case.tenant_id
        )
        if existing is not None:
            return existing

        bad_output = case.bad_output
        corrected_output = case.corrected_output
        redactor = create_redactor_from_settings()
        if redactor.enabled:
            bad_output, _ = redactor.redact_json(bad_output)
            corrected_output, _ = redactor.redact_json(corrected_output)

        async with self._conn() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO eval_cases
                    (case_id, trace_id, failing_step, bad_output, corrected_output,
                     category, created_at, pipeline_version, prompt_version, tenant_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    case.case_id,
                    case.trace_id,
                    case.failing_step,
                    json.dumps(bad_output),
                    json.dumps(corrected_output),
                    case.category.value,
                    case.created_at,
                    case.pipeline_version,
                    case.prompt_version,
                    case.tenant_id,
                )
            except asyncpg.UniqueViolationError:
                existing = await self._async_find_eval_case(
                    case.trace_id, case.failing_step, case.tenant_id
                )
                if existing is None:
                    raise
                return existing

        found = await self._async_find_eval_case(case.trace_id, case.failing_step, case.tenant_id)
        return found or case

    def _load_eval_case_row(self, row: asyncpg.Record) -> EvalCase:
        data = dict(row)
        data["bad_output"] = _json_value(data.get("bad_output"), {})
        data["corrected_output"] = _json_value(data.get("corrected_output"), {})
        return EvalCase.model_validate(data)

    async def _async_get_eval_case(
        self, case_id: str, tenant_id: str | None = None
    ) -> EvalCase | None:
        """Get an eval case by ID."""
        sql = "SELECT * FROM eval_cases WHERE case_id = $1"
        params: list[Any] = [case_id]
        if tenant_id is not None:
            sql += " AND tenant_id = $2"
            params.append(tenant_id)
        async with self._conn() as conn:
            row = await conn.fetchrow(sql, *params)
            if not row:
                return None
            return self._load_eval_case_row(row)

    async def _async_list_eval_cases(
        self, limit: int = 100, offset: int = 0, tenant_id: str | None = None
    ) -> list[EvalCase]:
        """List all eval cases."""
        sql = "SELECT * FROM eval_cases"
        params: list[Any] = []
        if tenant_id is not None:
            sql += " WHERE tenant_id = $1"
            params.append(tenant_id)
        param_offset = len(params) + 1
        sql += f" ORDER BY created_at DESC LIMIT ${param_offset} OFFSET ${param_offset + 1}"
        params.extend([limit, offset])
        async with self._conn() as conn:
            rows = await conn.fetch(sql, *params)
            return [self._load_eval_case_row(row) for row in rows]

    # --- Eval Run operations ---

    async def _async_create_eval_run(self, run: EvalRun) -> EvalRun:
        """Create a new eval run."""
        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO eval_runs
                (run_id, pipeline_version, prompt_versions, started_at, completed_at,
                 total_cases, passed_count, failed_count, tenant_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                run.run_id,
                run.pipeline_version,
                json.dumps(run.prompt_versions),
                run.started_at,
                run.completed_at,
                run.total_cases,
                run.passed_count,
                run.failed_count,
                run.tenant_id,
            )
        return run

    async def _async_update_eval_run(
        self,
        run_id: str,
        completed_at: datetime | None = None,
        passed_count: int | None = None,
        failed_count: int | None = None,
        total_cases: int | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Update eval run completion status."""
        if completed_at is None:
            completed_at = datetime.now(UTC)

        updates = ["completed_at = $1"]
        params: list[Any] = [completed_at]
        param_num = 2

        if total_cases is not None:
            updates.append(f"total_cases = ${param_num}")
            params.append(total_cases)
            param_num += 1
        if passed_count is not None:
            updates.append(f"passed_count = ${param_num}")
            params.append(passed_count)
            param_num += 1
        if failed_count is not None:
            updates.append(f"failed_count = ${param_num}")
            params.append(failed_count)
            param_num += 1

        where = f"WHERE run_id = ${param_num}"
        params.append(run_id)
        param_num += 1
        if tenant_id is not None:
            where += f" AND tenant_id = ${param_num}"
            params.append(tenant_id)
        sql = f"UPDATE eval_runs SET {', '.join(updates)} {where}"
        async with self._conn() as conn:
            await conn.execute(sql, *params)

    def _load_eval_run_row(self, row: asyncpg.Record) -> EvalRun:
        data = dict(row)
        data["prompt_versions"] = _json_value(data.get("prompt_versions"), {})
        return EvalRun.model_validate(data)

    async def _async_get_eval_run(
        self, run_id: str, tenant_id: str | None = None
    ) -> EvalRun | None:
        """Get an eval run by ID."""
        sql = "SELECT * FROM eval_runs WHERE run_id = $1"
        params: list[Any] = [run_id]
        if tenant_id is not None:
            sql += " AND tenant_id = $2"
            params.append(tenant_id)
        async with self._conn() as conn:
            row = await conn.fetchrow(sql, *params)
            if not row:
                return None
            return self._load_eval_run_row(row)

    async def _async_list_eval_runs(
        self, limit: int = 100, offset: int = 0, tenant_id: str | None = None
    ) -> list[EvalRun]:
        """List all eval runs."""
        sql = "SELECT * FROM eval_runs"
        params: list[Any] = []
        if tenant_id is not None:
            sql += " WHERE tenant_id = $1"
            params.append(tenant_id)
        param_offset = len(params) + 1
        sql += f" ORDER BY started_at DESC LIMIT ${param_offset} OFFSET ${param_offset + 1}"
        params.extend([limit, offset])
        async with self._conn() as conn:
            rows = await conn.fetch(sql, *params)
            return [self._load_eval_run_row(row) for row in rows]

    # --- Eval Result operations ---

    async def _async_create_eval_result(self, result: EvalResult) -> EvalResult:
        """Create a new eval result."""
        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO eval_results
                (result_id, run_id, case_id, step_name, score, fixed,
                 details, evaluated_at, tenant_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                result.result_id,
                result.run_id,
                result.case_id,
                result.step_name,
                result.score,
                result.fixed,
                json.dumps(result.details),
                result.evaluated_at,
                result.tenant_id,
            )
        return result

    async def _async_get_eval_results_for_run(
        self, run_id: str, tenant_id: str | None = None
    ) -> list[EvalResult]:
        """Get all eval results for a run."""
        sql = "SELECT * FROM eval_results WHERE run_id = $1"
        params: list[Any] = [run_id]
        if tenant_id is not None:
            sql += " AND tenant_id = $2"
            params.append(tenant_id)
        async with self._conn() as conn:
            rows = await conn.fetch(sql, *params)
            results = []
            for row in rows:
                data = dict(row)
                data["details"] = _json_value(data.get("details"), {})
                results.append(EvalResult.model_validate(data))
            return results

    # Synchronous wrappers for compatibility with existing code
    def save_trace(self, trace: Trace) -> Path:
        return self._run_coro(self._async_save_trace(trace))

    def load_trace(self, trace_id: str) -> Trace | None:
        return self._run_coro(self._async_load_trace(trace_id))

    def list_traces(
        self,
        status: TraceStatus | None = None,
        limit: int = 100,
        offset: int = 0,
        tenant_id: str | None = None,
    ) -> list[Trace]:
        return self._run_coro(self._async_list_traces(status, limit, offset, tenant_id))

    def get_trace(self, trace_id: str, tenant_id: str | None = None) -> Trace | None:
        return self._run_coro(self._async_get_trace(trace_id, tenant_id))

    def latest_root_cause_steps(
        self, trace_ids: list[str], tenant_id: str | None = None
    ) -> dict[str, str]:
        return self._run_coro(self._async_latest_root_cause_steps(trace_ids, tenant_id))

    def create_flag(self, flag: Flag) -> Flag:
        return self._run_coro(self._async_create_flag(flag))

    def get_flag(self, flag_id: str, tenant_id: str | None = None) -> Flag | None:
        return self._run_coro(self._async_get_flag(flag_id, tenant_id))

    def list_flags_for_trace(self, trace_id: str, tenant_id: str | None = None) -> list[Flag]:
        return self._run_coro(self._async_list_flags_for_trace(trace_id, tenant_id))

    def create_diagnosis(self, diagnosis: Diagnosis) -> Diagnosis:
        return self._run_coro(self._async_create_diagnosis(diagnosis))

    def get_diagnosis(self, diagnosis_id: str, tenant_id: str | None = None) -> Diagnosis | None:
        return self._run_coro(self._async_get_diagnosis(diagnosis_id, tenant_id))

    def get_diagnosis_for_trace(
        self, trace_id: str, tenant_id: str | None = None
    ) -> Diagnosis | None:
        return self._run_coro(self._async_get_diagnosis_for_trace(trace_id, tenant_id))

    def update_diagnosis_confirmation(
        self,
        diagnosis_id: str,
        confirmed: bool,
        overridden: bool = False,
        override_category: str | None = None,
        override_reason: str | None = None,
        override_step: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        return self._run_coro(
            self._async_update_diagnosis_confirmation(
                diagnosis_id,
                confirmed,
                overridden,
                override_category,
                override_reason,
                override_step,
                tenant_id,
            )
        )

    def save_prompt_template(self, template: PromptTemplate) -> None:
        return self._run_coro(self._async_save_prompt_template(template))

    def get_prompt_template(
        self, name: str, version: str, tenant_id: str | None = None
    ) -> PromptTemplate | None:
        return self._run_coro(self._async_get_prompt_template(name, version, tenant_id))

    def list_prompt_templates(self, tenant_id: str | None = None) -> list[PromptTemplate]:
        return self._run_coro(self._async_list_prompt_templates(tenant_id))

    def find_eval_case(
        self, trace_id: str, failing_step: str, tenant_id: str | None = None
    ) -> EvalCase | None:
        return self._run_coro(self._async_find_eval_case(trace_id, failing_step, tenant_id))

    def create_eval_case(self, case: EvalCase) -> EvalCase:
        return self._run_coro(self._async_create_eval_case(case))

    def get_eval_case(self, case_id: str, tenant_id: str | None = None) -> EvalCase | None:
        return self._run_coro(self._async_get_eval_case(case_id, tenant_id))

    def list_eval_cases(
        self, limit: int = 100, offset: int = 0, tenant_id: str | None = None
    ) -> list[EvalCase]:
        return self._run_coro(self._async_list_eval_cases(limit, offset, tenant_id))

    def create_eval_run(self, run: EvalRun) -> EvalRun:
        return self._run_coro(self._async_create_eval_run(run))

    def update_eval_run(
        self,
        run_id: str,
        completed_at: datetime | None = None,
        passed_count: int | None = None,
        failed_count: int | None = None,
        total_cases: int | None = None,
        tenant_id: str | None = None,
    ) -> None:
        return self._run_coro(
            self._async_update_eval_run(
                run_id, completed_at, passed_count, failed_count, total_cases, tenant_id
            )
        )

    def get_eval_run(self, run_id: str, tenant_id: str | None = None) -> EvalRun | None:
        return self._run_coro(self._async_get_eval_run(run_id, tenant_id))

    def list_eval_runs(
        self, limit: int = 100, offset: int = 0, tenant_id: str | None = None
    ) -> list[EvalRun]:
        return self._run_coro(self._async_list_eval_runs(limit, offset, tenant_id))

    def create_eval_result(self, result: EvalResult) -> EvalResult:
        return self._run_coro(self._async_create_eval_result(result))

    def get_eval_results_for_run(
        self, run_id: str, tenant_id: str | None = None
    ) -> list[EvalResult]:
        return self._run_coro(self._async_get_eval_results_for_run(run_id, tenant_id))

    # --- Tenant operations ---

    async def _async_create_tenant(self, tenant: Tenant, ignore_existing: bool) -> None:
        conflict = "ON CONFLICT (tenant_id) DO NOTHING" if ignore_existing else ""
        async with self._conn() as conn:
            await conn.execute(
                f"""
                INSERT INTO tenants (tenant_id, name, created_at, active)
                VALUES ($1, $2, $3, $4)
                {conflict}
                """,
                tenant.tenant_id,
                tenant.name,
                tenant.created_at,
                tenant.active,
            )

    async def _async_get_tenant(self, tenant_id: str, active_only: bool) -> Tenant | None:
        sql = "SELECT * FROM tenants WHERE tenant_id = $1"
        if active_only:
            sql += " AND active = TRUE"
        async with self._conn() as conn:
            row = await conn.fetchrow(sql, tenant_id)
        return Tenant.model_validate(dict(row)) if row else None

    async def _async_list_tenants(self) -> list[Tenant]:
        async with self._conn() as conn:
            rows = await conn.fetch("SELECT * FROM tenants ORDER BY created_at")
        return [Tenant.model_validate(dict(r)) for r in rows]

    async def _async_set_tenant_active(self, tenant_id: str, active: bool) -> bool:
        async with self._conn() as conn:
            status = await conn.execute(
                "UPDATE tenants SET active = $1 WHERE tenant_id = $2", active, tenant_id
            )
        return str(status).endswith(" 1")

    # --- API key operations ---

    async def _async_create_api_key(self, key: APIKey) -> None:
        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO api_keys (
                    key_id, tenant_id, key_hash, prefix, scopes, name, created_at, expires_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                key.key_id,
                key.tenant_id,
                key.key_hash,
                key.prefix,
                ",".join(key.scopes),
                key.name,
                key.created_at,
                key.expires_at,
            )

    async def _async_list_api_keys_by_prefix(self, prefix: str) -> list[APIKey]:
        async with self._conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM api_keys WHERE prefix = $1 AND revoked_at IS NULL", prefix
            )
        return [_row_to_api_key(dict(r)) for r in rows]

    async def _async_touch_api_key(self, key_id: str, used_at: datetime) -> None:
        async with self._conn() as conn:
            await conn.execute(
                "UPDATE api_keys SET last_used_at = $1 WHERE key_id = $2", used_at, key_id
            )

    async def _async_list_api_keys(self, tenant_id: str) -> list[APIKey]:
        async with self._conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM api_keys WHERE tenant_id = $1 ORDER BY created_at", tenant_id
            )
        return [_row_to_api_key(dict(r)) for r in rows]

    async def _async_get_api_key(self, key_id: str) -> APIKey | None:
        async with self._conn() as conn:
            row = await conn.fetchrow("SELECT * FROM api_keys WHERE key_id = $1", key_id)
        return _row_to_api_key(dict(row)) if row else None

    async def _async_revoke_api_key(self, key_id: str, revoked_at: datetime) -> bool:
        async with self._conn() as conn:
            status = await conn.execute(
                "UPDATE api_keys SET revoked_at = $1 WHERE key_id = $2 AND revoked_at IS NULL",
                revoked_at,
                key_id,
            )
        return str(status).endswith(" 1")

    # Sync wrappers for the tenant / API-key contract.

    def create_tenant(self, tenant: Tenant, *, ignore_existing: bool = False) -> None:

        self._run_coro(self._async_create_tenant(tenant, ignore_existing))

    def get_tenant(self, tenant_id: str, *, active_only: bool = True) -> Tenant | None:
        return self._run_coro(self._async_get_tenant(tenant_id, active_only))

    def list_tenants(self) -> list[Tenant]:
        return self._run_coro(self._async_list_tenants())

    def set_tenant_active(self, tenant_id: str, active: bool) -> bool:
        return self._run_coro(self._async_set_tenant_active(tenant_id, active))

    def create_api_key(self, key: APIKey) -> None:

        self._run_coro(self._async_create_api_key(key))

    def list_api_keys_by_prefix(self, prefix: str) -> list[APIKey]:
        return self._run_coro(self._async_list_api_keys_by_prefix(prefix))

    def touch_api_key(self, key_id: str, used_at: datetime) -> None:

        self._run_coro(self._async_touch_api_key(key_id, used_at))

    def list_api_keys(self, tenant_id: str) -> list[APIKey]:
        return self._run_coro(self._async_list_api_keys(tenant_id))

    def get_api_key(self, key_id: str) -> APIKey | None:
        return self._run_coro(self._async_get_api_key(key_id))

    def revoke_api_key(self, key_id: str, revoked_at: datetime) -> bool:
        return self._run_coro(self._async_revoke_api_key(key_id, revoked_at))


__all__ = ["PostgresStorage"]
