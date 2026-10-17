"""Storage — per-trace JSON payloads plus a SQLite index and relational tables.

Split rationale: full span payloads live in git-friendly, inspectable JSON
files; SQLite holds the index and the relational tables for flags, diagnoses,
eval cases, and eval runs. Owns atomic writes and the consistency story between
file and index.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forensics.migrations import migrate
from forensics.models import (
    Diagnosis,
    EvalCase,
    EvalResult,
    EvalRun,
    Flag,
    Trace,
    TraceStatus,
)


def _default_traces_dir() -> Path:
    from forensics.config import get_settings

    try:
        return get_settings().traces_dir
    except Exception:
        return Path("data/traces")


def _default_db_path() -> Path:
    from forensics.config import get_settings

    try:
        return get_settings().db_path
    except Exception:
        return Path("data/forensics.db")


class Storage:
    """Unified storage for traces (JSON + SQLite) and relational data (SQLite)."""

    def __init__(
        self,
        traces_dir: Path | None = None,
        db_path: Path | None = None,
    ):
        self.traces_dir = traces_dir or _default_traces_dir()
        self.db_path = db_path or _default_db_path()
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize all database tables."""
        with self._conn() as conn:
            # Traces and spans (from tracing module)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    total_latency_ms INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    total_cost_usd REAL DEFAULT 0.0,
                    pipeline_version TEXT,
                    schema_version INTEGER DEFAULT 1,
                    tenant_id TEXT NOT NULL DEFAULT 'default'
                )
            """
            )
            conn.execute(
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
                    started_at TEXT,
                    ended_at TEXT,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
                )
            """
            )

            # Flags table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flags (
                    flag_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    user_id TEXT DEFAULT 'anonymous',
                    note TEXT,
                    created_at TEXT NOT NULL,
                    diagnosis_id TEXT,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
                )
            """
            )

            # Diagnoses table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnoses (
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
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
                )
            """
            )

            # Eval cases table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS eval_cases (
                    case_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    failing_step TEXT NOT NULL,
                    bad_output TEXT NOT NULL,
                    corrected_output TEXT NOT NULL,
                    category TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    pipeline_version TEXT,
                    prompt_version TEXT,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
                )
            """
            )

            # Eval runs table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS eval_runs (
                    run_id TEXT PRIMARY KEY,
                    pipeline_version TEXT NOT NULL,
                    prompt_versions TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    total_cases INTEGER DEFAULT 0,
                    passed_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    tenant_id TEXT NOT NULL DEFAULT 'default'
                )
            """
            )

            # Eval results table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS eval_results (
                    result_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    score REAL NOT NULL,
                    fixed BOOLEAN NOT NULL,
                    details TEXT,
                    evaluated_at TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY (run_id) REFERENCES eval_runs(run_id),
                    FOREIGN KEY (case_id) REFERENCES eval_cases(case_id)
                )
            """
            )

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_document_id ON traces(document_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flags_trace_id ON flags(trace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_diagnoses_trace_id ON diagnoses(trace_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_cases_trace_id ON eval_cases(trace_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_results_run_id ON eval_results(run_id)"
            )

            conn.commit()

        # Close the gap for a database created before multi-tenancy (or by
        # `tracing.init_db`, which only knows about traces and spans).
        with self._conn() as conn:
            migrate(conn)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with WAL mode."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- Trace operations ---

    def save_trace(self, trace: Trace) -> Path:
        """Save trace: finalize status/totals, then write JSON + SQLite.

        Routed through `finalize_trace` so a trace saved here carries the same
        derived status and token/cost totals as one saved by the CLI. Writing
        JSON and SQLite directly would silently leave those fields at defaults.
        """
        from forensics.tracing import finalize_trace

        finalize_trace(trace, traces_dir=self.traces_dir, db_path=self.db_path)
        return self.traces_dir / f"{trace.trace_id}.json"

    def load_trace(self, trace_id: str) -> Trace | None:
        """Load trace from JSON file."""
        json_path = self.traces_dir / f"{trace_id}.json"
        if not json_path.exists():
            return None
        import json

        with open(json_path) as f:
            data = json.load(f)
        return Trace.model_validate(data)

    def list_traces(
        self,
        status: TraceStatus | None = None,
        limit: int = 100,
        offset: int = 0,
        tenant_id: str | None = None,
    ) -> list[Trace]:
        """List traces from SQLite index.

        `tenant_id=None` means "no filter" — that is what dev mode (auth
        disabled) and the CLI operators want. When a tenant is given the filter
        is applied in SQL, not in Python, so `limit`/`offset` stay meaningful.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if status:
            clauses.append("status = ?")
            params.append(status.value)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT trace_id FROM traces{where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        traces = []
        for (trace_id,) in rows:
            trace = self.load_trace(trace_id)
            if trace:
                traces.append(trace)
        return traces

    def get_trace(self, trace_id: str, tenant_id: str | None = None) -> Trace | None:
        """Get a single trace by ID, optionally scoped to one tenant.

        The tenant check reads the SQLite index rather than the JSON payload,
        because the index is the authority on ownership and a trace whose JSON
        file exists but whose index row is gone must not be served to anyone.
        """
        if tenant_id is not None:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT tenant_id FROM traces WHERE trace_id = ?", (trace_id,)
                ).fetchone()
            if row is None or row[0] != tenant_id:
                return None
        return self.load_trace(trace_id)

    # --- Flag operations ---

    def create_flag(self, flag: Flag) -> Flag:
        """Create a new flag."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO flags
                (flag_id, trace_id, user_id, note, created_at, diagnosis_id, tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    flag.flag_id,
                    flag.trace_id,
                    flag.user_id,
                    flag.note,
                    flag.created_at.isoformat(),
                    flag.diagnosis_id,
                    flag.tenant_id,
                ),
            )
        return flag

    def get_flag(self, flag_id: str, tenant_id: str | None = None) -> Flag | None:
        """Get a flag by ID."""
        sql = "SELECT * FROM flags WHERE flag_id = ?"
        params: list[Any] = [flag_id]
        if tenant_id is not None:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return Flag.model_validate(dict(zip(cols, row, strict=False)))

    def list_flags_for_trace(self, trace_id: str, tenant_id: str | None = None) -> list[Flag]:
        """List all flags for a trace."""
        sql = "SELECT * FROM flags WHERE trace_id = ?"
        params: list[Any] = [trace_id]
        if tenant_id is not None:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        sql += " ORDER BY created_at DESC"
        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            return [Flag.model_validate(dict(zip(cols, r, strict=False))) for r in rows]

    # --- Diagnosis operations ---

    def create_diagnosis(self, diagnosis: Diagnosis) -> Diagnosis:
        """Create a new diagnosis."""
        import json

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO diagnoses
                (diagnosis_id, trace_id, root_cause_step, category, confidence,
                 evidence_chain, step_scores, analyzer_version, model_id, provider,
                 created_at, confirmed, overridden, override_category, override_reason,
                 tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
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
                    diagnosis.created_at.isoformat(),
                    int(diagnosis.confirmed),
                    int(diagnosis.overridden),
                    diagnosis.override_category.value if diagnosis.override_category else None,
                    diagnosis.override_reason,
                    diagnosis.tenant_id,
                ),
            )
        return diagnosis

    def _load_diagnosis_row(self, row: Any, cols: list[str]) -> Diagnosis:
        import json

        from forensics.models import StepScore

        data = dict(zip(cols, row, strict=False))
        data["evidence_chain"] = json.loads(data["evidence_chain"] or "[]")
        data["step_scores"] = json.loads(data["step_scores"] or "[]")
        data["step_scores"] = [StepScore.model_validate(s) for s in data["step_scores"]]
        return Diagnosis.model_validate(data)

    def get_diagnosis(self, diagnosis_id: str, tenant_id: str | None = None) -> Diagnosis | None:
        """Get a diagnosis by ID."""
        sql = "SELECT * FROM diagnoses WHERE diagnosis_id = ?"
        params: list[Any] = [diagnosis_id]
        if tenant_id is not None:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if not row:
                return None
            return self._load_diagnosis_row(row, [d[0] for d in cursor.description])

    def get_diagnosis_for_trace(
        self, trace_id: str, tenant_id: str | None = None
    ) -> Diagnosis | None:
        """Get the diagnosis for a trace (latest)."""
        sql = "SELECT * FROM diagnoses WHERE trace_id = ?"
        params: list[Any] = [trace_id]
        if tenant_id is not None:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        sql += " ORDER BY created_at DESC LIMIT 1"
        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if not row:
                return None
            return self._load_diagnosis_row(row, [d[0] for d in cursor.description])

    def update_diagnosis_confirmation(
        self,
        diagnosis_id: str,
        confirmed: bool,
        overridden: bool = False,
        override_category: str | None = None,
        override_reason: str | None = None,
    ) -> None:
        """Update diagnosis confirmation status."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE diagnoses
                SET confirmed = ?, overridden = ?, override_category = ?, override_reason = ?
                WHERE diagnosis_id = ?
            """,
                (int(confirmed), int(overridden), override_category, override_reason, diagnosis_id),
            )

    # --- Eval Case operations ---

    def create_eval_case(self, case: EvalCase) -> EvalCase:
        """Create a new eval case."""
        import json

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO eval_cases
                (case_id, trace_id, failing_step, bad_output, corrected_output,
                 category, created_at, pipeline_version, prompt_version, tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    case.case_id,
                    case.trace_id,
                    case.failing_step,
                    json.dumps(case.bad_output),
                    json.dumps(case.corrected_output),
                    case.category.value,
                    case.created_at.isoformat(),
                    case.pipeline_version,
                    case.prompt_version,
                    case.tenant_id,
                ),
            )
        return case

    @staticmethod
    def _load_eval_case_row(row: Any, cols: list[str]) -> EvalCase:
        import json

        data = dict(zip(cols, row, strict=False))
        data["bad_output"] = json.loads(data["bad_output"])
        data["corrected_output"] = json.loads(data["corrected_output"])
        return EvalCase.model_validate(data)

    def get_eval_case(self, case_id: str, tenant_id: str | None = None) -> EvalCase | None:
        """Get an eval case by ID."""
        sql = "SELECT * FROM eval_cases WHERE case_id = ?"
        params: list[Any] = [case_id]
        if tenant_id is not None:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if not row:
                return None
            return self._load_eval_case_row(row, [d[0] for d in cursor.description])

    def list_eval_cases(
        self, limit: int = 100, offset: int = 0, tenant_id: str | None = None
    ) -> list[EvalCase]:
        """List all eval cases."""
        sql = "SELECT * FROM eval_cases"
        params: list[Any] = []
        if tenant_id is not None:
            sql += " WHERE tenant_id = ?"
            params.append(tenant_id)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            return [self._load_eval_case_row(row, cols) for row in rows]

    # --- Eval Run operations ---

    def create_eval_run(self, run: EvalRun) -> EvalRun:
        """Create a new eval run."""
        import json

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO eval_runs
                (run_id, pipeline_version, prompt_versions, started_at, completed_at,
                 total_cases, passed_count, failed_count, tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    run.run_id,
                    run.pipeline_version,
                    json.dumps(run.prompt_versions),
                    run.started_at.isoformat(),
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.total_cases,
                    run.passed_count,
                    run.failed_count,
                    run.tenant_id,
                ),
            )
        return run

    def update_eval_run(
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
        # Store an ISO string, not a datetime object: sqlite3's default
        # datetime adapter is deprecated and removed in Python 3.14.
        completed_iso = (
            completed_at.isoformat() if isinstance(completed_at, datetime) else str(completed_at)
        )

        with self._conn() as conn:
            updates = ["completed_at = ?"]
            params: list[Any] = [completed_iso]
            if total_cases is not None:
                updates.append("total_cases = ?")
                params.append(total_cases)
            if passed_count is not None:
                updates.append("passed_count = ?")
                params.append(passed_count)
            if failed_count is not None:
                updates.append("failed_count = ?")
                params.append(failed_count)
            params.append(run_id)
            conn.execute(f"UPDATE eval_runs SET {', '.join(updates)} WHERE run_id = ?", params)

    @staticmethod
    def _load_eval_run_row(row: Any, cols: list[str]) -> EvalRun:
        import json

        data = dict(zip(cols, row, strict=False))
        # The table stores NULL (not "") for an incomplete run; loading
        # "" back failed datetime validation on EvalRun.
        data["prompt_versions"] = json.loads(data["prompt_versions"] or "{}")
        return EvalRun.model_validate(data)

    def get_eval_run(self, run_id: str, tenant_id: str | None = None) -> EvalRun | None:
        """Get an eval run by ID."""
        sql = "SELECT * FROM eval_runs WHERE run_id = ?"
        params: list[Any] = [run_id]
        if tenant_id is not None:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if not row:
                return None
            return self._load_eval_run_row(row, [d[0] for d in cursor.description])

    def list_eval_runs(
        self, limit: int = 100, offset: int = 0, tenant_id: str | None = None
    ) -> list[EvalRun]:
        """List all eval runs."""
        sql = "SELECT * FROM eval_runs"
        params: list[Any] = []
        if tenant_id is not None:
            sql += " WHERE tenant_id = ?"
            params.append(tenant_id)
        sql += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            return [self._load_eval_run_row(row, cols) for row in rows]

    # --- Eval Result operations ---

    def create_eval_result(self, result: EvalResult) -> EvalResult:
        """Create a new eval result."""
        import json

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO eval_results
                (result_id, run_id, case_id, step_name, score, fixed, details, evaluated_at,
                 tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    result.result_id,
                    result.run_id,
                    result.case_id,
                    result.step_name,
                    result.score,
                    int(result.fixed),
                    json.dumps(result.details),
                    result.evaluated_at.isoformat(),
                    result.tenant_id,
                ),
            )
        return result

    def get_eval_results_for_run(
        self, run_id: str, tenant_id: str | None = None
    ) -> list[EvalResult]:
        """Get all eval results for a run."""
        import json

        sql = "SELECT * FROM eval_results WHERE run_id = ?"
        params: list[Any] = [run_id]
        if tenant_id is not None:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            results = []
            for row in rows:
                data = dict(zip(cols, row, strict=False))
                data["details"] = json.loads(data["details"] or "{}")
                results.append(EvalResult.model_validate(data))
            return results
