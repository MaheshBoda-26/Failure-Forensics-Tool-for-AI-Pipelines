import React from "react";
import type { Diagnosis, Trace } from "../api/types";
import {
  STEP_BLURB,
  STEP_LABEL,
  STEP_ORDER,
  findSpan,
  formatMs,
  stepIndex,
} from "../lib/trace";

interface TraceSpineProps {
  trace: Trace;
  diagnosis: Diagnosis | null;
  selectedStep: string;
  onSelectStep: (stepName: string) => void;
}

export const TraceSpine: React.FC<TraceSpineProps> = ({
  trace,
  diagnosis,
  selectedStep,
  onSelectStep,
}) => {
  const rootCause =
    diagnosis?.root_cause_step && diagnosis.root_cause_step !== "none"
      ? diagnosis.root_cause_step.toLowerCase()
      : null;
  const rootIndex = stepIndex(rootCause);

  const steps = React.useMemo(() => {
    const names = new Set<string>(STEP_ORDER);
    (trace.spans ?? []).forEach((span) => names.add(span.step_name.toLowerCase()));
    return Array.from(names);
  }, [trace]);

  return (
    <div className="spine">
      <p className="spine__label">Execution</p>
      <div className="spine__steps">
        {steps.map((step, index) => {
          const span = findSpan(trace, step);
          const score = diagnosis?.step_scores?.find(
            (item) => item.step_name.toLowerCase() === step,
          );
          const checks = score ? Object.entries(score.deterministic_checks) : [];
          const passed = checks.filter(([, ok]) => ok).length;
          const isFail = step === rootCause || span?.status === "failure";
          const isTainted = rootIndex >= 0 && index > rootIndex && !isFail;
          const isSelected = selectedStep.toLowerCase() === step;
          const state = isFail
            ? "Root cause"
            : isTainted
              ? "Downstream tainted"
              : checks.length > 0
                ? `${passed}/${checks.length} checks passed`
                : span
                  ? "Passed"
                  : "No span recorded";

          return (
            <button
              key={step}
              className={`spine-step${isFail ? " is-fail" : ""}${
                isTainted ? " is-tainted" : ""
              }`}
              onClick={() => onSelectStep(step)}
              aria-pressed={isSelected}
            >
              <span className="spine-step__dot" aria-hidden="true" />
              <span className="spine-step__body">
                <span className="spine-step__top">
                  <span className="spine-step__name">
                    {STEP_LABEL[step] ?? step}
                  </span>
                  <span className="spine-step__ms">{span ? formatMs(span.latency_ms) : "—"}</span>
                </span>
                <span className="spine-step__desc">{STEP_BLURB[step] ?? "Pipeline stage"}</span>
                <span className="spine-step__status">{state}</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
};
