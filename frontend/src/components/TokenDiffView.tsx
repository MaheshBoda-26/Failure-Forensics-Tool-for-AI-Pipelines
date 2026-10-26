import React from "react";
import { Trace, Diagnosis, Span } from "../api/types";
import { GitCompare, Plus, Minus } from "lucide-react";

interface TokenDiffViewProps {
  trace: Trace;
  diagnosis: Diagnosis | null;
}

export const TokenDiffView: React.FC<TokenDiffViewProps> = ({ trace, diagnosis }) => {
  // The failure verdict, taken from the diagnosis rather than from the mere
  // existence of a Diagnosis object (a passing trace has one too).
  const rootCauseStep = diagnosis?.root_cause_step;
  const hasRootCause = !!rootCauseStep && rootCauseStep !== "none";

  // Find the root cause span; fall back to the last non-intake step (or the
  // first span) when the analyzer reported no failure. `rootCauseStep` used to
  // be checked for truthiness only, and `"none"` is truthy — so on every clean
  // trace this looked up a step literally named "none" and resolved to
  // `undefined`.
  const fallbackSpan = trace.spans[2] || trace.spans[0];
  const rootCauseSpan = hasRootCause
    ? trace.spans.find((s) => s.step_name === rootCauseStep) || fallbackSpan
    : fallbackSpan;

  // NOTE: the backend does not expose a ground-truth document type yet; the API
  // is written so this can be wired to a real `expected_type` field later.
  // Reporting "UNKNOWN" is honest; the previous banner claimed a diff count.
  const groundTruthType = "UNKNOWN";

  const parseJson = (jsonStr: string) => {
    try {
      return JSON.parse(jsonStr);
    } catch {
      return { raw: jsonStr };
    }
  };

  const inputJson = rootCauseSpan ? parseJson(rootCauseSpan.input_json) : {};
  const outputJson = rootCauseSpan ? parseJson(rootCauseSpan.output_json) : {};

  const checks = diagnosis?.step_scores?.flatMap((step) =>
    Object.values(step.deterministic_checks)
  ) ?? [];
  const failedChecks = checks.filter((passed) => !passed).length;
  const passedChecks = checks.length - failedChecks;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        backgroundColor: "var(--color-ground)",
      }}
    >
      {/* Diff Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "14px 28px",
          borderBottom: "1px solid var(--color-rule)",
          backgroundColor: "var(--color-chalk)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <GitCompare size={16} color="var(--color-ink)" />
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--color-ink)" }}>
            Structured token diff · {trace.document_id}
          </span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
            Comparing golden ground truth vs actual LLM response
          </span>
        </div>

        <div style={{ display: "flex", gap: "16px", fontFamily: "var(--font-mono)", fontSize: "11px" }}>
          <span style={{ color: "var(--color-copper)", display: "flex", alignItems: "center", gap: "4px" }}>
            <Plus size={12} /> {passedChecks} CHECK{passedChecks === 1 ? "" : "S"} PASSED
          </span>
          <span style={{ color: "var(--color-cinnabar)", display: "flex", alignItems: "center", gap: "4px" }}>
            <Minus size={12} /> {failedChecks} FAILED
          </span>
        </div>
      </div>

      {/* Side-by-Side Diff Panes */}
      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          overflowY: "auto",
        }}
      >
        {/* Left Pane: Expected Ground Truth */}
        <div style={{ padding: "24px 28px", borderRight: "1px solid var(--color-rule)", display: "flex", flexDirection: "column", gap: "12px" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
              fontWeight: 700,
              color: "var(--color-copper)",
              letterSpacing: "var(--tracking-label)",
              textTransform: "uppercase",
            }}
          >
            <span>Expected ground truth (golden case)</span>
            <span style={{ backgroundColor: "var(--color-copper-tint)", padding: "2px 6px", borderRadius: "2px" }}>
              Pass criteria
            </span>
          </div>

          <pre
            style={{
              margin: 0,
              padding: "18px",
              backgroundColor: "var(--color-chalk)",
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-1)",
              fontFamily: "var(--font-mono)",
              fontSize: "12px",
              lineHeight: 1.6,
              color: "var(--color-ink)",
              overflowX: "auto",
            }}
          >
{JSON.stringify({
  trace_id: `golden_${trace.document_id}`,
  document_type: groundTruthType,
  confidence_threshold: 0.70,
  expected_input_shape: Object.keys(inputJson as Record<string, unknown>),
  validation_status: "VALID"
}, null, 2)}
          </pre>
          <div style={{ fontSize: "12px", color: "var(--color-ink-3)", lineHeight: 1.5 }}>
            The API does not expose a labelled ground truth for this document, so this
            pane shows the pass criteria only. The actual classification and confidence
            the pipeline produced are on the right.
          </div>
        </div>

        {/* Right Pane: Actual Execution Output */}
        <div style={{ padding: "24px 28px", display: "flex", flexDirection: "column", gap: "12px" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
              fontWeight: 700,
              color: "var(--color-cinnabar)",
              letterSpacing: "var(--tracking-label)",
              textTransform: "uppercase",
            }}
          >
            <span>Actual execution (trace {trace.trace_id})</span>
            <span style={{ backgroundColor: "var(--color-cinnabar-tint)", padding: "2px 6px", borderRadius: "2px" }}>
              {hasRootCause ? "Root cause" : "No failure"}
            </span>
          </div>

          <pre
            style={{
              margin: 0,
              padding: "18px",
              backgroundColor: "var(--color-cinnabar-tint)",
              border: "1px solid var(--color-cinnabar)",
              borderRadius: "var(--radius-1)",
              fontFamily: "var(--font-mono)",
              fontSize: "12px",
              lineHeight: 1.6,
              color: "var(--color-ink)",
              overflowX: "auto",
            }}
          >
{JSON.stringify(
  hasRootCause
    ? {
        trace_id: trace.trace_id,
        step: rootCauseStep,
        category: diagnosis?.category,
        // Render the failing span's own output rather than assuming every
        // failure is a schema violation on an extraction/classification shape.
        input: inputJson,
        output: outputJson,
        validation_status: "VIOLATED",
      }
    : {
        trace_id: trace.trace_id,
        step: rootCauseSpan?.step_name ?? null,
        output: outputJson,
        validation_status: "VALID",
      },
  null,
  2
)}
          </pre>
          <div style={{ fontSize: "12px", color: "var(--color-cinnabar)", lineHeight: 1.5, fontWeight: 500 }}>
            {hasRootCause
              ? `Root cause: ${diagnosis?.category} at ${rootCauseStep} (${((diagnosis?.confidence ?? 0) * 100).toFixed(1)}% confidence)`
              : "No deterministic failure detected."}
          </div>
        </div>
      </div>
    </div>
  );
};
