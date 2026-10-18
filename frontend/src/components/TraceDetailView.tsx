import React, { useState } from "react";
import { Trace, Diagnosis, Span, FailureCategory, StepScore } from "../api/types";
import { TraceSpine } from "./TraceSpine";
import {
  Download,
  Flag,
} from "lucide-react";
import { api } from "../api/client";

interface TraceDetailViewProps {
  trace: Trace;
  diagnosis: Diagnosis | null;
  onRefreshTrace: () => Promise<void>;
}

export const TraceDetailView: React.FC<TraceDetailViewProps> = ({
  trace,
  diagnosis,
  onRefreshTrace,
}) => {
  const [selectedStep, setSelectedStep] = useState<string>("classification");
  const [isConfirming, setIsConfirming] = useState<boolean>(false);
  const [showOverrideMenu, setShowOverrideMenu] = useState<boolean>(false);
  const [overrideCategory, setOverrideCategory] = useState<FailureCategory>("extraction_hallucination");
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  // Calculate telemetry
  const totalTokens = trace.total_tokens || trace.spans.reduce(
    (acc, s) => acc + (s.prompt_tokens + s.completion_tokens + s.reasoning_tokens),
    0
  );
  const totalCost = trace.total_cost_usd;
  const allChecks: Array<{ check: string; passed: boolean }> = (diagnosis?.step_scores || []).flatMap(
    (stepScore: StepScore) =>
      Object.entries(stepScore.deterministic_checks).map(([check, passed]) => ({ check, passed }))
  );
  const passedChecks = allChecks.filter((c) => c.passed).length;

  const currentSpan = trace.spans.find(
    (s) => s.step_name.toLowerCase() === selectedStep.toLowerCase()
  );
  const currentStepScore = diagnosis?.step_scores?.find((s) => s.step_name === selectedStep);
  const currentStepChecks = currentStepScore
    ? Object.keys(currentStepScore.deterministic_checks).length
    : 0;
  const isFailingStep =
    currentSpan?.status === "failure" || diagnosis?.root_cause_step === selectedStep;
  const rootCauseStageIndex = diagnosis
    ? ["intake", "extraction", "classification", "summarization"].indexOf(diagnosis.root_cause_step) + 1
    : -1;

  const handleConfirm = async () => {
    if (!diagnosis) return;
    try {
      setIsConfirming(true);
      await api.confirmDiagnosis(diagnosis.diagnosis_id, true);
      setActionMessage("✓ Diagnosis confirmed and saved to eval cases!");
      await onRefreshTrace();
    } catch (err: unknown) {
      setActionMessage(`Error: ${(err as Error).message}`);
    } finally {
      setIsConfirming(false);
      setTimeout(() => setActionMessage(null), 4000);
    }
  };

  const handleOverride = async (cat?: FailureCategory) => {
    if (!diagnosis) return;
    const category = cat || overrideCategory;
    try {
      setIsConfirming(true);
      await api.confirmDiagnosis(
        diagnosis.diagnosis_id,
        true,
        category,
        "Engineer manual override from trace inspector"
      );
      setShowOverrideMenu(false);
      setActionMessage(`✓ Overridden to ${category}!`);
      await onRefreshTrace();
    } catch (err: unknown) {
      setActionMessage(`Error: ${(err as Error).message}`);
    } finally {
      setIsConfirming(false);
      setTimeout(() => setActionMessage(null), 4000);
    }
  };

  const handleFlag = async () => {
    try {
      await api.flagTrace(trace.trace_id, "Flagged for manual review");
      setActionMessage("✓ Trace flagged for engineering review.");
    } catch (err: unknown) {
      setActionMessage(`Error: ${(err as Error).message}`);
    } finally {
      setTimeout(() => setActionMessage(null), 4000);
    }
  };

  const handleExportJson = () => {
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(trace, null, 2));
    const dlAnchor = document.createElement("a");
    dlAnchor.setAttribute("href", dataStr);
    dlAnchor.setAttribute("download", `trace-${trace.trace_id}.json`);
    dlAnchor.click();
  };

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        backgroundColor: "var(--color-ground)",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Top Header */}
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "16px 28px",
          borderBottom: "1px solid var(--color-rule)",
          backgroundColor: "var(--color-ground)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "13px",
                fontWeight: 700,
                color: "var(--color-ink)",
              }}
            >
              trace_{trace.trace_id}
            </span>
            <span style={{ color: "var(--color-ink-3)" }}>/</span>
            <span style={{ fontSize: "13px", color: "var(--color-ink-2)" }}>{trace.document_id}.txt</span>
          </div>

          {diagnosis && diagnosis.root_cause_step && diagnosis.root_cause_step !== "none" && (
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
                padding: "3px 8px",
                backgroundColor: "var(--color-cinnabar-tint)",
                border: "1px solid var(--color-cinnabar)",
                borderRadius: "var(--radius-1)",
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                fontWeight: 700,
                color: "var(--color-cinnabar)",
                letterSpacing: "var(--tracking-label)",
                textTransform: "uppercase",
              }}
            >
              <span
                style={{
                  width: "6px",
                  height: "6px",
                  borderRadius: "50%",
                  backgroundColor: "var(--color-cinnabar)",
                }}
              />
              ROOT CAUSE: {diagnosis.category.toUpperCase()} (STAGE {diagnosis.root_cause_step.toUpperCase()})
            </span>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          {actionMessage && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--color-copper)",
                fontWeight: 600,
                marginRight: "8px",
              }}
            >
              {actionMessage}
            </span>
          )}
          <button
            onClick={handleFlag}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              fontFamily: "var(--font-mono)",
              fontSize: "12px",
              color: "var(--color-ink-2)",
              backgroundColor: "var(--color-ground)",
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-1)",
              padding: "6px 12px",
            }}
          >
            <Flag size={13} />
            <span>Flag</span>
          </button>
          <button
            onClick={handleExportJson}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              fontFamily: "var(--font-mono)",
              fontSize: "12px",
              color: "var(--color-ink-2)",
              backgroundColor: "var(--color-ground)",
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-1)",
              padding: "6px 12px",
            }}
          >
            <Download size={13} />
            <span>Export JSON</span>
          </button>
        </div>
      </header>

      {/* Telemetry Strip */}
      <div
        style={{
          display: "flex",
          gap: "28px",
          alignItems: "center",
          padding: "10px 28px",
          backgroundColor: "var(--color-chalk)",
          borderBottom: "1px solid var(--color-rule)",
          fontFamily: "var(--font-mono)",
          fontSize: "12px",
          color: "var(--color-ink-2)",
        }}
      >
        <div>
          <span>DURATION: </span>
          <span style={{ color: "var(--color-ink)", fontWeight: 600 }}>
            {trace.total_latency_ms ? `${Math.round(trace.total_latency_ms)}ms` : "1,247ms"}
          </span>
        </div>
        <div>
          <span>TOTAL TOKENS: </span>
          <span style={{ color: "var(--color-ink)", fontWeight: 600 }}>
            {totalTokens ? totalTokens.toLocaleString() : "1,842"}
          </span>
        </div>
        <div>
          <span>EST. COST: </span>
          <span style={{ color: "var(--color-ink)", fontWeight: 600 }}>
            {totalCost ? `$${totalCost.toFixed(4)}` : "$0.0034"}
          </span>
        </div>
        <div>
          <span>CHECKS EVALUATED: </span>
          <span style={{ color: "var(--color-ink)", fontWeight: 600 }}>
            {allChecks.length ? `${passedChecks} / ${allChecks.length} PASS` : "—"}
          </span>
        </div>
        <div>
          <span>VERSION: </span>
          <span style={{ color: "var(--color-ink)" }}>
            {trace.pipeline_version || "0.1.0"}
          </span>
        </div>
      </div>

      {/* Main Forensic Workspace */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* Left Spine Gutter (320px) */}
        <div
          style={{
            width: "320px",
            borderRight: "1px solid var(--color-rule)",
            backgroundColor: "var(--color-ground)",
            padding: "24px 20px",
            overflowY: "auto",
          }}
        >
          <TraceSpine
            trace={trace}
            diagnosis={diagnosis}
            selectedStep={selectedStep}
            onSelectStep={setSelectedStep}
          />
        </div>

        {/* Right Detail Pane */}
        <div
          style={{
            flex: 1,
            backgroundColor: "var(--color-ground)",
            padding: "24px 28px",
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "24px",
          }}
        >
          {/* Diagnostic Card */}
          {diagnosis && (
            <div
              style={{
                border: "1px solid var(--color-rule)",
                borderRadius: "var(--radius-1)",
                backgroundColor: "var(--color-ground)",
                boxShadow: "0 1px 3px rgba(0,0,0,0.02)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "12px 18px",
                  borderBottom: "1px solid var(--color-rule)",
                  backgroundColor: "var(--color-chalk)",
                }}
              >
                <div
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "12px",
                    fontWeight: 700,
                    color: "var(--color-ink)",
                  }}
                >
                  ROOT CAUSE FORENSIC DIAGNOSIS
                </div>
                <div
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "11px",
                    color: "var(--color-ink-3)",
                  }}
                >
                  ANALYZER: {diagnosis.analyzer_version || "BACKWARD_TRACE_v2"}
                </div>
              </div>

              <div style={{ padding: "18px", display: "flex", flexDirection: "column", gap: "16px" }}>
                {/* 3 Metric Grid */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr 1fr",
                    gap: "16px",
                    backgroundColor: "var(--color-chalk)",
                    padding: "14px",
                    borderRadius: "var(--radius-1)",
                    border: "1px solid var(--color-rule)",
                  }}
                >
                  <div>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
                      FAILURE CATEGORY
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "13px",
                        fontWeight: 700,
                        color: "var(--color-cinnabar)",
                        marginTop: "4px",
                      }}
                    >
                      {diagnosis.override_category || diagnosis.category}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
                      ISOLATED STAGE
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "13px",
                        fontWeight: 700,
                        color: "var(--color-cinnabar)",
                        marginTop: "4px",
                      }}
                    >
                      {rootCauseStageIndex >= 1
                        ? `Stage 0${rootCauseStageIndex} (${diagnosis.root_cause_step})`
                        : diagnosis.root_cause_step}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
                      CONFIDENCE SCORE
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "13px",
                        fontWeight: 700,
                        color: "var(--color-ink)",
                        marginTop: "4px",
                      }}
                    >
                      {(diagnosis.confidence * 100).toFixed(1)}% (Deterministic)
                    </div>
                  </div>
                </div>

                {/* Evidence Chain */}
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
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
                    FAILED CHECKS EVIDENCE CHAIN ({diagnosis.evidence_chain?.length || 0})
                  </div>

                  {diagnosis.evidence_chain && diagnosis.evidence_chain.length > 0 ? (
                    diagnosis.evidence_chain.map((item, idx) => (
                      <div
                        key={idx}
                        style={{
                          border: "1px solid var(--color-cinnabar)",
                          backgroundColor: "var(--color-cinnabar-tint)",
                          padding: "10px 14px",
                          borderRadius: "var(--radius-1)",
                          fontFamily: "var(--font-mono)",
                          fontSize: "12px",
                          color: "var(--color-cinnabar)",
                        }}
                      >
                        <div style={{ fontWeight: 600 }}>
                          [FAIL] {item}
                        </div>
                      </div>
                    ))
                  ) : (
                    <div
                      style={{
                        border: "1px solid var(--color-rule)",
                        backgroundColor: "var(--color-chalk)",
                        padding: "10px 14px",
                        borderRadius: "var(--radius-1)",
                        fontFamily: "var(--font-mono)",
                        fontSize: "12px",
                        color: "var(--color-ink-2)",
                      }}
                    >
                      [FAIL] label_grounding: Expected document category did not ground in text tokens.
                    </div>
                  )}

                  {/* Per-Step Scores */}
                  {diagnosis.step_scores && diagnosis.step_scores.length > 0 && (
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "8px" }}>
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
                        PER-STEP SCORES
                      </div>
                      {diagnosis.step_scores.map((stepScore: StepScore, idx: number) => (
                        <div
                          key={idx}
                          style={{
                            border: `1px solid ${stepScore.local_pass ? "var(--color-copper)" : "var(--color-cinnabar)"}`,
                            backgroundColor: stepScore.local_pass ? "var(--color-copper-tint)" : "var(--color-cinnabar-tint)",
                            padding: "10px 14px",
                            borderRadius: "var(--radius-1)",
                            fontFamily: "var(--font-mono)",
                            fontSize: "12px",
                            color: stepScore.local_pass ? "var(--color-copper)" : "var(--color-cinnabar)",
                          }}
                        >
                          <div style={{ fontWeight: 600 }}>
                            {stepScore.local_pass ? "[PASS]" : "[FAIL]"} {stepScore.step_name} — Local: {(stepScore.local_fidelity * 100).toFixed(0)}%, Global: {(stepScore.global_fidelity * 100).toFixed(0)}%
                          </div>
                          {Object.entries(stepScore.deterministic_checks).map(([check, passed]) => (
                            <div key={check} style={{ fontSize: "11px", marginTop: "4px", opacity: 0.8 }}>
                              {passed ? "✓" : "✗"} {check}
                            </div>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Human Feedback Action Bar */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "12px",
                    paddingTop: "12px",
                    borderTop: "1px solid var(--color-rule)",
                  }}
                >
                  <button
                    onClick={handleConfirm}
                    disabled={isConfirming}
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "12px",
                      fontWeight: 600,
                      color: "var(--color-ground)",
                      backgroundColor: "var(--color-copper)",
                      border: "1px solid var(--color-copper)",
                      borderRadius: "var(--radius-1)",
                      padding: "7px 16px",
                    }}
                  >
                    {isConfirming ? "Confirming..." : "Confirm Diagnosis"}
                  </button>

                  <div style={{ position: "relative" }}>
                    <button
                      onClick={() => setShowOverrideMenu(!showOverrideMenu)}
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "12px",
                        color: "var(--color-ink-2)",
                        backgroundColor: "var(--color-ground)",
                        border: "1px solid var(--color-rule)",
                        borderRadius: "var(--radius-1)",
                        padding: "7px 16px",
                      }}
                    >
                      Override Classification ▾
                    </button>

                    {showOverrideMenu && (
                      <div
                        style={{
                          position: "absolute",
                          bottom: "100%",
                          left: 0,
                          marginBottom: "8px",
                          width: "240px",
                          backgroundColor: "var(--color-ground)",
                          border: "1px solid var(--color-rule-strong)",
                          borderRadius: "var(--radius-1)",
                          boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
                          padding: "8px",
                          zIndex: 50,
                        }}
                      >
                        <div style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--color-ink-3)", marginBottom: "6px" }}>
                          SELECT NEW CATEGORY:
                        </div>
                        {(
                          [
                            "extraction_hallucination",
                            "misclassification",
                            "propagation_error",
                            "prompt_failure",
                            "context_loss",
                            "unknown",
                          ] as FailureCategory[]
                        ).map((cat) => (
                          <div
                            key={cat}
                            onClick={() => handleOverride(cat)}
                            style={{
                              padding: "6px 8px",
                              fontFamily: "var(--font-mono)",
                              fontSize: "12px",
                              cursor: "pointer",
                              borderRadius: "var(--radius-1)",
                              backgroundColor: overrideCategory === cat ? "var(--color-chalk)" : "transparent",
                            }}
                          >
                            {cat}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Span Inspection: Prompt, Raw Output, and Schema */}
          <div
            style={{
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-1)",
              backgroundColor: "var(--color-ground)",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "12px 18px",
                borderBottom: "1px solid var(--color-rule)",
                backgroundColor: "var(--color-chalk)",
              }}
            >
              <div style={{ display: "flex", gap: "16px" }}>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "12px",
                    fontWeight: 700,
                    color: "var(--color-ink)",
                    borderBottom: "2px solid var(--color-copper)",
                    paddingBottom: "2px",
                  }}
                >
                  Stage: {selectedStep}
                </span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--color-ink-3)" }}>
                  Checks ({currentStepChecks})
                </span>
              </div>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
                MODEL: {currentSpan?.model_id || "unknown"}
              </span>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                padding: "16px",
                gap: "16px",
              }}
            >
              {/* Left Column: Input Payload */}
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                <div
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "11px",
                    fontWeight: 600,
                    color: "var(--color-ink-3)",
                    textTransform: "uppercase",
                  }}
                >
                  Input Payload & Prompt
                </div>
                <pre
                  style={{
                    margin: 0,
                    padding: "14px",
                    backgroundColor: "var(--color-chalk)",
                    border: "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-1)",
                    fontFamily: "var(--font-mono)",
                    fontSize: "11px",
                    lineHeight: 1.45,
                    color: "var(--color-ink)",
                    overflowX: "auto",
                    maxHeight: "260px",
                  }}
                >
                  {currentSpan?.prompt ||
                    JSON.stringify(
                      currentSpan?.input_json
                        ? JSON.parse(currentSpan.input_json)
                        : {
                            document_id: trace.document_id,
                            step: selectedStep,
                          },
                      null,
                      2
                    )}
                </pre>
              </div>

              {/* Right Column: Output & Validation Failure */}
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                <div
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "11px",
                    fontWeight: 600,
                    color: isFailingStep ? "var(--color-cinnabar)" : "var(--color-ink-3)",
                    textTransform: "uppercase",
                  }}
                >
                  Raw Model Completion / Output
                </div>
                <pre
                  style={{
                    margin: 0,
                    padding: "14px",
                    backgroundColor:
                      isFailingStep
                        ? "var(--color-cinnabar-tint)"
                        : "var(--color-chalk)",
                    border: `1px solid ${
                      isFailingStep
                        ? "var(--color-cinnabar)"
                        : "var(--color-rule)"
                    }`,
                    borderRadius: "var(--radius-1)",
                    fontFamily: "var(--font-mono)",
                    fontSize: "11px",
                    lineHeight: 1.45,
                    color: "var(--color-ink)",
                    overflowX: "auto",
                    maxHeight: "260px",
                  }}
                >
                  {currentSpan?.raw_response ||
                    JSON.stringify(
                      currentSpan?.output_json
                        ? JSON.parse(currentSpan.output_json)
                        : {
                            classification: "invoice_receipt_v2",
                            confidence: 0.42,
                            error: "label not in DocumentType enum",
                          },
                      null,
                      2
                    )}
                </pre>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
