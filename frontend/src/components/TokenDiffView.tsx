import React from "react";
import type { Diagnosis, Trace } from "../api/types";
import { humanize, stepLabel } from "../lib/trace";

interface TokenDiffViewProps {
  trace: Trace;
  diagnosis: Diagnosis | null;
}

function parseJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return { raw };
  }
}

export const TokenDiffView: React.FC<TokenDiffViewProps> = ({ trace, diagnosis }) => {
  const rootCauseStep = diagnosis?.override_step || diagnosis?.root_cause_step;
  const effectiveCategory = diagnosis?.override_category || diagnosis?.category;
  const hasRootCause = !!rootCauseStep && rootCauseStep !== "none";

  const fallbackSpan = trace.spans?.[2] ?? trace.spans?.[0];
  const rootSpan = hasRootCause
    ? trace.spans?.find((span) => span.step_name === rootCauseStep) ?? fallbackSpan
    : fallbackSpan;

  const inputJson = rootSpan ? parseJson(rootSpan.input_json) : {};
  const outputJson = rootSpan ? parseJson(rootSpan.output_json) : {};

  const checks = diagnosis?.step_scores?.flatMap((score) =>
    Object.values(score.deterministic_checks),
  ) ?? [];
  const failedChecks = checks.filter((passed) => !passed).length;
  const passedChecks = checks.length - failedChecks;

  const rootScore = diagnosis?.step_scores?.find(
    (score) => score.step_name === rootCauseStep,
  );
  const classificationSpan = trace.spans?.find((span) => span.step_name === "classification");
  const classifiedOutput = classificationSpan ? parseJson(classificationSpan.output_json) : null;
  const observedDocType =
    classifiedOutput &&
    typeof classifiedOutput === "object" &&
    "document_type" in classifiedOutput
      ? String((classifiedOutput as Record<string, unknown>).document_type)
      : null;

  const criteria = {
    document_id: trace.document_id,
    expected_type: trace.expected_type ?? "not specified",
    observed_type: observedDocType,
    step: rootCauseStep && rootCauseStep !== "none" ? rootCauseStep : null,
    checks: Object.keys(rootScore?.deterministic_checks ?? {}),
  };

  const observed = hasRootCause
    ? {
        trace_id: trace.trace_id,
        step: rootCauseStep,
        category: effectiveCategory,
        confidence: diagnosis?.confidence,
        input: inputJson,
        output: outputJson,
        validation_status: "VIOLATED",
      }
    : {
        trace_id: trace.trace_id,
        step: rootSpan?.step_name ?? null,
        output: outputJson,
        validation_status: "VALID",
      };

  return (
    <section className="panel">
      <div className="panel__head">
        <div>
          <h2 className="panel__title">Output comparison</h2>
          <p className="panel__hint">
            {trace.document_id}, pass criteria against the observed output
          </p>
        </div>
        <div className="diff__counts">
          {checks.length === 0 ? (
            <span className="faint">No checks recorded</span>
          ) : (
            <>
              <span className="is-pass">{passedChecks} passed</span>
              <span className="is-fail">{failedChecks} failed</span>
            </>
          )}
        </div>
      </div>

      <div className="diff__grid">
        <div className="diff__pane">
          <p className="diff__label">Pass criteria</p>
          <pre className="code">{JSON.stringify(criteria, null, 2)}</pre>
          <p className="diff__note">
            {trace.expected_type
              ? `Ground-truth label: ${trace.expected_type}${
                  observedDocType ? ` (classified as ${observedDocType})` : ""
                }.`
              : "No ground-truth label was provided for this document; deterministic checks still apply."}
          </p>
        </div>

        <div className="diff__pane">
          <p className={`diff__label${hasRootCause ? " is-fail" : ""}`}>Observed output</p>
          <pre className={`code${hasRootCause ? " is-fail" : ""}`}>
            {JSON.stringify(observed, null, 2)}
          </pre>
          <p className={`diff__note${hasRootCause ? " is-fail" : ""}`}>
            {hasRootCause
              ? `${humanize(effectiveCategory)} at ${stepLabel(rootCauseStep)}, ${(
                  (diagnosis?.confidence ?? 0) * 100
                ).toFixed(1)}% confidence.`
              : "No deterministic failure detected."}
          </p>
        </div>
      </div>
    </section>
  );
};
