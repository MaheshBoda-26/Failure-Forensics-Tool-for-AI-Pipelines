import { describe, it, expect } from "vitest";
import { serializeAnalyticsCsv } from "../AnalyticsView";

const analytics = {
  total_traces: 3,
  failure_rate: 0.33,
  total_cost_usd: 1.25,
  avg_latency_ms: 420,
  failures_by_category: { extraction_hallucination: 1, misclassification: 0 },
  failures_by_step: { extraction: 1 },
  cost_by_model: { "test/pipeline-model": 1.25 },
  tokens_by_model: { "test/pipeline-model": 1000 },
  latency_by_step: { extraction: 120 },
  model_performance: {
    "test/pipeline-model": {
      span_pass_rate: 0.67,
      avg_latency_ms: 140,
      avg_cost_usd: 0.42,
    },
  },
  failure_correlation: [{ step: "extraction", category: "extraction_hallucination", count: 1 }],
  cost_trend: [{ timestamp: "2026-10-30", cost_usd: 1.25 }],
} as any;

describe("serializeAnalyticsCsv", () => {
  it("serializes all analytics sections into CSV", () => {
    const csv = serializeAnalyticsCsv(analytics);
    expect(csv).toContain("Overview");
    expect(csv).toContain("Metric,Value");
    expect(csv).toContain("Total traces,3");
    expect(csv).toContain("Failures by category");
    expect(csv).toContain("extraction_hallucination,1");
    expect(csv).toContain("Cost trend");
    expect(csv).toContain("2026-10-30,1.25");
  });

  it("includes model performance row with combined columns", () => {
    const csv = serializeAnalyticsCsv(analytics);
    expect(csv).toContain("Model performance");
    expect(csv).toContain("test/pipeline-model,0.67,140,0.42,1.25,1000");
  });

  it("ends with a trailing newline", () => {
    const csv = serializeAnalyticsCsv(analytics);
    expect(csv.endsWith("\n")).toBe(true);
  });
});
