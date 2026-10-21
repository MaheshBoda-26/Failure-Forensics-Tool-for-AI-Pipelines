export type TraceStatus = "success" | "failure" | "degraded";

export type FailureCategory =
  | "extraction_hallucination"
  | "misclassification"
  | "propagation_error"
  | "prompt_failure"
  | "context_loss"
  | "unknown";

export interface CheckResult {
  check_name: string;
  passed: boolean;
  score: number;
  threshold: number;
  detail: string;
}

export interface Span {
  span_id: string;
  trace_id: string;
  step_name: "intake" | "extraction" | "classification" | "summarization" | string;
  parent_span_id: string | null;
  input_json: string;
  output_json: string;
  status: "success" | "failure" | "degraded";
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  model_id: string;
  provider: string;
  prompt: string;
  raw_response: string;
  confidence: number;
  error: string | null;
  cached: boolean;
  started_at: string;
  ended_at: string | null;
  pipeline_version: string;
  prompt_version: string;
  schema_version: number;
  tenant_id: string;
}

export interface Trace {
  trace_id: string;
  document_id: string;
  status: TraceStatus;
  spans: Span[];
  final_output: Record<string, unknown> | null;
  final_score: number | null;
  pipeline_version: string;
  prompt_version: string;
  /** Backend `Trace.schema_version` is an `int`, not a string. */
  schema_version: number;
  created_at: string;
  completed_at: string | null;
  total_latency_ms: number;
  total_tokens: number;
  total_cost_usd: number;
  tenant_id: string;
  /**
   * Root cause of the latest diagnosis, when one exists. `null` means "not
   * diagnosed"; `"none"` means "diagnosed clean". The run `status` alone cannot
   * express a semantic failure: the pipeline completes successfully while the
   * output is wrong.
   */
  root_cause_step?: string | null;
}

export interface StepScore {
  step_name: string;
  local_fidelity: number;
  global_fidelity: number;
  local_pass: boolean;
  global_pass: boolean;
  evidence: string[];
  deterministic_checks: Record<string, boolean>;
}

export interface JudgeStepScore {
  step_name: string;
  local_fidelity: number;
  global_fidelity: number;
  evidence: string[];
  semantic_issues: string[];
}

/** The judge LLM's stored verdict plus the provenance of the call. */
export interface JudgeAnalysis {
  step_scores: JudgeStepScore[];
  root_cause_step: string;
  category: FailureCategory;
  confidence: number;
  cost_usd: number;
  model_id: string;
  provider: string;
  created_at: string;
}

export interface Diagnosis {
  diagnosis_id: string;
  trace_id: string;
  root_cause_step: string;
  category: FailureCategory;
  confidence: number;
  evidence_chain: string[];
  step_scores: StepScore[];
  analyzer_version: string;
  model_id: string;
  provider: string;
  created_at: string;
  confirmed: boolean;
  overridden: boolean;
  override_category: FailureCategory | null;
  /** Human-supplied failing step, when the analyzer missed the failure. */
  override_step: string | null;
  override_reason: string | null;
  /** Present only when the judge LLM ran. */
  judge_analysis: JudgeAnalysis | null;
  /** True when the judge overrode the deterministic verdict. */
  analyzer_fallback: boolean;
}

export interface AnalyticsSnapshot {
  total_traces: number;
  failure_rate: number;
  failures_by_category: Record<string, number>;
  failures_by_step: Record<string, number>;
  failure_rate_over_time: Array<{ timestamp: string; failure_rate: number }>;
  avg_time_to_root_cause_seconds: number | null;
  generated_at?: string;
}

export interface EvalRun {
  run_id: string;
  pipeline_version: string;
  prompt_versions: Record<string, string>;
  started_at: string;
  completed_at: string | null;
  total_cases: number;
  passed_count: number;
  failed_count: number;
}

/**
 * Body of `POST /eval/run`. It is a summary of the run that was just executed,
 * not an `EvalRun`: the endpoint returns counts directly, and `run_id` is null
 * when the suite is empty and no run was created.
 */
export interface EvalRunSummary {
  status: "ok" | "empty";
  run_id: string | null;
  total_cases: number;
  passed_count: number;
  failed_count: number;
  current_accuracy: number;
}

export interface Flag {
  flag_id: string;
  trace_id: string;
  user_id: string;
  note: string | null;
  created_at: string;
}

export interface EvalResult {
  result_id: string;
  run_id: string;
  case_id: string;
  step_name: string;
  score: number;
  fixed: boolean;
  details: Record<string, unknown>;
  evaluated_at: string;
}
