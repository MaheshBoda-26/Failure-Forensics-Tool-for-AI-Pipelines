import React from "react";
import { Trace, Span, Diagnosis } from "../api/types";
import { Check, X, AlertTriangle } from "lucide-react";

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
  const steps = [
    { name: "intake", index: 1, label: "01 intake", desc: "Document normalization · no LLM" },
    { name: "extraction", index: 2, label: "02 extraction", desc: "Entity extraction · grounded" },
    { name: "classification", index: 3, label: "03 classification", desc: "DocumentType enum validation" },
    { name: "summarization", index: 4, label: "04 summarization", desc: "Summary & synthesis" },
  ];

  const rootCauseStep = diagnosis?.root_cause_step?.toLowerCase() || "";
  const failingIndex = steps.findIndex((s) => s.name === rootCauseStep);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "16px",
        fontFamily: "var(--font-body)",
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "11px",
          fontWeight: 600,
          letterSpacing: "var(--tracking-label)",
          color: "var(--color-ink-3)",
          textTransform: "uppercase",
        }}
      >
        EXECUTION STRATIGRAPHY (4 STAGES)
      </div>

      <div style={{ display: "flex", flexDirection: "column", position: "relative" }}>
        {steps.map((step, idx) => {
          const span = trace.spans.find((s) => s.step_name.toLowerCase() === step.name);
          const stepScore = diagnosis?.step_scores?.find((s) => s.step_name === step.name);
          const stepChecks = stepScore
            ? Object.keys(stepScore.deterministic_checks).length
            : 0;
          const isFailing = rootCauseStep === step.name;
          const isDownstream = failingIndex !== -1 && idx > failingIndex;
          const isPassed = !isFailing && !isDownstream;
          const isSelected = selectedStep === step.name;

          return (
            <div
              key={step.name}
              onClick={() => onSelectStep(step.name)}
              style={{
                display: "flex",
                gap: "14px",
                alignItems: "flex-start",
                padding: "10px 12px",
                cursor: "pointer",
                borderRadius: "var(--radius-1)",
                backgroundColor: isSelected
                  ? isFailing
                    ? "var(--color-cinnabar-tint)"
                    : "var(--color-chalk)"
                  : "transparent",
                border: isSelected
                  ? `1px solid ${isFailing ? "var(--color-cinnabar)" : "var(--color-rule-strong)"}`
                  : "1px solid transparent",
                opacity: isDownstream ? 0.6 : 1,
                transition: "all 0.15s ease",
              }}
            >
              {/* Vertical Spine Line & Node Marker */}
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  width: "16px",
                  flexShrink: 0,
                  marginTop: "2px",
                }}
              >
                {/* Node Circle */}
                <div
                  style={{
                    width: "14px",
                    height: "14px",
                    borderRadius: "50%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    backgroundColor: isFailing
                      ? "var(--color-cinnabar)"
                      : isPassed
                      ? "var(--color-ground)"
                      : "var(--color-chalk-deep)",
                    border: isFailing
                      ? "2px solid var(--color-cinnabar)"
                      : isPassed
                      ? "2px solid var(--color-copper)"
                      : "2px solid var(--color-rule-strong)",
                  }}
                >
                  {isFailing && <X size={9} color="#FFFFFF" strokeWidth={3} />}
                </div>

                {/* Connecting Line to next step */}
                {idx < steps.length - 1 && (
                  <div
                    style={{
                      width: "2px",
                      height: "46px",
                      backgroundColor: isFailing
                        ? "var(--color-cinnabar)"
                        : isDownstream
                        ? "var(--color-rule)"
                        : "var(--color-rule)",
                      borderLeft: isDownstream ? "2px dashed var(--color-rule-strong)" : "none",
                      margin: "4px 0",
                    }}
                  />
                )}
              </div>

              {/* Step Info */}
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "13px",
                      fontWeight: isSelected || isFailing ? 700 : 500,
                      color: isFailing ? "var(--color-cinnabar)" : "var(--color-ink)",
                    }}
                  >
                    {step.label}
                  </span>
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "11px",
                      color: isFailing ? "var(--color-cinnabar)" : "var(--color-ink-3)",
                    }}
                  >
                    {span ? `${Math.round(span.latency_ms)}ms` : "—"}
                  </span>
                </div>

                <div
                  style={{
                    fontSize: "12px",
                    color: isDownstream ? "var(--color-ink-3)" : "var(--color-ink-2)",
                    marginTop: "2px",
                  }}
                >
                  {step.desc}
                </div>

                {/* Badge Status */}
                <div style={{ marginTop: "4px" }}>
                  {isFailing ? (
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "10px",
                        fontWeight: 700,
                        color: "var(--color-cinnabar)",
                        letterSpacing: "0.04em",
                      }}
                    >
                      ✗ ROOT CAUSE · CHECKS FAILED
                    </span>
                  ) : isDownstream ? (
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "10px",
                        color: "var(--color-ink-3)",
                      }}
                    >
                      ⚠ DOWNSTREAM TAINTED
                    </span>
                  ) : (
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "10px",
                        color: "var(--color-copper)",
                      }}
                    >
                      {stepChecks > 0 ? `✓ ${stepChecks} checks passed` : "✓ passed"}
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
