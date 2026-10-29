import React, { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowLeft,
  Check,
  ChevronDown,
  Download,
  Flag,
  X,
} from "lucide-react";
import { api } from "../api/client";
import type { Diagnosis, FailureCategory, StepScore, Trace } from "../api/types";
import {
  STEP_ORDER,
  findSpan,
  formatCost,
  formatMs,
  formatPct,
  humanize,
  relativeTime,
  shortId,
  stepIndex,
  stepLabel,
  totalTokens,
} from "../lib/trace";
import { TraceSpine } from "./TraceSpine";
import { Empty, StatusPill } from "./ui";

interface TraceDetailViewProps {
  trace: Trace;
  diagnosis: Diagnosis | null;
  onRefreshTrace: () => Promise<void>;
  onBack?: () => void;
}

const CATEGORIES: FailureCategory[] = [
  "extraction_hallucination",
  "misclassification",
  "propagation_error",
  "prompt_failure",
  "context_loss",
  "unknown",
];

/**
 * Parse a span payload for display without ever throwing during render.
 * `input_json`/`output_json` are model-controlled text, and a span from a
 * `prompt_schema` failure is not JSON at all.
 */
function parseSpanJson(raw: string | undefined): { ok: true; value: unknown } | { ok: false; raw: string } {
  const text = raw ?? "";
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false, raw: text };
  }
}

function renderSpanJson(raw: string | undefined, fallback: unknown): string {
  if (!raw) {
    return JSON.stringify(fallback, null, 2);
  }
  const parsed = parseSpanJson(raw);
  return JSON.stringify(parsed.ok ? parsed.value : parsed.raw, null, 2);
}

export const TraceDetailView: React.FC<TraceDetailViewProps> = ({
  trace,
  diagnosis,
  onRefreshTrace,
  onBack,
}) => {
  const [selectedStep, setSelectedStep] = useState<string>("intake");
  const [isConfirming, setIsConfirming] = useState(false);
  const [isDiagnosing, setIsDiagnosing] = useState(false);
  const [showOverrideMenu, setShowOverrideMenu] = useState(false);
  const [overrideCategory, setOverrideCategory] =
    useState<FailureCategory>("extraction_hallucination");
  const [message, setMessage] = useState<string | null>(null);
  const [messageError, setMessageError] = useState(false);

  const steps = useMemo(() => {
    const names = new Set<string>(STEP_ORDER);
    (trace.spans ?? []).forEach((span) => names.add(span.step_name.toLowerCase()));
    return Array.from(names);
  }, [trace]);

  const effectiveRootStep = diagnosis?.override_step || diagnosis?.root_cause_step;

  useEffect(() => {
    const root = effectiveRootStep;
    if (root && root !== "none") {
      setSelectedStep(root.toLowerCase());
      return;
    }
    setSelectedStep((trace.spans?.[0]?.step_name ?? "intake").toLowerCase());
  }, [trace.trace_id, effectiveRootStep, trace.spans]);

  const notify = (text: string, error = false) => {
    setMessage(text);
    setMessageError(error);
    window.setTimeout(() => setMessage(null), 4500);
  };

  const tokens = totalTokens(trace);
  const allChecks: Array<{ check: string; passed: boolean }> = (
    diagnosis?.step_scores ?? []
  ).flatMap((score: StepScore) =>
    Object.entries(score.deterministic_checks).map(([check, passed]) => ({ check, passed })),
  );
  const passedChecks = allChecks.filter((item) => item.passed).length;

  const currentSpan = findSpan(trace, selectedStep);
  const currentScore = diagnosis?.step_scores?.find(
    (score) => score.step_name.toLowerCase() === selectedStep,
  );
  const currentChecks = currentScore ? Object.keys(currentScore.deterministic_checks).length : 0;
  const isFailingStep =
    currentSpan?.status === "failure" ||
    (effectiveRootStep ?? "").toLowerCase() === selectedStep;

  const rootStep =
    effectiveRootStep && effectiveRootStep !== "none"
      ? effectiveRootStep
      : null;
  const hasRootCause = !!rootStep;
  const rootStage = stepIndex(rootStep);
  const category = diagnosis?.override_category || diagnosis?.category;
  const analyzer = diagnosis
    ? diagnosis.analyzer_fallback
      ? "Judge fallback"
      : diagnosis.judge_analysis
        ? "Deterministic and judge"
        : "Deterministic"
    : "Not run";

  const statusTone = hasRootCause
    ? "fail"
    : diagnosis
      ? "pass"
      : trace.status === "failure"
        ? "fail"
        : "neutral";
  const statusLabel = hasRootCause
    ? "Failure"
    : diagnosis
      ? "No failure"
      : trace.status === "failure"
        ? "Failure"
        : "Undiagnosed";

  const handleConfirm = async () => {
    if (!diagnosis) return;
    try {
      setIsConfirming(true);
      await api.confirmDiagnosis(diagnosis.diagnosis_id, true);
      notify("Diagnosis confirmed.");
      await onRefreshTrace();
    } catch (err: unknown) {
      notify(`Could not confirm: ${(err as Error).message}`, true);
    } finally {
      setIsConfirming(false);
    }
  };

  const handleOverride = async (next: FailureCategory) => {
    if (!diagnosis) return;
    try {
      setIsConfirming(true);
      await api.confirmDiagnosis(
        diagnosis.diagnosis_id,
        true,
        next,
        "Manual override from the trace inspector",
        selectedStep,
      );
      setShowOverrideMenu(false);
      notify(`Override saved: ${humanize(next)}.`);
      await onRefreshTrace();
    } catch (err: unknown) {
      notify(`Could not override: ${(err as Error).message}`, true);
    } finally {
      setIsConfirming(false);
    }
  };

  const handleDiagnose = async () => {
    try {
      setIsDiagnosing(true);
      await api.runDiagnosis(trace.trace_id);
      notify("Diagnosis complete.");
      await onRefreshTrace();
    } catch (err: unknown) {
      notify(`Could not run diagnosis: ${(err as Error).message}`, true);
    } finally {
      setIsDiagnosing(false);
    }
  };

  const handleFlag = async () => {
    try {
      await api.flagTrace(trace.trace_id, "Flagged for manual review");
      notify("Trace flagged for review.");
    } catch (err: unknown) {
      notify(`Could not flag trace: ${(err as Error).message}`, true);
    }
  };

  const handleExport = () => {
    const data =
      "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(trace, null, 2));
    const link = document.createElement("a");
    link.setAttribute("href", data);
    link.setAttribute("download", `trace-${trace.trace_id}.json`);
    link.click();
  };

  const messageNode = message ? (
    <span className={`notice${messageError ? " is-error" : ""}`} role="status">
      {message}
    </span>
  ) : null;

  return (
    <div className="detail">
      <div className="detail__head">
        <div>
          {onBack ? (
            <button className="btn btn--ghost btn--sm detail__back" onClick={onBack}>
              <ArrowLeft size={14} /> All traces
            </button>
          ) : null}
          <h2 className="detail__title">
            Trace
            <StatusPill tone={statusTone}>{statusLabel}</StatusPill>
          </h2>
          <div className="detail__meta">
            <span className="mono">{shortId(trace.trace_id, 12)}</span>
            <span>{trace.document_id}</span>
            <span>{relativeTime(trace.created_at)}</span>
            <span>{trace.spans?.length ?? 0} spans</span>
          </div>
        </div>

        <div className="detail__actions">
          <button className="btn btn--sm" onClick={handleFlag}>
            <Flag size={13} /> Flag
          </button>
          <button className="btn btn--sm" onClick={handleExport}>
            <Download size={13} /> Export
          </button>
        </div>
      </div>

      <div className="telemetry">
        <div className="telemetry__cell">
          <div className="telemetry__label">Duration</div>
          <div className="telemetry__value">{formatMs(trace.total_latency_ms)}</div>
        </div>
        <div className="telemetry__cell">
          <div className="telemetry__label">Tokens</div>
          <div className="telemetry__value">{tokens.toLocaleString()}</div>
        </div>
        <div className="telemetry__cell">
          <div className="telemetry__label">Cost</div>
          <div className="telemetry__value">{formatCost(trace.total_cost_usd)}</div>
        </div>
        <div className="telemetry__cell">
          <div className="telemetry__label">Checks</div>
          <div className="telemetry__value">
            {allChecks.length > 0 ? `${passedChecks}/${allChecks.length}` : "—"}
          </div>
        </div>
        <div className="telemetry__cell">
          <div className="telemetry__label">Pipeline</div>
          <div className="telemetry__value">{trace.pipeline_version || "0.1.0"}</div>
        </div>
      </div>

      <div className="detail__body">
        <TraceSpine
          trace={trace}
          diagnosis={diagnosis}
          selectedStep={selectedStep}
          onSelectStep={setSelectedStep}
        />

        <div className="stack">
          <section className="panel">
            <div className="panel__head">
              <div>
                <h2 className="panel__title">Diagnosis</h2>
                <p className="panel__hint">{diagnosis ? analyzer : "Not run"}</p>
              </div>
              {diagnosis ? (
                <StatusPill
                  tone={
                    diagnosis.overridden ? "warn" : diagnosis.confirmed ? "pass" : "neutral"
                  }
                >
                  {diagnosis.overridden
                    ? "Overridden"
                    : diagnosis.confirmed
                      ? "Confirmed"
                      : "Unconfirmed"}
                </StatusPill>
              ) : null}
            </div>

            {diagnosis ? (
              <div className="panel__body">
                <div className="verdict">
                  <div>
                    <p className="verdict__label">
                      {hasRootCause ? "Root cause" : "Result"}
                    </p>
                    <p className={`verdict__value${hasRootCause ? " is-fail" : ""}`}>
                      {hasRootCause
                        ? `${humanize(category)} at ${stepLabel(rootStep)}`
                        : "No deterministic failure"}
                    </p>
                  </div>
                  <dl className="verdict__facts">
                    <div>
                      <dt>Confidence</dt>
                      <dd>{formatPct(diagnosis.confidence, 1)}</dd>
                    </div>
                    <div>
                      <dt>Stage</dt>
                      <dd>{rootStage >= 0 ? `${rootStage + 1} of 4` : "—"}</dd>
                    </div>
                    <div>
                      <dt>Analyzer</dt>
                      <dd>{diagnosis.analyzer_version || "deterministic"}</dd>
                    </div>
                  </dl>
                </div>

                {diagnosis.evidence_chain?.length ? (
                  <div className={`evidence${hasRootCause ? " is-fail" : ""}`}>
                    <p className="evidence__label">
                      {hasRootCause ? "Evidence from the failing stage" : "Evidence"}
                    </p>
                    {diagnosis.evidence_chain.map((item, index) => (
                      <p className="evidence__item" key={`${item}-${index}`}>
                        {item}
                      </p>
                    ))}
                  </div>
                ) : null}

                {diagnosis.step_scores?.length ? (
                  <div className="scores">
                    <p className="scores__label">Per-stage checks</p>
                    <div className="table-wrap">
                      <table className="table table--scores">
                        <thead>
                          <tr>
                            <th scope="col">Stage</th>
                            <th scope="col" className="num">
                              Local
                            </th>
                            <th scope="col" className="num">
                              Global
                            </th>
                            <th scope="col">Checks</th>
                          </tr>
                        </thead>
                        <tbody>
                          {diagnosis.step_scores.map((score) => (
                            <tr key={score.step_name}>
                              <td>{stepLabel(score.step_name)}</td>
                              <td className="num">{formatPct(score.local_fidelity)}</td>
                              <td className="num">{formatPct(score.global_fidelity)}</td>
                              <td>
                                {Object.entries(score.deterministic_checks).length === 0 ? (
                                  <span className="faint">none</span>
                                ) : (
                                  Object.entries(score.deterministic_checks).map(
                                    ([check, ok]) => (
                                      <span
                                        className={`check ${ok ? "is-pass" : "is-fail"}`}
                                        key={check}
                                      >
                                        {ok ? <Check size={11} /> : <X size={11} />}
                                        {check}
                                      </span>
                                    ),
                                  )
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : null}

                <div className="panel__actions">
                  <button
                    className="btn btn--primary btn--sm"
                    onClick={handleConfirm}
                    disabled={isConfirming}
                  >
                    <Check size={13} />
                    {isConfirming ? "Saving" : "Confirm diagnosis"}
                  </button>

                  <div className="menu-anchor">
                    <button
                      className="btn btn--sm"
                      onClick={() => setShowOverrideMenu((open) => !open)}
                      aria-haspopup="menu"
                      aria-expanded={showOverrideMenu}
                      disabled={isConfirming}
                    >
                      Override <ChevronDown size={13} />
                    </button>

                    {showOverrideMenu ? (
                      <>
                        <div
                          className="popover-backdrop"
                          onClick={() => setShowOverrideMenu(false)}
                          aria-hidden="true"
                        />
                        <div className="popover popover--menu" role="menu" aria-label="Override category">
                          {CATEGORIES.map((option) => (
                            <button
                              className="menu-item"
                              key={option}
                              role="menuitem"
                              onClick={() => handleOverride(option)}
                            >
                              {humanize(option)}
                            </button>
                          ))}
                        </div>
                      </>
                    ) : null}
                  </div>

                  {messageNode}
                </div>
              </div>
            ) : (
              <div className="panel__body">
                <Empty
                  action={
                    <button
                      className="btn btn--primary btn--sm"
                      onClick={handleDiagnose}
                      disabled={isDiagnosing}
                    >
                      <Activity size={13} />
                      {isDiagnosing ? "Running" : "Run diagnosis"}
                    </button>
                  }
                >
                  No diagnosis stored for this trace.
                </Empty>
                {messageNode ? <div className="panel__actions">{messageNode}</div> : null}
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel__head">
              <div>
                <h2 className="panel__title">Stage payload</h2>
                <p className="panel__hint">
                  {stepLabel(selectedStep)}
                  {currentSpan?.model_id ? `, ${currentSpan.model_id}` : ""}
                </p>
              </div>
              <span className="faint num" style={{ fontSize: 12 }}>
                {currentChecks} checks
              </span>
            </div>

            <div className="panel__body">
              {currentSpan ? (
                <div className="payload__grid">
                  <div>
                    <p className="payload__label">Input</p>
                    <pre className="code">
                      {currentSpan.prompt ||
                        renderSpanJson(currentSpan.input_json, {
                          document_id: trace.document_id,
                          step: selectedStep,
                        })}
                    </pre>
                  </div>
                  <div>
                    <p className={`payload__label${isFailingStep ? " is-fail" : ""}`}>Output</p>
                    <pre className={`code${isFailingStep ? " is-fail" : ""}`}>
                      {currentSpan.raw_response ||
                        renderSpanJson(currentSpan.output_json, {
                          document_id: trace.document_id,
                          step: selectedStep,
                          status: "no output recorded",
                        })}
                    </pre>
                  </div>
                </div>
              ) : (
                <Empty>No payload recorded for this stage.</Empty>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
};
