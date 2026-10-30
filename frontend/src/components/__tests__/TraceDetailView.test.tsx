import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { TraceDetailView } from "../TraceDetailView";
import type { Trace, Span } from "../../api/types";

afterEach(() => {
  cleanup();
});

const span: Span = {
  span_id: "s1",
  trace_id: "trace_empty_001",
  step_name: "extraction",
  parent_span_id: null,
  input_json: "{}",
  output_json: "{}",
  status: "success",
  latency_ms: 100,
  prompt_tokens: 10,
  completion_tokens: 20,
  reasoning_tokens: 0,
  model_id: "test/model",
  provider: "test",
  prompt: "extract",
  raw_response: "{}",
  confidence: 3,
  error: null,
  cached: false,
  started_at: "2026-10-30T12:00:00Z",
  ended_at: "2026-10-30T12:00:01Z",
  pipeline_version: "0.1.0",
  prompt_version: "v1",
  schema_version: 1,
  tenant_id: "default",
  prompt_template: "extraction-v1",
};

const baseTrace: Trace = {
  trace_id: "trace_empty_001",
  document_id: "doc_001",
  status: "success",
  spans: [],
  final_output: null,
  final_score: null,
  pipeline_version: "0.1.0",
  prompt_version: "v1",
  schema_version: 1,
  created_at: "2026-10-30T12:00:00Z",
  completed_at: "2026-10-30T12:00:05Z",
  total_latency_ms: 5000,
  total_tokens: 0,
  total_cost_usd: 0,
  tenant_id: "default",
  root_cause_step: null,
  expected_type: null,
};

describe("TraceDetailView empty state", () => {
  it("shows an explicit empty-trace panel when a trace has no spans", () => {
    render(
      <TraceDetailView
        trace={baseTrace}
        diagnosis={null}
        onRefreshTrace={async () => {}}
        onBack={() => {}}
      />,
    );

    screen.getByText(/This trace has no spans/);
    screen.getByText(/trace_empty_001/);
    expect(screen.getByRole("button", { name: /Back to traces/ })).toBeDefined();
  });

  it("renders the stage payload panel when spans are present", () => {
    const traceWithSpans: Trace = { ...baseTrace, spans: [span] };
    expect(traceWithSpans.spans.length).toBe(1);

    render(
      <TraceDetailView
        trace={traceWithSpans}
        diagnosis={null}
        onRefreshTrace={async () => {}}
        onBack={() => {}}
      />,
    );

    // The non-empty path renders the TraceSpine and stage-payload panels.
    screen.getByText(/Stage payload/);
    expect(screen.queryByText(/This trace has no spans/)).toBeNull();
  });
});
