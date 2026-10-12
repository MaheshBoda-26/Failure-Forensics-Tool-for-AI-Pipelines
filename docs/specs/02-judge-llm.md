# SPEC: Judge LLM Integration (v1)

## Summary
Add a judge model that runs **on-demand** (when deterministic analyzer finds a failure OR `JUDGE_FORCE=true`). Judge catches semantic failures deterministic checks miss. Reuses existing spend caps.

## Config (`.env`)
```bash
JUDGE_ENABLED=true|false              # default: false (opt-in)
JUDGE_FORCE=false|true                # default: false (run even if deterministic passes)
JUDGE_MODEL_ID=anthropic/claude-3.5-sonnet
JUDGE_MODEL_PROVIDER=anthropic
JUDGE_MODEL_FAMILY=anthropic
JUDGE_MODEL_PRICE_IN=3.00
JUDGE_MODEL_PRICE_OUT=15.00
```

## Data Models

### `src/forensics/models.py` — New
```python
class StepJudgeScore(BaseModel):
    step_name: str
    local_fidelity: float = Field(ge=0.0, le=1.0)
    global_fidelity: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = []
    semantic_issues: list[str] = []  # what deterministic missed


class JudgeAnalysis(BaseModel):
    step_scores: list[StepJudgeScore]
    root_cause_step: str
    category: FailureCategory
    confidence: float = Field(ge=0.0, le=1.0)
    cost_usd: float = 0.0
    model_id: str
    provider: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

### Extend `Diagnosis`
```python
class Diagnosis(VersionedModel):
    # ... existing fields ...
    judge_analysis: JudgeAnalysis | None = None
    analyzer_fallback: bool = False  # true if judge overrode deterministic
```

## Changes

### `src/forensics/llm.py`
Strengthen `judge_analyze`:
```python
async def judge_analyze(
    self,
    trace_data: dict[str, Any],
    document_text: str,
    expected_type: DocumentType | None = None,
) -> JudgeAnalysis:
    # 1. Build structured prompt with fenced document + trace
    # 2. Call judge model with JSON response format (Pydantic model -> JSON schema)
    # 3. Validate response against JudgeAnalysis
    # 4. Retry once on parse failure
    # 5. Track cost via existing spend ledger
```

### `src/forensics/analyzer_simple.py`
Add `JudgeAnalyzer` class:
```python
class JudgeAnalyzer:
    def __init__(self, llm_client: LLMClient):
        self.client = llm_client

    async def analyze(
        self,
        trace: Trace,
        document_text: str,
        expected_type: DocumentType | None = None,
    ) -> JudgeAnalysis:
        trace_dict = trace.model_dump(mode="json")
        return await self.client.judge_analyze(trace_dict, document_text, expected_type)
```

### `src/forensics/pipeline.py` / `src/forensics/tracing.py`
In `finalize_trace`:
1. Run deterministic analyzer first (`analyze_trace`)
2. If `deterministic.root_cause_step != "none"` OR `settings.judge_force`:
   - Create `JudgeAnalyzer` with LLM client
   - Call `judge_analyze`
   - If judge disagrees with deterministic: set `diagnosis.analyzer_fallback = True`, use judge's verdict
   - Store `judge_analysis` in diagnosis

### `src/forensics/api.py`
- `POST /traces/{id}/diagnose` returns `judge_analysis` if present
- `GET /analytics` includes `judge_overrides_count`, `judge_cost_usd`

## Cost Control
- Reuse `spend_cap_per_run_usd` and `spend_cap_global_usd`
- Judge call counts toward same caps
- If cap exceeded: skip judge, log warning, use deterministic only

## Acceptance Criteria
1. `JUDGE_ENABLED=false`: zero judge calls, zero cost
2. `JUDGE_ENABLED=true`, deterministic finds failure: judge runs, may override
3. `JUDGE_FORCE=true`: judge runs on every trace
4. Judge catches semantic failure (e.g., "summary has wrong tone", "extraction misses implied entity") that deterministic misses
4. Judge verdict stored in `Diagnosis.judge_analysis`
5. Spend ledger tracks judge cost separately (tag `judge`)

## Effort
- **Files**: 4 modified, 1 new model
- **Time**: ~4 hours
- **Risk**: Medium (new model, prompt engineering, cost tracking)
