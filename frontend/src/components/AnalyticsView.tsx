import React, { useState, useMemo, useEffect } from "react";
import { AnalyticsSnapshot, EvalRun, EvalResult } from "../api/types";
import { api } from "../api/client";
import { Play, TrendingUp, AlertTriangle, ShieldCheck, Clock } from "lucide-react";

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
            Reliability & Failure Analytics
          </h2>
          <p style={{ margin: "4px 0 0 0", color: "var(--color-ink-2)", fontSize: "14px" }}>
            Aggregate fault isolation, stage vulnerability distribution, and golden regression trends.
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
            <span>{isRunningEval ? "RUNNING EVAL SUITE..." : "RUN EVAL SUITE"}</span>
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
            PIPELINE PASS RATE
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
            TOP ROOT CAUSE
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
            MEAN TIME TO ROOT CAUSE
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
            GOLDEN REGRESSIONS
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
              Pipeline Stage Vulnerability Map
            </div>
            <div style={{ fontSize: "13px", color: "var(--color-ink-2)", marginTop: "2px" }}>
              Originating fault step vs downstream manifestation point
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
            GOLDEN EVAL BENCHMARK REGRESSION TABLE
          </div>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-cinnabar)", fontWeight: 700 }}>
            {evalResults.filter(r => !r.fixed).length} REGRESSIONS FLAGGED
          </span>
        </div>

        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-mono)", fontSize: "12px", textAlign: "left" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--color-rule)", color: "var(--color-ink-3)", fontSize: "11px" }}>
              <th style={{ padding: "12px 20px" }}>BENCHMARK SUITE</th>
              <th style={{ padding: "12px 20px" }}>FAILURE MODE</th>
              <th style={{ padding: "12px 20px" }}>ISOLATED STEP</th>
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
    </div>
  );
};
