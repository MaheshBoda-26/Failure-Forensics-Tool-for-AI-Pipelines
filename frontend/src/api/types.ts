export type TraceStatus = "success" | "failure";

export type FailureCategory =
  | "misclassification"
  | "extraction_omission"
  | "schema_drift"
  | "summary_hallucination"
  | "prompt_failure"
  | "token_limit"
  | "tool_call_malformed"
  | "unparseable_output"
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
  input_data: Record<string, unknown> | string;
  output_data: Record<string, unknown> | string;
  status: "success" | "failure";
  latency_ms: number;
  token_usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cost_usd: number;
  } | null;
  error: string | null;
  checks: CheckResult[];
  prompt: string | null;
  raw_response: string | null;
}

export interface Trace {
  trace_id: string;
  document_id: string;
  status: TraceStatus;
  start_time: string;
  end_time: string;
  duration_ms: number;
  spans: Span[];
  metadata: Record<string, unknown>;
  pipeline_version: string;
  prompt_version: string;
  schema_version: string;
}

export interface EvidenceItem {
  step: string;
  check_name: string;
  score: number;
  threshold: number;
  detail: string;
  severity: "high" | "medium" | "low";
}

export interface Diagnosis {
  diagnosis_id: string;
  trace_id: string;
  root_cause_step: string;
  category: FailureCategory;
  confidence: number;
  evidence_chain: EvidenceItem[];
  step_scores: Record<string, number>;
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
  baseline_accuracy: number;
  current_accuracy: number;
  total_cases: number;
  regressions: string[];
  improvements: string[];
  created_at: string;
}

export interface Flag {
  flag_id: string;
  trace_id: string;
  user_id: string;
  note: string | null;
  created_at: string;
}
