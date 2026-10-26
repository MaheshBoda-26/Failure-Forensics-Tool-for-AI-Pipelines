import React, { useState, useMemo, useEffect } from "react";
import { AnalyticsSnapshot, EvalRun, EvalResult } from "../api/types";
import { api } from "../api/client";
import { Play, TrendingUp, AlertTriangle, ShieldCheck, Clock, DollarSign, BarChart2, Zap, Cpu, GitBranch, PieChart } from "lucide-react";

interface AnalyticsViewProps {
  analytics: AnalyticsSnapshot | null;
  evalRuns: EvalRun[];
  onRefreshAnalytics: () => Promise<void>;
}

export const AnalyticsView: React.FC<AnalyticsViewProps> = ({
  analytics,
  evalRuns,
  onRefreshAnalytics,
}) => {
  const [isRunningEval, setIsRunningEval] = useState<boolean>(false);
  const [evalNotice, setEvalNotice] = useState<string | null>(null);
  const [evalResults, setEvalResults] = useState<EvalResult[]>([]);

  const handleRunEval = async () => {
    try {
      setIsRunningEval(true);
      const res = await api.triggerEvalRun();
      if (!res.run_id) {
        setEvalNotice("No eval cases yet — confirm a diagnosis to create one.");
        setEvalResults([]);
        await onRefreshAnalytics();
        return;
      }
      setEvalNotice(`✓ Eval run ${res.run_id} completed: ${res.passed_count}/${res.total_cases} cases passed`);
      await onRefreshAnalytics();
      // Only fetch results for a run that exists: the empty-suite response has
      // no run id, and following an invented one surfaced a 404 as an error.
      const results = await api.getEvalResults(res.run_id);
      setEvalResults(results);
    } catch (err: unknown) {
      setEvalNotice(`Error running eval: ${(err as Error).message}`);
    } finally {
      setIsRunningEval(false);
      setTimeout(() => setEvalNotice(null), 5000);
    }
  };

  // Fetch eval results for latest run on mount and when evalRuns change
  useEffect(() => {
    const latestRun = evalRuns[0];
    if (latestRun) {
      api.getEvalResults(latestRun.run_id).then(setEvalResults).catch(() => setEvalResults([]));
    } else {
      setEvalResults([]);
    }
  }, [evalRuns]);

  // Derived strictly from the response. The previous `analytics ? ... : 84`
  // fallbacks rendered a healthy-looking dashboard (84% pass rate, 15 traces,
  // 180ms) before any data had loaded, which is indistinguishable from a real
  // measurement. `null` renders as an em dash instead.
  const passRate = analytics ? Math.round((1 - analytics.failure_rate) * 100) : null;
  const totalTraces = analytics?.total_traces ?? 0;
  const categoryCounts = analytics?.failures_by_category ?? {};
  const totalCategoryFailures = Object.values(categoryCounts).reduce((a, b) => a + b, 0);
  const topCategoryEntry = Object.entries(categoryCounts).sort((a, b) => b[1] - a[1])[0];
  const topCategory = topCategoryEntry?.[0] ?? null;
  const topCategoryShare =
    topCategoryEntry && totalCategoryFailures > 0
      ? Math.round((topCategoryEntry[1] / totalCategoryFailures) * 100)
      : null;

  // Enhanced metrics
  const totalCost = analytics?.total_cost_usd ?? 0;
  const costByModel = analytics?.cost_by_model ?? {};
  const costByStep = analytics?.cost_by_step ?? {};
  const tokensByModel = analytics?.tokens_by_model ?? {};
  const latencyByModel = analytics?.latency_by_model ?? {};
  const latencyByStep = analytics?.latency_by_step ?? {};
  const modelPerformance = analytics?.model_performance ?? {};
  const failureCorrelation = analytics?.failure_correlation ?? [];
  const costTrend = analytics?.cost_trend ?? [];
  const avgLatencyMs = analytics?.avg_latency_ms ?? 0;

  // Top model by cost
  const topModelEntry = Object.entries(costByModel).sort((a, b) => b[1] - a[1])[0];
  const topModel = topModelEntry?.[0] ?? null;
  const topModelCost = topModelEntry?.[1] ?? 0;

  // Compute stage vulnerability percentages from real data
  const stageVulnerability = useMemo(() => {
    const steps = ["intake", "extraction", "classification", "summarization"];
    const failuresByStep = analytics?.failures_by_step || {};
    const totalFailures = Object.values(failuresByStep).reduce((a, b) => a + b, 0);
    return steps.map((step, idx) => {
      const count = failuresByStep[step] || 0;
      const pct = totalFailures > 0 ? (count / totalFailures) * 100 : 0;
      const colors = ["var(--color-copper)", "var(--color-ochre)", "var(--color-cinnabar)", "var(--color-ink-3)"];
      return {
        stage: `${String(idx + 1).padStart(2, "0")} ${step}`,
        pct,
        color: colors[idx],
        label: `${pct.toFixed(1)}%`,
      };
    });
  }, [analytics]);

  // Failure correlation heatmap data
  const correlationMatrix = useMemo(() => {
    const steps = ["intake", "extraction", "classification", "summarization"];
    const categories = [
      "extraction_hallucination",
      "misclassification",
      "prompt_failure",
      "propagation_error",
      "context_loss",
      "unknown"
    ];
    return steps.map(step =>
      categories.map(cat => {
        const entry = failureCorrelation.find(f => f.step === step && f.category === cat);
        return { step, category: cat, count: entry?.count ?? 0 };
      })
    );
  }, [failureCorrelation]);

  // Get latest eval run for regression table
  const latestRun = evalRuns[0];
  const hasEvalData = latestRun && latestRun.total_cases > 0;

  return (
    <div
      style={{
        flex: 1,
        padding: "32px",
        overflowY: "auto",
        display: "flex",
        flexDirection: "column",
        gap: "28px",
        backgroundColor: "var(--color-ground)",
      }}
    >
      {/* Top Banner */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h2
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "28px",
              fontWeight: 800,
              color: "var(--color-ink)",
              margin: 0,
            }}
          >
            Reliability and failure analytics
          </h2>
          <p style={{ margin: "4px 0 0 0", color: "var(--color-ink-2)", fontSize: "14px" }}>
            Aggregate fault isolation, stage vulnerability distribution, and regression trends.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          {evalNotice && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-copper)", fontWeight: 600 }}>
              {evalNotice}
            </span>
          )}
          <button
            onClick={handleRunEval}
            disabled={isRunningEval}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              fontFamily: "var(--font-mono)",
              fontSize: "12px",
              fontWeight: 600,
              color: "var(--color-ground)",
              backgroundColor: "var(--color-copper)",
              border: "1px solid var(--color-copper)",
              borderRadius: "var(--radius-1)",
              padding: "8px 16px",
            }}
          >
            <Play size={12} fill="currentColor" />
            <span>{isRunningEval ? "Running eval suite..." : "Run eval suite"}</span>
          </button>
        </div>
      </div>

      {/* KPI Metric Strip */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "18px" }}>
        <div
          style={{
            padding: "20px",
            backgroundColor: "var(--color-chalk)",
            border: "1px solid var(--color-rule)",
            borderRadius: "var(--radius-1)",
          }}
        >
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
              color: "var(--color-ink-3)",
              letterSpacing: "var(--tracking-label)",
              textTransform: "uppercase",
            }}
          >
            Pipeline pass rate
          </div>
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "36px",
              fontWeight: 800,
              color: "var(--color-ink)",
              marginTop: "6px",
            }}
          >
            {passRate === null ? "—" : `${passRate}%`}
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-copper)", marginTop: "6px" }}>
            {totalTraces} traces tracked
          </div>
        </div>

        <div
          style={{
            padding: "20px",
            backgroundColor: "var(--color-chalk)",
            border: "1px solid var(--color-rule)",
            borderRadius: "var(--radius-1)",
          }}
        >
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
              color: "var(--color-ink-3)",
              letterSpacing: "var(--tracking-label)",
              textTransform: "uppercase",
            }}
          >
            Top root cause
          </div>
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "24px",
              fontWeight: 800,
              color: "var(--color-cinnabar)",
              marginTop: "10px",
              textTransform: "capitalize",
            }}
          >
            {topCategory ? topCategory.replace(/_/g, " ") : "—"}
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-2)", marginTop: "6px" }}>
            {topCategoryShare === null
              ? "No classified failures yet"
              : `${topCategoryShare}% of classified failures`}
          </div>
        </div>

        <div
          style={{
            padding: "20px",
            backgroundColor: "var(--color-chalk)",
            border: "1px solid var(--color-rule)",
            borderRadius: "var(--radius-1)",
          }}
        >
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
              color: "var(--color-ink-3)",
              letterSpacing: "var(--tracking-label)",
              textTransform: "uppercase",
            }}
          >
            Mean time to root cause
          </div>
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "36px",
              fontWeight: 800,
              color: "var(--color-ink)",
              marginTop: "6px",
            }}
          >
            {analytics?.avg_time_to_root_cause_seconds != null
              ? `${Math.round(analytics.avg_time_to_root_cause_seconds * 1000)}ms`
              : "—"}
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-copper)", marginTop: "6px" }}>
            Deterministic backward trace analyzer
          </div>
        </div>

        <div
          style={{
            padding: "20px",
            backgroundColor: "var(--color-chalk)",
            border: "1px solid var(--color-rule)",
            borderRadius: "var(--radius-1)",
          }}
        >
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
              color: "var(--color-ink-3)",
              letterSpacing: "var(--tracking-label)",
              textTransform: "uppercase",
            }}
          >
            Golden regressions
          </div>
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "36px",
              fontWeight: 800,
              color: "var(--color-cinnabar)",
              marginTop: "6px",
            }}
          >
            {evalRuns[0]?.failed_count ?? evalResults.filter((r) => !r.fixed).length} Traces
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-cinnabar)", marginTop: "6px" }}>
            ⚠ Blocked by evaluation gate
          </div>
        </div>
      </div>

      {/* Stage Vulnerability Map */}
      <div
        style={{
          border: "1px solid var(--color-rule)",
          borderRadius: "var(--radius-1)",
          backgroundColor: "var(--color-ground)",
          padding: "24px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: "18px", fontWeight: 700, color: "var(--color-ink)" }}>
              Pipeline stage vulnerability map
            </div>
            <div style={{ fontSize: "13px", color: "var(--color-ink-2)", marginTop: "2px" }}>
              Originating fault step versus downstream manifestation point
            </div>
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
            N={totalTraces} EXECUTED RUNS
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {stageVulnerability.map((item) => (
            <div key={item.stage} style={{ display: "flex", alignItems: "center", gap: "16px" }}>
              <span
                style={{
                  width: "140px",
                  fontFamily: "var(--font-mono)",
                  fontSize: "12px",
                  fontWeight: 600,
                  color: item.pct > 50 ? "var(--color-cinnabar)" : "var(--color-ink)",
                }}
              >
                {item.stage}
              </span>
              <div
                style={{
                  flex: 1,
                  height: "22px",
                  backgroundColor: "var(--color-chalk)",
                  borderRadius: "var(--radius-0)",
                  overflow: "hidden",
                  border: "1px solid var(--color-rule)",
                  position: "relative",
                }}
              >
                <div
                  style={{
                    width: `${Math.max(1, item.pct)}%`,
                    height: "100%",
                    backgroundColor: item.color,
                    transition: "width 0.4s ease",
                  }}
                />
              </div>
              <span
                style={{
                  width: "60px",
                  textAlign: "right",
                  fontFamily: "var(--font-mono)",
                  fontSize: "12px",
                  fontWeight: item.pct > 50 ? 700 : 500,
                  color: item.pct > 50 ? "var(--color-cinnabar)" : "var(--color-ink-2)",
                }}
              >
                {item.label}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Golden Eval Benchmark Results */}
      <div
        style={{
          border: "1px solid var(--color-rule)",
          borderRadius: "var(--radius-1)",
          backgroundColor: "var(--color-ground)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "16px 20px",
            borderBottom: "1px solid var(--color-rule)",
            backgroundColor: "var(--color-chalk)",
          }}
        >
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 700, color: "var(--color-ink)" }}>
            Golden eval benchmark regression table
          </div>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-cinnabar)", fontWeight: 700 }}>
            {evalResults.filter(r => !r.fixed).length} Regressions flagged
          </span>
        </div>

        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-mono)", fontSize: "12px", textAlign: "left" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--color-rule)", color: "var(--color-ink-3)", fontSize: "11px" }}>
              <th style={{ padding: "12px 20px" }}>Benchmark suite</th>
              <th style={{ padding: "12px 20px" }}>Failure mode</th>
              <th style={{ padding: "12px 20px" }}>Isolated step</th>
              <th style={{ padding: "12px 20px" }}>BASELINE</th>
              <th style={{ padding: "12px 20px" }}>CURRENT</th>
              <th style={{ padding: "12px 20px" }}>DELTA</th>
              <th style={{ padding: "12px 20px" }}>STATUS</th>
            </tr>
          </thead>
          <tbody>
            {evalResults.length > 0 ? (
              evalResults.map((result) => {
                const category = result.details?.category || "unknown";
                const isFixed = result.fixed;
                return (
                  <tr key={result.result_id} style={{ borderBottom: "1px solid var(--color-rule)", backgroundColor: isFixed ? "transparent" : "var(--color-cinnabar-tint)" }}>
                    <td style={{ padding: "12px 20px", fontWeight: 600, color: "var(--color-ink)" }}>{result.case_id.slice(0, 14)}</td>
                    <td style={{ padding: "12px 20px", color: isFixed ? "var(--color-ink-2)" : "var(--color-cinnabar)" }}><span>{String(category)}</span></td>
                    <td style={{ padding: "12px 20px" }}>{result.step_name}</td>
                    {/* No baseline is persisted per case, so these cells show
                        the measured score rather than an invented delta. */}
                    <td style={{ padding: "12px 20px" }}>—</td>
                    <td style={{ padding: "12px 20px", fontWeight: 700, color: isFixed ? "var(--color-copper)" : "var(--color-cinnabar)" }}>
                      {(result.score * 100).toFixed(0)}%
                    </td>
                    <td style={{ padding: "12px 20px" }}>—</td>
                    <td style={{ padding: "12px 20px", color: isFixed ? "var(--color-copper)" : "var(--color-cinnabar)", fontWeight: 700 }}>
                      {isFixed ? "FIXED" : "REGRESSION"}
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr style={{ borderBottom: "1px solid var(--color-rule)" }}>
                <td colSpan={7} style={{ padding: "20px", textAlign: "center", color: "var(--color-ink-2)" }}>
                  No eval results available. Run eval suite to populate.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {/* Cost & Token Analytics */}
      <div
        style={{
          border: "1px solid var(--color-rule)",
          borderRadius: "var(--radius-1)",
          backgroundColor: "var(--color-ground)",
          padding: "24px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: "18px", fontWeight: 700, color: "var(--color-ink)" }}>
              Cost and token analytics
            </div>
            <div style={{ fontSize: "13px", color: "var(--color-ink-2)", marginTop: "2px" }}>
              Spend breakdown by model and pipeline stage
            </div>
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
            TOTAL SPEND: ${totalCost.toFixed(4)}
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "16px" }}>
          {/* Cost by Model */}
          <div style={{ border: "1px solid var(--color-rule)", borderRadius: "var(--radius-1)", backgroundColor: "var(--color-chalk)", padding: "20px" }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)", textTransform: "uppercase", marginBottom: "12px" }}>
              Cost by model
            </div>
            {Object.entries(costByModel).length > 0 ? (
              Object.entries(costByModel)
                .sort((a, b) => b[1] - a[1])
                .map(([model, cost]) => (
                  <div key={model} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--color-rule)" }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--color-ink)" }}>{model}</span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 700, color: "var(--color-ochre)" }}>${cost.toFixed(4)}</span>
                  </div>
                ))
            ) : (
              <div style={{ color: "var(--color-ink-2)", fontSize: "12px", textAlign: "center", padding: "20px" }}>
                No cost data available
              </div>
            )}
          </div>

          {/* Tokens by Model */}
          <div style={{ border: "1px solid var(--color-rule)", borderRadius: "var(--radius-1)", backgroundColor: "var(--color-chalk)", padding: "20px" }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)", textTransform: "uppercase", marginBottom: "12px" }}>
              Tokens by model
            </div>
            {Object.entries(tokensByModel).length > 0 ? (
              Object.entries(tokensByModel)
                .sort((a, b) => b[1] - a[1])
                .map(([model, tokens]) => (
                  <div key={model} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--color-rule)" }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--color-ink)" }}>{model}</span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 700, color: "var(--color-copper)" }}>{tokens.toLocaleString()}</span>
                  </div>
                ))
            ) : (
              <div style={{ color: "var(--color-ink-2)", fontSize: "12px", textAlign: "center", padding: "20px" }}>
                No token data available
              </div>
            )}
          </div>

          {/* Cost by Step */}
          <div style={{ border: "1px solid var(--color-rule)", borderRadius: "var(--radius-1)", backgroundColor: "var(--color-chalk)", padding: "20px" }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)", textTransform: "uppercase", marginBottom: "12px" }}>
              Cost by pipeline stage
            </div>
            {Object.entries(costByStep).length > 0 ? (
              Object.entries(costByStep)
                .sort((a, b) => b[1] - a[1])
                .map(([step, cost]) => (
                  <div key={step} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--color-rule)" }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--color-ink)", textTransform: "capitalize" }}>{step}</span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 700, color: "var(--color-ochre)" }}>${cost.toFixed(4)}</span>
                  </div>
                ))
            ) : (
              <div style={{ color: "var(--color-ink-2)", fontSize: "12px", textAlign: "center", padding: "20px" }}>
                No step cost data available
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Model Performance Comparison */}
      <div
        style={{
          border: "1px solid var(--color-rule)",
          borderRadius: "var(--radius-1)",
          backgroundColor: "var(--color-ground)",
          padding: "24px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: "18px", fontWeight: 700, color: "var(--color-ink)" }}>
              Model performance comparison
            </div>
            <div style={{ fontSize: "13px", color: "var(--color-ink-2)", marginTop: "2px" }}>
              Share of each model's spans not blamed, plus latency and cost
            </div>
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
            AVG LATENCY: {Math.round(avgLatencyMs)}ms
          </div>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-mono)", fontSize: "12px", textAlign: "left" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--color-rule)", color: "var(--color-ink-3)", fontSize: "11px" }}>
                <th style={{ padding: "12px 20px" }}>MODEL</th>
                <th style={{ padding: "12px 20px" }}>SPAN PASS RATE</th>
                <th style={{ padding: "12px 20px" }}>AVG LATENCY</th>
                <th style={{ padding: "12px 20px" }}>AVG COST</th>
                <th style={{ padding: "12px 20px" }}>TOTAL COST</th>
                <th style={{ padding: "12px 20px" }}>TOTAL TOKENS</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(modelPerformance).length > 0 ? (
                Object.entries(modelPerformance)
                  .sort((a, b) => b[1].span_pass_rate - a[1].span_pass_rate)
                  .map(([model, perf]) => (
                    <tr key={model} style={{ borderBottom: "1px solid var(--color-rule)" }}>
                      <td style={{ padding: "12px 20px", fontWeight: 600, color: "var(--color-ink)" }}>{model}</td>
                      <td style={{ padding: "12px 20px", fontWeight: 700, color: perf.span_pass_rate >= 0.9 ? "var(--color-copper)" : perf.span_pass_rate >= 0.7 ? "var(--color-ochre)" : "var(--color-cinnabar)" }}>
                        {(perf.span_pass_rate * 100).toFixed(1)}%
                      </td>
                      <td style={{ padding: "12px 20px", color: perf.avg_latency_ms > 5000 ? "var(--color-cinnabar)" : "var(--color-ink)" }}>
                        {Math.round(perf.avg_latency_ms)}ms
                      </td>
                      <td style={{ padding: "12px 20px" }}>${perf.avg_cost_usd.toFixed(6)}</td>
                      <td style={{ padding: "12px 20px" }}>${costByModel[model]?.toFixed(4) ?? "0.0000"}</td>
                      <td style={{ padding: "12px 20px", color: "var(--color-ink-2)" }}>{tokensByModel[model]?.toLocaleString() ?? "0"}</td>
                    </tr>
                  ))
              ) : (
                <tr>
                  <td colSpan={6} style={{ padding: "20px", textAlign: "center", color: "var(--color-ink-2)" }}>
                    No model performance data available
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Failure Correlation Heatmap */}
      <div
        style={{
          border: "1px solid var(--color-rule)",
          borderRadius: "var(--radius-1)",
          backgroundColor: "var(--color-ground)",
          padding: "24px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: "18px", fontWeight: 700, color: "var(--color-ink)" }}>
              Failure correlation heatmap
            </div>
            <div style={{ fontSize: "13px", color: "var(--color-ink-2)", marginTop: "2px" }}>
              Pipeline step and failure category co-occurrence
            </div>
          </div>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-mono)", fontSize: "11px", textAlign: "center" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--color-rule)", color: "var(--color-ink-3)" }}>
                <th style={{ padding: "10px 14px", textAlign: "left" }}>STEP \ CATEGORY</th>
                {["extraction_hallucination", "misclassification", "prompt_failure", "propagation_error", "context_loss", "unknown"].map(cat => (
                  <th key={cat} style={{ padding: "10px 14px", fontSize: "10px", writingMode: "vertical-rl", textOrientation: "mixed", transform: "rotate(180deg)", whiteSpace: "nowrap" }}>
                    {cat.replace(/_/g, " ")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {["intake", "extraction", "classification", "summarization"].map(step => (
                <tr key={step} style={{ borderBottom: "1px solid var(--color-rule)" }}>
                  <td style={{ padding: "10px 14px", textAlign: "left", fontFamily: "var(--font-mono)", fontSize: "11px", fontWeight: 600, color: "var(--color-ink)" }}>
                    {step}
                  </td>
                  {["extraction_hallucination", "misclassification", "prompt_failure", "propagation_error", "context_loss", "unknown"].map(cat => {
                    const entry = failureCorrelation.find(f => f.step === step && f.category === cat);
                    const count = entry?.count ?? 0;
                    const maxCount = Math.max(...failureCorrelation.map(f => f.count), 1);
                    const intensity = count / maxCount;
                    const bgColor = count > 0
                      ? `rgba(200, 40, 40, ${0.2 + intensity * 0.6})`
                      : "transparent";
                    return (
                      <td key={cat} style={{ padding: "8px 14px", backgroundColor: bgColor, border: "1px solid var(--color-rule)" }}>
                        {count > 0 ? <span style={{ fontWeight: 700, color: "var(--color-cinnabar)" }}>{count}</span> : "—"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Cost Trend Chart */}
      {(costTrend || []).length > 0 && (
        <div
          style={{
            border: "1px solid var(--color-rule)",
            borderRadius: "var(--radius-1)",
            backgroundColor: "var(--color-ground)",
            padding: "24px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
            <div>
              <div style={{ fontFamily: "var(--font-display)", fontSize: "18px", fontWeight: 700, color: "var(--color-ink)" }}>
                Cost trend over time
              </div>
              <div style={{ fontSize: "13px", color: "var(--color-ink-2)", marginTop: "2px" }}>
                Daily spend trend
              </div>
            </div>
          </div>

          <div style={{ height: "200px", position: "relative" }}>
            <svg width="100%" height="100%" viewBox="0 0 800 200" preserveAspectRatio="none">
              <defs>
                <linearGradient id="costGradient" x1="0%" y1="0%" x2="0%" y2="100%">
                  <stop offset="0%" stopColor="var(--color-copper)" stopOpacity="0.3" />
                  <stop offset="100%" stopColor="var(--color-copper)" stopOpacity="0" />
                </linearGradient>
              </defs>
              {(() => {
                const maxCost = Math.max(...costTrend.map(c => c.cost_usd), 1);
                const points = costTrend.map((c, i) => {
                  const x = (i / Math.max(costTrend.length - 1, 1)) * 780 + 10;
                  const y = 180 - (c.cost_usd / maxCost) * 160;
                  return `${x},${y}`;
                }).join(" ");
                const areaPoints = costTrend.map((c, i) => {
                  const x = (i / Math.max(costTrend.length - 1, 1)) * 780 + 10;
                  const y = 180 - (c.cost_usd / maxCost) * 160;
                  return `${x},${y}`;
                }).join(" ") + " 790,190 10,190";
                return (
                  <>
                    <polygon points={areaPoints} fill="url(#costGradient)" />
                    <polyline points={points} fill="none" stroke="var(--color-copper)" strokeWidth="2" />
                    {costTrend.map((c, i) => (
                      <circle key={i} cx={(i / Math.max(costTrend.length - 1, 1)) * 780 + 10} cy={180 - (c.cost_usd / maxCost) * 160} r="4" fill="var(--color-copper)" />
                    ))}
                  </>
                );
              })()}
            </svg>
          </div>
        </div>
      )}

      {/* Latency Analysis */}
      <div
        style={{
          border: "1px solid var(--color-rule)",
          borderRadius: "var(--radius-1)",
          backgroundColor: "var(--color-ground)",
          padding: "24px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: "18px", fontWeight: 700, color: "var(--color-ink)" }}>
              Latency analysis
            </div>
            <div style={{ fontSize: "13px", color: "var(--color-ink-2)", marginTop: "2px" }}>
              Per-model and per-stage latency breakdown
            </div>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "24px" }}>
          {/* Latency by Model */}
          <div style={{ border: "1px solid var(--color-rule)", borderRadius: "var(--radius-1)", backgroundColor: "var(--color-chalk)", padding: "20px" }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)", textTransform: "uppercase", marginBottom: "12px" }}>
              Latency by model (avg ms)
            </div>
            {Object.entries(latencyByModel || {}).length > 0 ? (
              Object.entries(latencyByModel)
                .sort((a, b) => b[1] - a[1])
                .map(([model, latency]) => (
                  <div key={model} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--color-rule)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <div style={{ width: "10px", height: "10px", borderRadius: "2px", backgroundColor: "var(--color-copper)" }} />
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--color-ink)" }}>{model}</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                      <div style={{
                        width: "120px",
                        height: "8px",
                        backgroundColor: "var(--color-rule)",
                        borderRadius: "4px",
                        overflow: "hidden"
                      }}>
                        <div style={{
                          width: `${Math.min(latency / 5000, 1) * 100}%`,
                          height: "100%",
                          backgroundColor: latency > 3000 ? "var(--color-cinnabar)" : latency > 1000 ? "var(--color-ochre)" : "var(--color-copper)",
                          transition: "width 0.3s ease"
                        }} />
                      </div>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", fontWeight: 600, color: latency > 3000 ? "var(--color-cinnabar)" : "var(--color-ink)", minWidth: "60px" }}>
                        {Math.round(latency)}ms
                      </span>
                    </div>
                  </div>
                ))
            ) : (
              <div style={{ color: "var(--color-ink-2)", fontSize: "12px", textAlign: "center", padding: "20px" }}>
                No latency data available
              </div>
            )}
          </div>

          {/* Latency by Step */}
          <div style={{ border: "1px solid var(--color-rule)", borderRadius: "var(--radius-1)", backgroundColor: "var(--color-chalk)", padding: "20px" }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)", textTransform: "uppercase", marginBottom: "12px" }}>
              Latency by pipeline stage (avg ms)
            </div>
            {Object.entries(latencyByStep || {}).length > 0 ? (
              Object.entries(latencyByStep)
                .sort((a, b) => ["intake", "extraction", "classification", "summarization"].indexOf(a[0]) - ["intake", "extraction", "classification", "summarization"].indexOf(b[0]))
                .map(([step, latency]) => (
                  <div key={step} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--color-rule)" }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--color-ink)", textTransform: "capitalize" }}>{step}</span>
                    <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                      <div style={{
                        width: "120px",
                        height: "8px",
                        backgroundColor: "var(--color-rule)",
                        borderRadius: "4px",
                        overflow: "hidden"
                      }}>
                        <div style={{
                          width: `${Math.min(latency / 3000, 1) * 100}%`,
                          height: "100%",
                          backgroundColor: latency > 2000 ? "var(--color-cinnabar)" : latency > 1000 ? "var(--color-ochre)" : "var(--color-copper)",
                          transition: "width 0.3s ease"
                        }} />
                      </div>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", fontWeight: 600, color: latency > 2000 ? "var(--color-cinnabar)" : "var(--color-ink)", minWidth: "60px" }}>
                        {Math.round(latency)}ms
                      </span>
                    </div>
                  </div>
                ))
            ) : (
              <div style={{ color: "var(--color-ink-2)", fontSize: "12px", textAlign: "center", padding: "20px" }}>
                No step latency data available
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
