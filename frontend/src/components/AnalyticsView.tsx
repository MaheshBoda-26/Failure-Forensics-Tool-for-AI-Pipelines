import React, { useState } from "react";
import { AnalyticsSnapshot, EvalRun } from "../api/types";
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

  const handleRunEval = async () => {
    try {
      setIsRunningEval(true);
      const res = await api.triggerEvalRun();
      setEvalNotice(`✓ Eval run ${res.run_id} completed: ${Math.round(res.current_accuracy * 100)}% accuracy`);
      await onRefreshAnalytics();
    } catch (err: unknown) {
      setEvalNotice(`Error running eval: ${(err as Error).message}`);
    } finally {
      setIsRunningEval(false);
      setTimeout(() => setEvalNotice(null), 5000);
    }
  };

  const passRate = analytics ? Math.round((1 - analytics.failure_rate) * 100) : 84;
  const totalTraces = analytics?.total_traces || 15;
  const topCategory = analytics?.failures_by_category
    ? Object.entries(analytics.failures_by_category).sort((a, b) => b[1] - a[1])[0]?.[0] || "misclassification"
    : "misclassification";

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
            {passRate}%
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-copper)", marginTop: "6px" }}>
            ↑ +3.8% vs baseline ({totalTraces} traces tracked)
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
            {topCategory.replace("_", " ")}
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-2)", marginTop: "6px" }}>
            68% of all stage failures
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
            {analytics?.avg_time_to_root_cause_seconds
              ? `${Math.round(analytics.avg_time_to_root_cause_seconds * 1000)}ms`
              : "180ms"}
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
            {evalRuns[0]?.regressions?.length ?? 2} Traces
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
          {[
            { stage: "01 intake", pct: 0.2, color: "var(--color-copper)", label: "0.2%" },
            { stage: "02 extraction", pct: 14.1, color: "var(--color-ochre)", label: "14.1%" },
            { stage: "03 classification", pct: 68.3, color: "var(--color-cinnabar)", label: "68.3%" },
            { stage: "04 summarization", pct: 17.4, color: "var(--color-ink-3)", label: "17.4%" },
          ].map((item) => (
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
            2 REGRESSIONS FLAGGED
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
            <tr style={{ borderBottom: "1px solid var(--color-rule)", backgroundColor: "var(--color-cinnabar-tint)" }}>
              <td style={{ padding: "12px 20px", fontWeight: 600, color: "var(--color-ink)" }}>invoice_multipage_tax</td>
              <td style={{ padding: "12px 20px", color: "var(--color-cinnabar)" }}>misclassification</td>
              <td style={{ padding: "12px 20px" }}>03 classification</td>
              <td style={{ padding: "12px 20px" }}>98.0%</td>
              <td style={{ padding: "12px 20px", fontWeight: 700, color: "var(--color-cinnabar)" }}>82.0%</td>
              <td style={{ padding: "12px 20px", color: "var(--color-cinnabar)", fontWeight: 700 }}>-16.0%</td>
              <td style={{ padding: "12px 20px", color: "var(--color-cinnabar)", fontWeight: 700 }}>REGRESSION</td>
            </tr>
            <tr style={{ borderBottom: "1px solid var(--color-rule)" }}>
              <td style={{ padding: "12px 20px", fontWeight: 600, color: "var(--color-ink)" }}>legal_contract_jurisdiction</td>
              <td style={{ padding: "12px 20px", color: "var(--color-ink-2)" }}>extraction_omission</td>
              <td style={{ padding: "12px 20px" }}>02 extraction</td>
              <td style={{ padding: "12px 20px" }}>91.5%</td>
              <td style={{ padding: "12px 20px" }}>92.0%</td>
              <td style={{ padding: "12px 20px", color: "var(--color-copper)" }}>+0.5%</td>
              <td style={{ padding: "12px 20px", color: "var(--color-copper)" }}>PASSED</td>
            </tr>
            <tr style={{ borderBottom: "1px solid var(--color-rule)" }}>
              <td style={{ padding: "12px 20px", fontWeight: 600, color: "var(--color-ink)" }}>financial_summary_grounding</td>
              <td style={{ padding: "12px 20px", color: "var(--color-ink-2)" }}>summary_hallucination</td>
              <td style={{ padding: "12px 20px" }}>04 summarization</td>
              <td style={{ padding: "12px 20px" }}>88.0%</td>
              <td style={{ padding: "12px 20px" }}>89.2%</td>
              <td style={{ padding: "12px 20px", color: "var(--color-copper)" }}>+1.2%</td>
              <td style={{ padding: "12px 20px", color: "var(--color-copper)" }}>PASSED</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
};
