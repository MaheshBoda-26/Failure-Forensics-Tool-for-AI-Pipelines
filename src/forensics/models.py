"""Pydantic domain models.

Covers Document, IntakeOutput, ExtractionOutput, ClassificationOutput,
SummaryOutput, Span, Trace, StepScore, Diagnosis, Flag, EvalCase, EvalRun,
EvalResult. Every record carries pipeline_version, prompt_version, and
schema_version; Span and Diagnosis additionally carry model id and pinned
vendor attribution, since the recorded provider field is part of a
result's identity.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(UTC)


#: Tenant that owns every record created before multi-tenancy existed, and every
#: record created while auth is disabled. It must match the `DEFAULT 'default'`
#: in the SQLite migration, or freshly migrated rows would be unreachable.
DEFAULT_TENANT_ID = "default"


class FailureCategory(StrEnum):
    """Failure taxonomy categories."""

    EXTRACTION_HALLUCINATION = "extraction_hallucination"
    MISCLASSIFICATION = "misclassification"
    PROPAGATION_ERROR = "propagation_error"
    PROMPT_FAILURE = "prompt_failure"
    CONTEXT_LOSS = "context_loss"
    UNKNOWN = "unknown"


class DocumentType(StrEnum):
    """Document classification types.

    `mixed` is a real, first-class type: the dataset contains documents that
    genuinely combine several document shapes (e.g. a proposal carrying
    contract terms). Leaving it out of the enum made those documents
    unclassifiable by construction, so the classifier could never match their
    ground-truth label.
    """

    CONTRACT = "contract"
    INVOICE = "invoice"
    REPORT = "report"
    CORRESPONDENCE = "correspondence"
    MIXED = "mixed"


class Document(BaseModel):
    """Input document."""

    # A doc id becomes a filename component (`documents/<doc_id>.txt`), so it is
    # constrained at the model boundary as well as at the path build site.
    doc_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:12],
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="Filename-safe document identifier",
    )
    content: str = Field(..., min_length=1, description="Raw document text")
    type: DocumentType | None = Field(default=None, description="Ground truth type if known")
    metadata: dict[str, Any] = Field(default_factory=dict)


class Entity(BaseModel):
    """Extracted entity with source span."""

    value: str = Field(..., min_length=1)
    entity_type: str = Field(..., description="e.g., person, date, amount, organization")
    start_offset: int = Field(..., ge=0, description="Character offset in source document")
    end_offset: int = Field(..., ge=0, description="Character offset in source document")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("end_offset")
    @classmethod
    def validate_offsets(cls, v: int, info: Any) -> int:
        if "start_offset" in info.data and v < info.data["start_offset"]:
            raise ValueError("end_offset must be >= start_offset")
        return v


class IntakeOutput(BaseModel):
    """Output of intake step (pass-through with metadata)."""

    document: Document
    cleaned_text: str = Field(..., description="Normalized/cleaned text")
    char_count: int = Field(..., ge=0)
    detected_encoding: str | None = None


class ExtractionOutput(BaseModel):
    """Output of extraction step."""

    entities: list[Entity] = Field(default_factory=list)
    raw_response: str = Field(default="", description="Raw LLM response for debugging")


class ClassificationOutput(BaseModel):
    """Output of classification step."""

    document_type: DocumentType
    confidence: float = Field(..., ge=0.0, le=1.0)
    all_scores: dict[DocumentType, float] = Field(default_factory=dict)


class SummaryOutput(BaseModel):
    """Output of summarization step."""

    summary: str = Field(..., min_length=1)
    key_points: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineOutput(BaseModel):
    """Complete pipeline output combining all steps."""

    intake: IntakeOutput
    extraction: ExtractionOutput
    classification: ClassificationOutput
    summarization: SummaryOutput


def _get_pipeline_version() -> str:
    """Get pipeline version from settings, with fallback."""
    try:
        from forensics.config import get_settings

        return get_settings().pipeline_version
    except Exception:
        return "0.1.0"


def _get_prompt_version() -> str:
    """Get prompt version from settings, with fallback."""
    try:
        from forensics.config import get_settings

        return get_settings().pipeline_version  # Use pipeline_version as proxy
    except Exception:
        return "dev"


class VersionedModel(BaseModel):
    """Mixin for version tracking."""

    pipeline_version: str = Field(
        default_factory=_get_pipeline_version, description="Pipeline code version"
    )
    prompt_version: str = Field(
        default_factory=_get_prompt_version, description="Prompt content hash"
    )
    schema_version: int = Field(default=1, description="Schema version")


class TenantScoped(BaseModel):
    """Mixin for records that belong to exactly one tenant.

    `tenant_id` defaults to ``"default"`` so every record created before
    multi-tenancy existed — and every record created in dev mode, where auth is
    off — lands in one well-known tenant instead of becoming invisible.
    """

    tenant_id: str = Field(
        default=DEFAULT_TENANT_ID,
        description="Owning tenant; matches the SQLite migration default",
    )


class Span(VersionedModel, TenantScoped):
    """Single step trace span."""

    span_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    trace_id: str = Field(..., description="Parent trace ID")
    step_name: str = Field(
        ..., description="e.g., intake, extraction, classification, summarization"
    )
    parent_span_id: str | None = None

    # Input/Output
    input_json: str = Field(..., description="Serialized input to this step")
    output_json: str = Field(..., description="Serialized output from this step")

    # LLM details
    model_id: str = Field(..., description="Exact model slug used (e.g., openai/gpt-4o-mini)")
    provider: str = Field(..., description="Pinned provider (e.g., openai)")
    prompt: str = Field(..., description="Full prompt sent to LLM")
    raw_response: str = Field(..., description="Raw LLM response")

    # Metrics
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)

    # Quality signals
    confidence: int = Field(default=3, ge=1, le=5, description="Self-reported confidence 1-5")
    error: str | None = Field(default=None, description="Error message if step failed")
    status: Literal["success", "failure", "degraded"] = Field(default="success")
    #: True when this step's response came from the response cache. Cached work
    #: costs nothing, so `_span_cost_usd` must not bill the recorded tokens.
    cached: bool = Field(default=False, description="Response served from cache")

    # Timing
    started_at: datetime = Field(default_factory=_utc_now)
    ended_at: datetime | None = None


class TraceStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DEGRADED = "degraded"


class Trace(VersionedModel, TenantScoped):
    """Complete execution trace."""

    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    document_id: str
    spans: list[Span] = Field(default_factory=list)
    final_output: PipelineOutput | None = None
    status: TraceStatus = TraceStatus.SUCCESS
    final_score: float | None = Field(default=None, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    total_latency_ms: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    #: Latest diagnosis verdict for this trace, populated on list responses so a
    #: client can distinguish a semantic failure (the run completed, the output
    #: was wrong) from a clean run without a second request per row. `None`
    #: means "no diagnosis on file"; `"none"` means "diagnosed clean".
    root_cause_step: str | None = Field(
        default=None,
        description="Root cause from the latest diagnosis, when one exists",
    )
    #: Ground-truth document type (from dataset labels or user-supplied on a
    #: custom run), exposed so the UI and re-diagnosis know the expected type.
    expected_type: DocumentType | None = Field(
        default=None,
        description="Ground-truth document type when known",
    )


class StepScore(BaseModel):
    """Quality score for a single step from the analyzer."""

    step_name: str
    local_fidelity: float = Field(..., ge=0.0, le=1.0, description="Faithful to own input?")
    global_fidelity: float = Field(..., ge=0.0, le=1.0, description="Correct relative to source?")
    local_pass: bool
    global_pass: bool
    evidence: list[str] = Field(default_factory=list)
    deterministic_checks: dict[str, bool] = Field(default_factory=dict)


class Diagnosis(VersionedModel, TenantScoped):
    """Root cause diagnosis from backward trace analysis."""

    diagnosis_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    trace_id: str
    root_cause_step: str = Field(..., description="Step name identified as root cause")
    category: FailureCategory
    confidence: float = Field(..., ge=0.0, le=1.0, description="Analyzer confidence in diagnosis")
    evidence_chain: list[str] = Field(default_factory=list, description="Human-readable evidence")
    step_scores: list[StepScore] = Field(default_factory=list)
    analyzer_version: str
    model_id: str = Field(..., description="Judge model used for diagnosis")
    provider: str = Field(..., description="Judge model provider")
    created_at: datetime = Field(default_factory=_utc_now)
    confirmed: bool = Field(default=False, description="Human confirmed this diagnosis")
    overridden: bool = Field(default=False, description="Human overrode this diagnosis")
    override_category: FailureCategory | None = None
    override_step: str | None = Field(
        default=None,
        description="Human-supplied root-cause step, overriding the analyzer's",
    )
    override_reason: str | None = None
    # Judge LLM analysis (optional, populated when judge runs)
    judge_analysis: "JudgeAnalysis | None" = None
    analyzer_fallback: bool = Field(
        default=False, description="True if judge overrode deterministic verdict"
    )


class Flag(TenantScoped):
    """Human flag on a trace."""

    flag_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    trace_id: str
    user_id: str = Field(default="anonymous")
    note: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    diagnosis_id: str | None = None


class EvalCase(TenantScoped):
    """Evaluation case generated from a confirmed flag."""

    case_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    trace_id: str
    failing_step: str
    bad_output: dict[str, Any] = Field(..., description="The bad output (serialized)")
    corrected_output: dict[str, Any] = Field(..., description="Human-corrected output (serialized)")
    category: FailureCategory
    created_at: datetime = Field(default_factory=_utc_now)
    pipeline_version: str
    prompt_version: str


class EvalRun(TenantScoped):
    """Single evaluation run against the current pipeline."""

    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    pipeline_version: str
    prompt_versions: dict[str, str] = Field(..., description="Per-step prompt content hashes")
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    total_cases: int = 0
    passed_count: int = 0
    failed_count: int = 0


class EvalResult(TenantScoped):
    """Result of evaluating one case in a run."""

    result_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    run_id: str
    case_id: str
    step_name: str
    score: float = Field(..., ge=0.0, le=1.0)
    fixed: bool = Field(..., description="Whether this case is now fixed")
    details: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime = Field(default_factory=_utc_now)


class AnalyticsSnapshot(BaseModel):
    """Aggregated analytics for dashboard."""

    total_traces: int = 0
    failure_rate: float = 0.0
    failures_by_category: dict[FailureCategory, int] = Field(default_factory=dict)
    failures_by_step: dict[str, int] = Field(default_factory=dict)
    failure_rate_over_time: list[dict[str, Any]] = Field(default_factory=list)
    avg_time_to_root_cause_seconds: float | None = None
    generated_at: datetime = Field(default_factory=_utc_now)

    # Enhanced metrics
    total_cost_usd: float = 0.0
    cost_by_model: dict[str, float] = Field(default_factory=dict)
    cost_by_step: dict[str, float] = Field(default_factory=dict)
    tokens_by_model: dict[str, int] = Field(default_factory=dict)
    tokens_by_step: dict[str, int] = Field(default_factory=dict)
    avg_latency_ms: float = 0.0
    latency_by_model: dict[str, float] = Field(default_factory=dict)
    latency_by_step: dict[str, float] = Field(default_factory=dict)
    #: model -> {span_pass_rate, avg_latency_ms, avg_cost_usd}. `span_pass_rate`
    #: is the share of that model's spans that were *not* the diagnosed
    #: root-cause step — not a trace-level pass rate, which would be identical
    #: for every model because every trace runs every step.
    model_performance: dict[str, dict[str, float]] = Field(default_factory=dict)
    failure_correlation: list[dict[str, Any]] = Field(
        default_factory=list
    )  # step x category heatmap
    cost_trend: list[dict[str, Any]] = Field(default_factory=list)  # daily cost trend


class StepJudgeScore(BaseModel):
    """Judge model score for a single step."""

    step_name: str
    local_fidelity: float = Field(..., ge=0.0, le=1.0)
    global_fidelity: float = Field(..., ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    semantic_issues: list[str] = Field(
        default_factory=list, description="Issues deterministic checks missed"
    )


class JudgeVerdict(BaseModel):
    """The verdict the judge model is actually asked to produce.

    Deliberately excludes ``model_id``, ``provider`` and ``cost_usd``: those are
    facts about the call, not the verdict. Requiring them of the model meant the
    prompt never asked for them and *every* judge reply failed validation, so the
    judge could never run at all.
    """

    step_scores: list[StepJudgeScore]
    root_cause_step: str
    category: FailureCategory
    confidence: float = Field(..., ge=0.0, le=1.0)


class JudgeAnalysis(JudgeVerdict):
    """A stored judge verdict plus the provenance of the call that produced it."""

    cost_usd: float = Field(default=0.0, ge=0.0)
    model_id: str
    provider: str
    created_at: datetime = Field(default_factory=_utc_now)


class Tenant(BaseModel):
    """A billing/isolation boundary owning traces, flags and eval data."""

    tenant_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    created_at: datetime = Field(default_factory=_utc_now)
    active: bool = True


#: Scopes a key may hold. `*` is every scope, and is what every key minted
#: before scopes existed carries — the migration widens, it does not narrow.
API_KEY_SCOPES: frozenset[str] = frozenset({"read", "write", "*"})


class APIKey(BaseModel):
    """A tenant's API key. Only the bcrypt hash is stored, never the key.

    `prefix` exists so a key can be identified (in a log, in `list-keys`) without
    holding the plaintext or attempting a bcrypt comparison against every row.

    `scopes` bounds what the key can do: `read` allows the GET routes, `write`
    allows the mutating routes (and implies `read`, because a key that can start
    a run but cannot read its result is a trap), and `*` allows everything.
    """

    key_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    tenant_id: str
    key_hash: str = Field(..., description="bcrypt hash of the plaintext key")
    prefix: str = Field(..., description="Leading characters, for identification only")
    scopes: list[str] = Field(
        default_factory=lambda: ["*"],
        description="read / write / *; defaults to full access for legacy keys",
    )
    name: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = Field(default=None, description="Optional expiration timestamp")

    def allows(self, scope: str) -> bool:
        """Whether this key may perform `scope` (`read` or `write`).

        `*` allows everything (including scopes added later); `write` implies
        `read`; nothing implies `write`. A non-wildcard key allows nothing it
        does not list, which fails closed for an unrecognised scope.
        """
        if "*" in self.scopes:
            return True
        if scope == "read":
            return "read" in self.scopes or "write" in self.scopes
        return scope in self.scopes


__all__ = [
    "API_KEY_SCOPES",
    "DEFAULT_TENANT_ID",
    "APIKey",
    "AnalyticsSnapshot",
    "ClassificationOutput",
    "Diagnosis",
    "Document",
    "DocumentType",
    "Entity",
    "EvalCase",
    "EvalResult",
    "EvalRun",
    "ExtractionOutput",
    "FailureCategory",
    "Flag",
    "IntakeOutput",
    "JudgeAnalysis",
    "JudgeVerdict",
    "PipelineOutput",
    "Span",
    "StepJudgeScore",
    "StepScore",
    "SummaryOutput",
    "Tenant",
    "TenantScoped",
    "Trace",
    "TraceStatus",
]
