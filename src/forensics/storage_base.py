"""Abstract storage interface for pluggable backends (SQLite, PostgreSQL).

This defines the contract that all storage backends must implement.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from forensics.models import (
    Diagnosis,
    EvalCase,
    EvalResult,
    EvalRun,
    Flag,
    Trace,
    TraceStatus,
)


class StorageBase(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    def __init__(
        self,
        traces_dir: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        """Initialize the storage backend."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close any open connections."""
        ...

    # --- Trace operations ---

    @abstractmethod
    def save_trace(self, trace: Trace) -> Path:
        """Save trace: finalize status/totals, then write JSON + index."""
        ...

    @abstractmethod
    def load_trace(self, trace_id: str) -> Trace | None:
        """Load trace from JSON file."""
        ...

    @abstractmethod
    def list_traces(
        self,
        status: TraceStatus | None = None,
        limit: int = 100,
        offset: int = 0,
        tenant_id: str | None = None,
    ) -> list[Trace]:
        """List traces from index."""
        ...

    @abstractmethod
    def get_trace(self, trace_id: str, tenant_id: str | None = None) -> Trace | None:
        """Get a single trace by ID, optionally scoped to one tenant."""
        ...

    @abstractmethod
    def latest_root_cause_steps(
        self, trace_ids: list[str], tenant_id: str | None = None
    ) -> dict[str, str]:
        """Get latest root cause step for each trace ID."""
        ...

    # --- Flag operations ---

    @abstractmethod
    def create_flag(self, flag: Flag) -> Flag:
        """Create a new flag."""
        ...

    @abstractmethod
    def get_flag(self, flag_id: str, tenant_id: str | None = None) -> Flag | None:
        """Get a flag by ID."""
        ...

    @abstractmethod
    def list_flags_for_trace(self, trace_id: str, tenant_id: str | None = None) -> list[Flag]:
        """List all flags for a trace."""
        ...

    # --- Diagnosis operations ---

    @abstractmethod
    def create_diagnosis(self, diagnosis: Diagnosis) -> Diagnosis:
        """Create a new diagnosis."""
        ...

    @abstractmethod
    def get_diagnosis(self, diagnosis_id: str, tenant_id: str | None = None) -> Diagnosis | None:
        """Get a diagnosis by ID."""
        ...

    @abstractmethod
    def get_diagnosis_for_trace(
        self, trace_id: str, tenant_id: str | None = None
    ) -> Diagnosis | None:
        """Get the diagnosis for a trace (latest)."""
        ...

    @abstractmethod
    def update_diagnosis_confirmation(
        self,
        diagnosis_id: str,
        confirmed: bool,
        overridden: bool = False,
        override_category: str | None = None,
        override_reason: str | None = None,
        override_step: str | None = None,
    ) -> None:
        """Update diagnosis confirmation status."""
        ...

    # --- Eval Case operations ---

    @abstractmethod
    def find_eval_case(
        self, trace_id: str, failing_step: str, tenant_id: str | None = None
    ) -> EvalCase | None:
        """Find an eval case by trace and failing step."""
        ...

    @abstractmethod
    def create_eval_case(self, case: EvalCase) -> EvalCase:
        """Create a new eval case, or return existing."""
        ...

    @abstractmethod
    def get_eval_case(self, case_id: str, tenant_id: str | None = None) -> EvalCase | None:
        """Get an eval case by ID."""
        ...

    @abstractmethod
    def list_eval_cases(
        self, limit: int = 100, offset: int = 0, tenant_id: str | None = None
    ) -> list[EvalCase]:
        """List all eval cases."""
        ...

    # --- Eval Run operations ---

    @abstractmethod
    def create_eval_run(self, run: EvalRun) -> EvalRun:
        """Create a new eval run."""
        ...

    @abstractmethod
    def update_eval_run(
        self,
        run_id: str,
        completed_at: datetime | None = None,
        passed_count: int | None = None,
        failed_count: int | None = None,
        total_cases: int | None = None,
    ) -> None:
        """Update eval run completion status."""
        ...

    @abstractmethod
    def get_eval_run(self, run_id: str, tenant_id: str | None = None) -> EvalRun | None:
        """Get an eval run by ID."""
        ...

    @abstractmethod
    def list_eval_runs(
        self, limit: int = 100, offset: int = 0, tenant_id: str | None = None
    ) -> list[EvalRun]:
        """List all eval runs."""
        ...

    # --- Eval Result operations ---

    @abstractmethod
    def create_eval_result(self, result: EvalResult) -> EvalResult:
        """Create a new eval result."""
        ...

    @abstractmethod
    def get_eval_results_for_run(
        self, run_id: str, tenant_id: str | None = None
    ) -> list[EvalResult]:
        """Get all eval results for a run."""
        ...


__all__ = ["StorageBase"]
