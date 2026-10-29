import React, { useEffect, useMemo, useState } from "react";
import { ArrowRight, Play, RefreshCw } from "lucide-react";
import { api } from "../api/client";
import type { AnalyticsSnapshot, Diagnosis, EvalRun, Trace } from "../api/types";
import {
  STEP_BLURB,
  STEP_LABEL,
  STEP_ORDER,
  findSpan,
  formatMs,
  formatPct,
  humanize,
  isFailedTrace,
  relativeTime,
  shortId,
  stepIndex,
  stepLabel,
} from "../lib/trace";
import { HonestyMatrix } from "./HonestyMatrix";
import { Empty, Metric, SectionHead, StatusPill } from "./ui";

interface OverviewProps {
  traces: Trace[];
  analytics: AnalyticsSnapshot | null;
  evalRuns: EvalRun[];
  onInspect: (traceId: string) => void;
  onOpenExplorer: () => void;
  onRunDemo: () => Promise<void>;
  isRunningDemo: boolean;
  isRefreshing: boolean;
  lastUpdated: Date | null;
  onRefresh: () => Promise<void>;
}

export const Overview: React.FC<OverviewProps> = ({
  traces,
  analytics,
  evalRuns,
  onInspect,
  onOpenExplorer,
  onRunDemo,
  isRunningDemo,
  isRefreshing,
  lastUpdated,
  onRefresh,
}) => {
  const sorted = useMemo(
    () =>
      [...traces].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    [traces],
  );

  const withSpans = sorted.filter((trace) => (trace.spans?.length ?? 0) > 0);
  const latest = withSpans[0] ?? sorted[0] ?? null;
  const latestFailure = withSpans.find(isFailedTrace) ?? sorted.find(isFailedTrace) ?? null;
  const focus = latestFailure ?? latest;
  const focusId = focus?.trace_id ?? null;
  const [focusDiagnosis, setFocusDiagnosis] = useState<Diagnosis | null>(null);

  useEffect(() => {
    if (!focusId) {
      setFocusDiagnosis(null);
      return;
    }
    let alive = true;
    api
      .getDiagnosis(focusId)
      .then((diagnosis) => {
        if (alive) setFocusDiagnosis(diagnosis);
      })
      .catch(() => {
        if (alive) setFocusDiagnosis(null);
      });
    return () => {
      alive = false;
    };
  }, [focusId, lastUpdated]);

  const effectiveFocusRoot = focusDiagnosis?.override_step || focusDiagnosis?.root_cause_step;
  const diagnosedRoot =
    effectiveFocusRoot && effectiveFocusRoot !== "none"
      ? effectiveFocusRoot
      : null;
  const traceRoot =
    focus?.root_cause_step && focus.root_cause_step !== "none" ? focus.root_cause_step : null;
  const rootStep = diagnosedRoot ?? traceRoot;
  const hasFailure = !!rootStep || focus?.status === "failure";
  const rootIndex = stepIndex(rootStep);

  const totalTraces = analytics?.total_traces ?? traces.length;
  const meanToRoot =
    analytics?.avg_time_to_root_cause_seconds != null
      ? formatMs(analytics.avg_time_to_root_cause_seconds * 1000)
      : "—";
  const latestEval = evalRuns[0] ?? null;
  const regressions = latestEval ? latestEval.failed_count : "—";

  const summary =
    traces.length === 0
      ? "No traces yet. Run the demo to load the 15-document dataset."
      : latestFailure
        ? `${totalTraces} traces in this workspace. Latest break: ${humanize(
            focusDiagnosis?.override_category ?? focusDiagnosis?.category ?? latestFailure.root_cause_step,
          )} at ${stepLabel(latestFailure.root_cause_step)}, ${relativeTime(
            latestFailure.created_at,
          )}.`
        : `${totalTraces} traces in this workspace. No failure in the latest run.`;

  const verdict = hasFailure
    ? `${humanize(focusDiagnosis?.override_category ?? focusDiagnosis?.category ?? rootStep ?? "failure")} at ${
        rootStep ? stepLabel(rootStep) : "an unidentified stage"
      }`
    : focus
      ? focusDiagnosis
        ? "No deterministic failure"
        : "Not diagnosed yet"
      : "No trace yet";

  const evidence = focusDiagnosis?.evidence_chain?.length
    ? focusDiagnosis.evidence_chain[focusDiagnosis.evidence_chain.length - 1]
    : null;

  const failureCounts = STEP_ORDER.map((step) => ({
    step,
    count: analytics?.failures_by_step?.[step] ?? 0,
  }));
  const totalFailures = failureCounts.reduce((sum, item) => sum + item.count, 0);
  const maxFailures = Math.max(...failureCounts.map((item) => item.count), 0);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="page-sub">{summary}</p>
        </div>
        <div className="page-actions">
          <button
            className="icon-btn"
            onClick={onRefresh}
            disabled={isRefreshing}
            aria-label="Refresh data"
          >
            <RefreshCw size={15} className={isRefreshing ? "spin" : undefined} />
          </button>
          {traces.length > 0 ? (
            <button className="btn" onClick={onRunDemo} disabled={isRunningDemo}>
              {isRunningDemo ? (
                <RefreshCw size={14} className="spin" />
              ) : (
                <Play size={13} fill="currentColor" />
              )}
              <span>{isRunningDemo ? "Running demo" : "Run demo"}</span>
            </button>
          ) : null}
          <button
            className="btn btn--primary"
            onClick={() => (focus ? onInspect(focus.trace_id) : onRunDemo())}
            disabled={isRunningDemo || (!focus && traces.length === 0)}
          >
            <span>{focus ? "Inspect latest trace" : "Run demo"}</span>
            <ArrowRight size={14} />
          </button>
        </div>
      </div>

      <div className="metrics">
        <Metric
          label="Traces"
          value={totalTraces}
          hint={analytics ? "in this workspace" : "loaded from the API"}
        />
        <Metric
          label="Failure rate"
          value={analytics ? formatPct(analytics.failure_rate) : "—"}
          hint={analytics ? "of all runs" : "waiting for analytics"}
          negative={!!analytics && analytics.failure_rate > 0}
        />
        <Metric label="Mean time to root cause" value={meanToRoot} hint="deterministic analyzer" />
        <Metric
          label="Open regressions"
          value={regressions}
          hint={latestEval ? "in the latest eval run" : "no eval run yet"}
          negative={latestEval ? latestEval.failed_count > 0 : false}
        />
      </div>

      <section className="section" aria-labelledby="latest-title">
        <div className="section__head">
          <div>
            <h2 className="section__title" id="latest-title">
              {latestFailure ? "Latest failure" : "Latest trace"}
            </h2>
            <p className="section__hint">
              {focus ? (
                <>
                  {focus.document_id}{" "}
                  <span className="mono">{shortId(focus.trace_id)}</span>{" "}
                  {relativeTime(focus.created_at)}
                </>
              ) : (
                "Nothing has run yet."
              )}
            </p>
          </div>
          <div className="page-actions">
            {focus ? (
              <StatusPill tone={hasFailure ? "fail" : "pass"}>
                {hasFailure ? "Failure" : "No failure"}
              </StatusPill>
            ) : null}
            {focus ? (
              <button className="btn btn--sm" onClick={() => onInspect(focus.trace_id)}>
                Inspect trace <ArrowRight size={12} />
              </button>
            ) : null}
          </div>
        </div>

        <div className="panel">
          <div className="chain">
            {STEP_ORDER.map((step, index) => {
              const span = focus ? findSpan(focus, step) : undefined;
              const score = focusDiagnosis?.step_scores?.find(
                (item) => item.step_name === step,
              );
              const checks = score ? Object.entries(score.deterministic_checks) : [];
              const passed = checks.filter(([, ok]) => ok).length;
              const isFail =
                !!focus && hasFailure && (step === rootStep || span?.status === "failure");
              const isTainted = rootIndex >= 0 && index > rootIndex && !isFail;
              const state = !focus
                ? "No trace"
                : isFail
                  ? "Root cause"
                  : isTainted
                    ? "Downstream tainted"
                    : "Passed";

              return (
                <div
                  key={step}
                  className={`chain-step${isFail ? " is-fail" : ""}${
                    isTainted ? " is-tainted" : ""
                  }${!focus ? " is-empty" : ""}`}
                >
                  <div className="chain-rail">
                    <span className="chain-dot" />
                  </div>
                  <div className="chain-step__name">{STEP_LABEL[step]}</div>
                  <div className="chain-step__desc">{STEP_BLURB[step]}</div>
                  <div className="chain-step__meta">
                    <span>{span ? formatMs(span.latency_ms) : "—"}</span>
                    <span>
                      {checks.length > 0
                        ? `${passed}/${checks.length} checks`
                        : span
                          ? "No checks"
                          : "—"}
                    </span>
                  </div>
                  <span className="chain-step__state">{state}</span>
                </div>
              );
            })}
          </div>

          <div className="verdict-strip">
            <div>
              <span className="verdict-strip__label">
                {hasFailure ? "Root cause" : "Diagnosis"}
              </span>
              <p className={`verdict-strip__value${hasFailure ? " is-fail" : ""}`}>{verdict}</p>
            </div>
            <dl className="verdict-strip__facts">
              <div>
                <dt>Confidence</dt>
                <dd>{focusDiagnosis ? formatPct(focusDiagnosis.confidence, 1) : "—"}</dd>
              </div>
              <div>
                <dt>Stage</dt>
                <dd>{rootIndex >= 0 ? `${rootIndex + 1} of 4` : "—"}</dd>
              </div>
            </dl>
            {evidence ? <p className="verdict-strip__finding">{evidence}</p> : null}
            {!focus ? (
              <p className="verdict-strip__finding">
                Run the demo pipeline to generate traces and a diagnosis.
              </p>
            ) : null}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="overview-grid">
          <div>
            <SectionHead
              title="Failures by stage"
              hint="Where the first break starts, across classified failures."
            />
            <div className="panel">
              <div className="panel__body">
                {totalFailures === 0 ? (
                  <Empty>No classified failures yet.</Empty>
                ) : (
                  <div className="bars">
                    {failureCounts.map(({ step, count }) => {
                      const share = totalFailures > 0 ? count / totalFailures : 0;
                      const hot = count === maxFailures && count > 0;
                      return (
                        <div className="bar" key={step}>
                          <span className="bar__label">{STEP_LABEL[step]}</span>
                          <span className="bar__track">
                            <span
                              className={`bar__fill${hot ? " is-hot" : ""}`}
                              style={{ width: `${maxFailures > 0 ? (count / maxFailures) * 100 : 0}%` }}
                            />
                          </span>
                          <span className="bar__value">
                            <strong>{count}</strong> {formatPct(share)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div>
            <SectionHead
              title="Recent traces"
              hint="The four most recent runs in this workspace."
              action={
                <button className="btn btn--ghost btn--sm" onClick={onOpenExplorer}>
                  View all <ArrowRight size={12} />
                </button>
              }
            />
            <div className="panel">
              {sorted.length === 0 ? (
                <div className="panel__body">
                  <Empty>No traces yet.</Empty>
                </div>
              ) : (
                <div className="recent">
                  {sorted.slice(0, 4).map((trace) => {
                    const failed = isFailedTrace(trace);
                    return (
                      <button
                        className="recent-row"
                        key={trace.trace_id}
                        onClick={() => onInspect(trace.trace_id)}
                      >
                        <span className="recent-row__main">
                          <span className="recent-row__id">{shortId(trace.trace_id)}</span>
                          <span className="recent-row__doc">{trace.document_id}</span>
                        </span>
                        <span className="recent-row__right">
                          <StatusPill tone={failed ? "fail" : "pass"}>
                            {failed ? "Failure" : "Passed"}
                          </StatusPill>
                          <span className="recent-row__ms">{formatMs(trace.total_latency_ms)}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <SectionHead
          title="Guarantees and limits"
          hint="What the analyzer promises, and where it stops."
        />
        <HonestyMatrix />
      </section>
    </div>
  );
};
