"""PostgreSQL storage implementation using asyncpg.

This is an async-first implementation suitable for high-concurrency scenarios.
Uses the same schema as SQLite but with PostgreSQL-specific types and features.
"""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from forensics.models import (
    APIKey,
    Diagnosis,
    EvalCase,
    EvalResult,
    EvalRun,
    Flag,
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

    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create the connection pool."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._database_url,
                min_size=2,
                max_size=10,
                command_timeout=30,
            )
        return self._pool

    async def _aclose(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    def close(self) -> None:
        """Close the connection pool (sync wrapper, matching StorageBase)."""
        import asyncio

        asyncio.run(self._aclose())

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[asyncpg.Connection]:
        """Get a database connection from the pool."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            yield conn

    async def _init_schema(self) -> None:
        """Initialize all database tables in PostgreSQL."""
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
        """Save trace: finalize status/totals, then write JSON + PostgreSQL."""
        from forensics.tracing import finalize_trace

        finalize_trace(trace, traces_dir=self.traces_dir, db_path=self.db_path)
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
        if tenant_id is not None:
            async with self._conn() as conn:
                row = await conn.fetchrow(
                    "SELECT tenant_id FROM traces WHERE trace_id = $1", trace_id
                )
            if row is None or row["tenant_id"] != tenant_id:
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
        data["evidence_chain"] = data["evidence_chain"] or []
        data["step_scores"] = [StepScore.model_validate(s) for s in (data["step_scores"] or [])]
        data["judge_analysis"] = data["judge_analysis"]
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
    ) -> None:
        """Update diagnosis confirmation status."""
        from forensics.pii import create_redactor_from_settings

        if override_reason:
            redactor = create_redactor_from_settings()
            if redactor.enabled:
                override_reason, _ = redactor.redact(override_reason)

        async with self._conn() as conn:
            await conn.execute(
                """
                UPDATE diagnoses
                SET confirmed = $1, overridden = $2, override_category = $3, override_reason = $4,
                    override_step = COALESCE($5, override_step)
                WHERE diagnosis_id = $6
                """,
                confirmed,
                overridden,
                override_category,
                override_reason,
                override_step,
                diagnosis_id,
            )

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
        data["bad_output"] = data["bad_output"] or {}
        data["corrected_output"] = data["corrected_output"] or {}
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

        params.append(run_id)
        sql = f"UPDATE eval_runs SET {', '.join(updates)} WHERE run_id = ${param_num}"
        async with self._conn() as conn:
            await conn.execute(sql, *params)

    def _load_eval_run_row(self, row: asyncpg.Record) -> EvalRun:
        data = dict(row)
        data["prompt_versions"] = data["prompt_versions"] or {}
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
                data["details"] = data["details"] or {}
                results.append(EvalResult.model_validate(data))
            return results

    # Synchronous wrappers for compatibility with existing code
    def save_trace(self, trace: Trace) -> Path:
        import asyncio

        return asyncio.run(self._async_save_trace(trace))

    def load_trace(self, trace_id: str) -> Trace | None:
        import asyncio

        return asyncio.run(self._async_load_trace(trace_id))

    def list_traces(
        self,
        status: TraceStatus | None = None,
        limit: int = 100,
        offset: int = 0,
        tenant_id: str | None = None,
    ) -> list[Trace]:
        import asyncio

        return asyncio.run(self._async_list_traces(status, limit, offset, tenant_id))

    def get_trace(self, trace_id: str, tenant_id: str | None = None) -> Trace | None:
        import asyncio

        return asyncio.run(self._async_get_trace(trace_id, tenant_id))

    def latest_root_cause_steps(
        self, trace_ids: list[str], tenant_id: str | None = None
    ) -> dict[str, str]:
        import asyncio

        return asyncio.run(self._async_latest_root_cause_steps(trace_ids, tenant_id))

    def create_flag(self, flag: Flag) -> Flag:
        import asyncio

        return asyncio.run(self._async_create_flag(flag))

    def get_flag(self, flag_id: str, tenant_id: str | None = None) -> Flag | None:
        import asyncio

        return asyncio.run(self._async_get_flag(flag_id, tenant_id))

    def list_flags_for_trace(self, trace_id: str, tenant_id: str | None = None) -> list[Flag]:
        import asyncio

        return asyncio.run(self._async_list_flags_for_trace(trace_id, tenant_id))

    def create_diagnosis(self, diagnosis: Diagnosis) -> Diagnosis:
        import asyncio

        return asyncio.run(self._async_create_diagnosis(diagnosis))

    def get_diagnosis(self, diagnosis_id: str, tenant_id: str | None = None) -> Diagnosis | None:
        import asyncio

        return asyncio.run(self._async_get_diagnosis(diagnosis_id, tenant_id))

    def get_diagnosis_for_trace(
        self, trace_id: str, tenant_id: str | None = None
    ) -> Diagnosis | None:
        import asyncio

        return asyncio.run(self._async_get_diagnosis_for_trace(trace_id, tenant_id))

    def update_diagnosis_confirmation(
        self,
        diagnosis_id: str,
        confirmed: bool,
        overridden: bool = False,
        override_category: str | None = None,
        override_reason: str | None = None,
        override_step: str | None = None,
    ) -> None:
        import asyncio

        return asyncio.run(
            self._async_update_diagnosis_confirmation(
                diagnosis_id,
                confirmed,
                overridden,
                override_category,
                override_reason,
                override_step,
            )
        )

    def find_eval_case(
        self, trace_id: str, failing_step: str, tenant_id: str | None = None
    ) -> EvalCase | None:
        import asyncio

        return asyncio.run(self._async_find_eval_case(trace_id, failing_step, tenant_id))

    def create_eval_case(self, case: EvalCase) -> EvalCase:
        import asyncio

        return asyncio.run(self._async_create_eval_case(case))

    def get_eval_case(self, case_id: str, tenant_id: str | None = None) -> EvalCase | None:
        import asyncio

        return asyncio.run(self._async_get_eval_case(case_id, tenant_id))

    def list_eval_cases(
        self, limit: int = 100, offset: int = 0, tenant_id: str | None = None
    ) -> list[EvalCase]:
        import asyncio

        return asyncio.run(self._async_list_eval_cases(limit, offset, tenant_id))

    def create_eval_run(self, run: EvalRun) -> EvalRun:
        import asyncio

        return asyncio.run(self._async_create_eval_run(run))

    def update_eval_run(
        self,
        run_id: str,
        completed_at: datetime | None = None,
        passed_count: int | None = None,
        failed_count: int | None = None,
        total_cases: int | None = None,
    ) -> None:
        import asyncio

        return asyncio.run(
            self._async_update_eval_run(
                run_id, completed_at, passed_count, failed_count, total_cases
            )
        )

    def get_eval_run(self, run_id: str, tenant_id: str | None = None) -> EvalRun | None:
        import asyncio

        return asyncio.run(self._async_get_eval_run(run_id, tenant_id))

    def list_eval_runs(
        self, limit: int = 100, offset: int = 0, tenant_id: str | None = None
    ) -> list[EvalRun]:
        import asyncio

        return asyncio.run(self._async_list_eval_runs(limit, offset, tenant_id))

    def create_eval_result(self, result: EvalResult) -> EvalResult:
        import asyncio

        return asyncio.run(self._async_create_eval_result(result))

    def get_eval_results_for_run(
        self, run_id: str, tenant_id: str | None = None
    ) -> list[EvalResult]:
        import asyncio

        return asyncio.run(self._async_get_eval_results_for_run(run_id, tenant_id))

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
                    key_id, tenant_id, key_hash, prefix, name, created_at, expires_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                key.key_id,
                key.tenant_id,
                key.key_hash,
                key.prefix,
                key.name,
                key.created_at,
                key.expires_at,
            )

    async def _async_list_api_keys_by_prefix(self, prefix: str) -> list[APIKey]:
        async with self._conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM api_keys WHERE prefix = $1 AND revoked_at IS NULL", prefix
            )
        return [APIKey.model_validate(dict(r)) for r in rows]

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
        return [APIKey.model_validate(dict(r)) for r in rows]

    async def _async_get_api_key(self, key_id: str) -> APIKey | None:
        async with self._conn() as conn:
            row = await conn.fetchrow("SELECT * FROM api_keys WHERE key_id = $1", key_id)
        return APIKey.model_validate(dict(row)) if row else None

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
        import asyncio

        asyncio.run(self._async_create_tenant(tenant, ignore_existing))

    def get_tenant(self, tenant_id: str, *, active_only: bool = True) -> Tenant | None:
        import asyncio

        return asyncio.run(self._async_get_tenant(tenant_id, active_only))

    def list_tenants(self) -> list[Tenant]:
        import asyncio

        return asyncio.run(self._async_list_tenants())

    def set_tenant_active(self, tenant_id: str, active: bool) -> bool:
        import asyncio

        return asyncio.run(self._async_set_tenant_active(tenant_id, active))

    def create_api_key(self, key: APIKey) -> None:
        import asyncio

        asyncio.run(self._async_create_api_key(key))

    def list_api_keys_by_prefix(self, prefix: str) -> list[APIKey]:
        import asyncio

        return asyncio.run(self._async_list_api_keys_by_prefix(prefix))

    def touch_api_key(self, key_id: str, used_at: datetime) -> None:
        import asyncio

        asyncio.run(self._async_touch_api_key(key_id, used_at))

    def list_api_keys(self, tenant_id: str) -> list[APIKey]:
        import asyncio

        return asyncio.run(self._async_list_api_keys(tenant_id))

    def get_api_key(self, key_id: str) -> APIKey | None:
        import asyncio

        return asyncio.run(self._async_get_api_key(key_id))

    def revoke_api_key(self, key_id: str, revoked_at: datetime) -> bool:
        import asyncio

        return asyncio.run(self._async_revoke_api_key(key_id, revoked_at))


__all__ = ["PostgresStorage"]
