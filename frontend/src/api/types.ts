export type TraceStatus = "success" | "failure";

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
  step_name: "intake" | "extraction" | "classification" | "summarization" | string;
  step_index: number;
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
}

export interface Trace {
  trace_id: string;
  document_id: string;
  status: TraceStatus;
  start_time: string;
  end_time: string;
  total_latency_ms: number;
  spans: Span[];
  metadata: Record<string, unknown>;
  pipeline_version: string;
  prompt_version: string;
  schema_version: string;
  final_output: Record<string, unknown> | null;
  final_score: number | null;
  created_at: string;
  completed_at: string | null;
  total_tokens: number;
  total_cost_usd: number;
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
  override_reason: string | null;
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
