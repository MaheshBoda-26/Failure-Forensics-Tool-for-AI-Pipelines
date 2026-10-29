import React, { useEffect, useMemo, useState } from "react";
import { Play } from "lucide-react";
import { api } from "../api/client";
import type {
  AnalyticsSnapshot,
  EvalResult,
  EvalRun,
  FaultBenchmarkSummary,
} from "../api/types";
import {
  STEP_LABEL,
  STEP_ORDER,
  formatCost,
  formatMs,
  formatPct,
  humanize,
  shortId,
  stepLabel,
} from "../lib/trace";
import { Empty, Metric, SectionHead, StatusPill } from "./ui";

interface AnalyticsViewProps {
  analytics: AnalyticsSnapshot | null;
  evalRuns: EvalRun[];
  onRefreshAnalytics: () => Promise<void>;
}

const CATEGORIES = [
  { id: "extraction_hallucination", label: "Hallucination" },
  { id: "misclassification", label: "Misclass." },
  { id: "prompt_failure", label: "Prompt" },
  { id: "propagation_error", label: "Propagation" },
  { id: "context_loss", label: "Context" },
  { id: "unknown", label: "Unknown" },
];

function titleCase(value: string): string {
  return value.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export const AnalyticsView: React.FC<AnalyticsViewProps> = ({
  analytics,
  evalRuns,
  onRefreshAnalytics,
}) => {
  const [isRunningEval, setIsRunningEval] = useState(false);
  const [evalNotice, setEvalNotice] = useState<string | null>(null);
  const [evalResults, setEvalResults] = useState<EvalResult[]>([]);
  const [benchmark, setBenchmark] = useState<FaultBenchmarkSummary | null>(null);
  const [isRunningBenchmark, setIsRunningBenchmark] = useState(false);

  const handleRunEval = async () => {
    try {
      setIsRunningEval(true);
      const result = await api.triggerEvalRun();
      if (!result.run_id) {
        setEvalNotice("No eval cases yet. Confirm a diagnosis to create one.");
        setEvalResults([]);
        await onRefreshAnalytics();
        return;
      }
      setEvalNotice(
        `Eval run completed: ${result.passed_count}/${result.total_cases} cases passed.`,
      );
      await onRefreshAnalytics();
      setEvalResults(await api.getEvalResults(result.run_id));
    } catch (err: unknown) {
      setEvalNotice(`Could not run eval: ${(err as Error).message}`);
    } finally {
      setIsRunningEval(false);
      window.setTimeout(() => setEvalNotice(null), 6000);
    }
  };

  const handleRunBenchmark = async () => {
    try {
      setIsRunningBenchmark(true);
      const summary = await api.runFaultBenchmark();
      setBenchmark(summary);
    } catch (err: unknown) {
      setEvalNotice(`Could not run fault benchmark: ${(err as Error).message}`);
      window.setTimeout(() => setEvalNotice(null), 6000);
    } finally {
      setIsRunningBenchmark(false);
    }
  };

  useEffect(() => {
    const latestRun = evalRuns[0];
    if (!latestRun) {
      setEvalResults([]);
      return;
    }
    api
      .getEvalResults(latestRun.run_id)
      .then(setEvalResults)
      .catch(() => setEvalResults([]));
  }, [evalRuns]);

  useEffect(() => {
    let alive = true;
    api
      .getFaultBenchmark()
      .then((res) => {
        if (alive) setBenchmark(res);
      })
      .catch(() => {
        if (alive) setBenchmark(null);
      });
    return () => {
      alive = false;
    };
  }, [analytics?.total_traces]);

  const totalTraces = analytics?.total_traces ?? 0;
  const passRate = analytics ? 1 - analytics.failure_rate : null;

  const categoryCounts = analytics?.failures_by_category ?? {};
  const totalCategoryFailures = Object.values(categoryCounts).reduce((sum, n) => sum + n, 0);
  const topCategoryEntry = Object.entries(categoryCounts).sort((a, b) => b[1] - a[1])[0];
  const topCategory = topCategoryEntry?.[0] ?? null;
  const topCategoryShare =
    topCategoryEntry && totalCategoryFailures > 0
      ? topCategoryEntry[1] / totalCategoryFailures
      : null;

  const totalCost = analytics?.total_cost_usd ?? 0;
  const costByModel = analytics?.cost_by_model ?? {};
  const tokensByModel = analytics?.tokens_by_model ?? {};
  const latencyByStep = analytics?.latency_by_step ?? {};
  const modelPerformance = analytics?.model_performance ?? {};
  const failureCorrelation = analytics?.failure_correlation ?? [];
  const costTrend = analytics?.cost_trend ?? [];
  const avgLatencyMs = analytics?.avg_latency_ms ?? 0;

  const stageRows = useMemo(() => {
    const failuresByStep = analytics?.failures_by_step ?? {};
    const total = STEP_ORDER.reduce((sum, step) => sum + (failuresByStep[step] ?? 0), 0);
    const max = Math.max(...STEP_ORDER.map((step) => failuresByStep[step] ?? 0), 0);
    return STEP_ORDER.map((step) => ({
      step,
      count: failuresByStep[step] ?? 0,
      share: total > 0 ? (failuresByStep[step] ?? 0) / total : 0,
      max,
    }));
  }, [analytics]);

  const totalStageFailures = stageRows.reduce((sum, row) => sum + row.count, 0);
  const maxStageFailures = Math.max(...stageRows.map((row) => row.count), 0);

  const maxCorrelation = Math.max(...failureCorrelation.map((item) => item.count), 0);

  const latestRun = evalRuns[0] ?? null;
  const regressions = latestRun ? latestRun.failed_count : "—";

  const trendMax = Math.max(...costTrend.map((point) => point.cost_usd), 0.0001);
  const trendWidth = 560;
  const trendHeight = 140;
  const trendPadX = 6;
  const trendPadY = 10;
  const trendY = (value: number) =>
    trendHeight - trendPadY - (value / trendMax) * (trendHeight - 2 * trendPadY);
  const trendPoints =
    costTrend.length === 1
      ? `${trendWidth * 0.1},${trendY(costTrend[0].cost_usd)} ${trendWidth * 0.9},${trendY(
          costTrend[0].cost_usd,
        )}`
      : costTrend
          .map((point, index) => {
            const x =
              trendPadX +
              (index / Math.max(costTrend.length - 1, 1)) * (trendWidth - 2 * trendPadX);
            return `${x},${trendY(point.cost_usd)}`;
          })
          .join(" ");

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Analytics</h1>
          <p className="page-sub">Failure isolation, regressions and spend.</p>
        </div>
        <div className="page-actions">
          {evalNotice ? (
            <span className="notice" role="status">
              {evalNotice}
            </span>
          ) : null}
          <button
            className="btn"
            onClick={handleRunBenchmark}
            disabled={isRunningBenchmark}
          >
            <Play size={13} fill="currentColor" />
            {isRunningBenchmark ? "Scoring faults" : "Score fault injector"}
          </button>
          <button
            className="btn btn--primary"
            onClick={handleRunEval}
            disabled={isRunningEval}
          >
            <Play size={13} fill="currentColor" />
            {isRunningEval ? "Running eval" : "Run eval suite"}
          </button>
        </div>
      </div>

      <div className="metrics">
        <Metric
          label="Pass rate"
          value={passRate == null ? "—" : formatPct(passRate)}
          hint={`${totalTraces} traces`}
        />
        <Metric
          label="Top root cause"
          value={topCategory ? titleCase(humanize(topCategory)) : "—"}
          hint={
            topCategoryShare == null
              ? "No classified failures"
              : `${formatPct(topCategoryShare)} of classified failures`
          }
          negative={!!topCategory}
        />
        <Metric
          label="Mean time to root cause"
          value={
            analytics?.avg_time_to_root_cause_seconds != null
              ? formatMs(analytics.avg_time_to_root_cause_seconds * 1000)
              : "—"
          }
          hint="deterministic analyzer"
        />
        <Metric
          label="Open regressions"
          value={regressions}
          hint={latestRun ? `run ${shortId(latestRun.run_id)}` : "no eval run yet"}
          negative={!!latestRun && latestRun.failed_count > 0}
        />
      </div>

      <section className="section">
        <SectionHead
          title="Where failures start"
          hint="Root-cause stage across classified failures."
        />
        <div className="panel">
          <div className="panel__body">
            {totalStageFailures === 0 ? (
              <Empty>No classified failures yet.</Empty>
            ) : (
              <div className="bars">
                {stageRows.map((row) => (
                  <div className="bar" key={row.step}>
                    <span className="bar__label">{STEP_LABEL[row.step]}</span>
                    <span className="bar__track">
                      <span
                        className={`bar__fill${
                          row.count === maxStageFailures && row.count > 0 ? " is-hot" : ""
                        }`}
                        style={{
                          width: `${maxStageFailures > 0 ? (row.count / maxStageFailures) * 100 : 0}%`,
                        }}
                      />
                    </span>
                    <span className="bar__value">
                      <strong>{row.count}</strong> {formatPct(row.share)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="section">
        <SectionHead
          title="Failure correlation"
          hint="Step and category co-occurrence. Darker means more failures."
        />
        <div className="panel">
          <div className="panel__body matrix-wrap">
            <table className="matrix">
              <caption className="sr-only">Failure correlation by stage and category</caption>
              <thead>
                <tr>
                  <th scope="col">Stage</th>
                  {CATEGORIES.map((category) => (
                    <th scope="col" key={category.id}>
                      {category.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {STEP_ORDER.map((step) => (
                  <tr key={step}>
                    <td>{STEP_LABEL[step]}</td>
                    {CATEGORIES.map((category) => {
                      const entry = failureCorrelation.find(
                        (item) => item.step === step && item.category === category.id,
                      );
                      const count = entry?.count ?? 0;
                      const intensity = maxCorrelation > 0 ? count / maxCorrelation : 0;
                      return (
                        <td
                          key={category.id}
                          className={count > 0 ? "is-hot" : undefined}
                          style={
                            count > 0
                              ? {
                                  backgroundColor: `color-mix(in srgb, var(--cinnabar) ${Math.round(
                                    10 + intensity * 45,
                                  )}%, var(--surface))`,
                                }
                              : undefined
                          }
                        >
                          {count}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="section">
        <SectionHead
          title="Fault injection scorecard"
          hint={
            benchmark && benchmark.total > 0
              ? `Step localization ${formatPct(benchmark.step_accuracy, 1)}, category accuracy ${formatPct(benchmark.category_accuracy, 1)} across ${benchmark.total} injected faults (${benchmark.baselines_scored} clean baselines).`
              : "Synthetic corruptions injected into clean traces to verify analyzer localization."
          }
        />
        <div className="panel">
          {!benchmark || benchmark.results.length === 0 ? (
            <div className="panel__body">
              <Empty
                action={
                  <button
                    className="btn btn--sm"
                    onClick={handleRunBenchmark}
                    disabled={isRunningBenchmark}
                  >
                    {isRunningBenchmark ? "Scoring" : "Score fault injector"}
                  </button>
                }
              >
                No eligible clean baseline traces yet. Run the demo first.
              </Empty>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Injected fault</th>
                    <th scope="col">Target stage</th>
                    <th scope="col">Expected category</th>
                    <th scope="col" className="num">
                      Baselines
                    </th>
                    <th scope="col" className="num">
                      Step localization
                    </th>
                    <th scope="col" className="num">
                      Category match
                    </th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {benchmark.results.map((row) => {
                    const scoreable = row.traces > 0;
                    const stepRate = scoreable ? row.step_correct / row.traces : null;
                    const catRate = scoreable ? row.category_correct / row.traces : null;
                    const perfect = stepRate === 1 && catRate === 1;
                    return (
                      <tr key={row.fault}>
                        <td className="mono">{row.fault}</td>
                        <td>{stepLabel(row.expected_step)}</td>
                        <td>{humanize(row.expected_category)}</td>
                        <td className="num">{row.traces}</td>
                        <td className="num">
                          {stepRate == null ? "n/a" : formatPct(stepRate, 0)}
                        </td>
                        <td className="num">
                          {catRate == null ? "n/a" : formatPct(catRate, 0)}
                        </td>
                        <td>
                          <StatusPill
                            tone={!scoreable ? "neutral" : perfect ? "pass" : "fail"}
                          >
                            {!scoreable ? "Skipped" : perfect ? "Verified" : "Miss"}
                          </StatusPill>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <section className="section">
        <SectionHead
          title="Eval regressions"
          hint="Golden suite results for the latest run."
        />
        <div className="panel">
          {evalResults.length === 0 ? (
            <div className="panel__body">
              <Empty
                action={
                  <button
                    className="btn btn--sm"
                    onClick={handleRunEval}
                    disabled={isRunningEval}
                  >
                    {isRunningEval ? "Running eval" : "Run eval suite"}
                  </button>
                }
              >
                No eval results yet. Confirm a diagnosis to create a case.
              </Empty>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Suite</th>
                    <th scope="col">Failure mode</th>
                    <th scope="col">Stage</th>
                    <th scope="col" className="num">
                      Score
                    </th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {evalResults.map((result) => (
                    <tr key={result.result_id}>
                      <td className="mono">{shortId(result.case_id, 14)}</td>
                      <td>{humanize(String(result.details?.category ?? "unknown"))}</td>
                      <td>{stepLabel(result.step_name)}</td>
                      <td className="num">{formatPct(result.score)}</td>
                      <td>
                        <StatusPill tone={result.fixed ? "pass" : "fail"}>
                          {result.fixed ? "Fixed" : "Regression"}
                        </StatusPill>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <section className="section">
        <SectionHead
          title="Models"
          hint={`Share of spans not blamed, average latency ${formatMs(avgLatencyMs)}.`}
        />
        <div className="panel">
          {Object.keys(modelPerformance).length === 0 ? (
            <div className="panel__body">
              <Empty>No model performance data yet.</Empty>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Model</th>
                    <th scope="col" className="num">
                      Span pass rate
                    </th>
                    <th scope="col" className="num">
                      Avg latency
                    </th>
                    <th scope="col" className="num">
                      Avg cost
                    </th>
                    <th scope="col" className="num">
                      Total cost
                    </th>
                    <th scope="col" className="num">
                      Tokens
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(modelPerformance)
                    .sort((a, b) => b[1].span_pass_rate - a[1].span_pass_rate)
                    .map(([model, performance]) => (
                      <tr key={model}>
                        <td className="mono">{model}</td>
                        <td className="num">{formatPct(performance.span_pass_rate, 1)}</td>
                        <td className="num">{formatMs(performance.avg_latency_ms)}</td>
                        <td className="num">{formatCost(performance.avg_cost_usd)}</td>
                        <td className="num">{formatCost(costByModel[model])}</td>
                        <td className="num">
                          {(tokensByModel[model] ?? 0).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <section className="section">
        <SectionHead
          title="Spend and latency"
          hint={`Total spend ${formatCost(totalCost)} across every recorded run.`}
        />
        <div className="panel">
          <div className="panel__body">
            <div className="analytics-grid">
              <div>
                <p className="scores__label">Spend by model</p>
                {Object.keys(costByModel).length === 0 ? (
                  <Empty>No spend recorded yet.</Empty>
                ) : (
                  <div className="bars">
                    {Object.entries(costByModel)
                      .sort((a, b) => b[1] - a[1])
                      .map(([model, cost]) => {
                        const max = Math.max(...Object.values(costByModel), 0.0001);
                        return (
                          <div className="bar" key={model}>
                            <span className="bar__label mono">{model}</span>
                            <span className="bar__track">
                              <span
                                className="bar__fill"
                                style={{ width: `${(cost / max) * 100}%` }}
                              />
                            </span>
                            <span className="bar__value">{formatCost(cost)}</span>
                          </div>
                        );
                      })}
                  </div>
                )}
              </div>

              <div>
                <p className="scores__label">Latency by stage</p>
                {Object.keys(latencyByStep).length === 0 ? (
                  <Empty>No latency recorded yet.</Empty>
                ) : (
                  <div className="bars">
                    {STEP_ORDER.filter((step) => latencyByStep[step] != null).map((step) => {
                      const latency = latencyByStep[step] ?? 0;
                      const max = Math.max(...Object.values(latencyByStep), 1);
                      return (
                        <div className="bar" key={step}>
                          <span className="bar__label">{STEP_LABEL[step]}</span>
                          <span className="bar__track">
                            <span
                              className={`bar__fill${latency > 3000 ? " is-hot" : ""}`}
                              style={{ width: `${(latency / max) * 100}%` }}
                            />
                          </span>
                          <span className="bar__value">{formatMs(latency)}</span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>

            {costTrend.length > 0 ? (
              <div style={{ marginTop: 20 }}>
                <p className="scores__label">Daily spend</p>
                <div className="spark-wrap">
                  <div className="spark-scale">
                    <span>{formatCost(trendMax)}</span>
                    <span>$0</span>
                  </div>
                  <svg
                    className="spark"
                    viewBox={`0 0 ${trendWidth} ${trendHeight}`}
                    preserveAspectRatio="none"
                    role="img"
                    aria-label="Daily spend trend"
                  >
                    {[0.25, 0.5, 0.75].map((fraction) => (
                      <line
                        key={fraction}
                        x1={trendPadX}
                        x2={trendWidth - trendPadX}
                        y1={trendPadY + fraction * (trendHeight - 2 * trendPadY)}
                        y2={trendPadY + fraction * (trendHeight - 2 * trendPadY)}
                        stroke="var(--grid)"
                        strokeWidth="1"
                      />
                    ))}
                    <polyline
                      points={trendPoints}
                      fill="none"
                      stroke="var(--copper)"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </div>
                <div className="spark-axis">
                  <span />
                  <span className="spark-axis__labels">
                    <span>
                      {new Date(
                        costTrend[0].timestamp.includes("T")
                          ? costTrend[0].timestamp
                          : `${costTrend[0].timestamp}T00:00:00`,
                      ).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                      })}
                    </span>
                    <span>
                      {new Date(
                        costTrend[costTrend.length - 1].timestamp.includes("T")
                          ? costTrend[costTrend.length - 1].timestamp
                          : `${costTrend[costTrend.length - 1].timestamp}T00:00:00`,
                      ).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                    </span>
                  </span>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
};
