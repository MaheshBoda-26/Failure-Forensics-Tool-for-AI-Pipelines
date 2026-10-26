import React, { useState, useMemo } from "react";
import { Trace, Diagnosis, StepScore } from "../api/types";
import { ZoomIn, ZoomOut, Maximize2, Share2, Layers } from "lucide-react";

interface DAGGraphViewProps {
  trace: Trace;
  diagnosis: Diagnosis | null;
}

export const DAGGraphView: React.FC<DAGGraphViewProps> = ({ trace, diagnosis }) => {
  const [selectedNode, setSelectedNode] = useState<string>("classification");
  const [zoomLevel, setZoomLevel] = useState<number>(1);

  // Build nodes dynamically from trace spans and diagnosis step_scores
  const nodes = useMemo(() => {
    if (!trace.spans || trace.spans.length === 0) return [];

    const stepScoresMap = new Map<string, StepScore>();
    if (diagnosis?.step_scores) {
      diagnosis.step_scores.forEach((ss) => stepScoresMap.set(ss.step_name, ss));
    }

    // Order: intake, extraction, classification, summarization
    const stepOrder = ["intake", "extraction", "classification", "summarization"];

    // Copy before sorting: `.sort` mutates in place, and mutating the
    // `trace.spans` prop during render reorders the parent's data.
    return [...trace.spans]
      .sort((a, b) => stepOrder.indexOf(a.step_name) - stepOrder.indexOf(b.step_name))
      .map((span, idx) => {
        const stepScore = stepScoresMap.get(span.step_name);
        // A span that raised is a failure even when no step score blames it:
        // `local_pass` is only about deterministic checks, and a crashed step
        // may have none.
        const spanFailed = span.status === "failure";
        const isFailed = spanFailed || (stepScore ? !stepScore.local_pass : false);
        const isTainted = !isFailed && !!stepScore && stepScore.local_pass && !stepScore.global_pass;

        // Get check count
        const totalChecks = stepScore ? Object.keys(stepScore.deterministic_checks).length : 0;
        const passedChecks = stepScore ? Object.values(stepScore.deterministic_checks).filter(Boolean).length : 0;
        const failedChecks = totalChecks - passedChecks;

        // Get root cause stage marker
        const isRootCause = diagnosis?.root_cause_step === span.step_name;

        return {
          id: span.step_name,
          stage: `STAGE ${String(idx + 1).padStart(2, "0")}${isRootCause ? " · ROOT CAUSE" : ""}`,
          name: span.step_name,
          desc: getStepDescription(span.step_name),
          tokens: `${span.prompt_tokens + span.completion_tokens + span.reasoning_tokens} tokens`,
          duration: `${span.latency_ms}ms`,
          status: isFailed ? "FAILED" : isTainted ? "TAINTED" : "PASSED",
          // Report the number of *failed* checks, not the number that passed.
          // `2/3 checks failed` was previously printed for a step that had
          // passed 2 of 3.
          checks:
            totalChecks > 0
              ? isFailed
                ? `${failedChecks}/${totalChecks} checks failed`
                : `${passedChecks}/${totalChecks} checks passed`
              : span.status === "failure"
                ? "Step raised an exception"
                : "No checks",
        };
      });
  }, [trace, diagnosis]);

  const getStepDescription = (stepName: string): string => {
    switch (stepName) {
      case "intake":
        return "Document normalization and UTF-8 validation";
      case "extraction":
        return "Grounded entity extraction";
      case "classification":
        return "DocumentType strict enum mapping";
      case "summarization":
        return "Context synthesis and downstream consumption";
      default:
        return "Processing step";
    }
  };

  const selectedNodeData = nodes.find(n => n.id === selectedNode);
  const failingIndex = diagnosis?.root_cause_step
    ? nodes.findIndex((n) => n.id === diagnosis.root_cause_step)
    : -1;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        backgroundColor: "var(--color-ground)",
        overflow: "hidden",
      }}
    >
      {/* DAG sub-header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "12px 28px",
          borderBottom: "1px solid var(--color-rule)",
          backgroundColor: "var(--color-ground)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--color-ink)" }}>
            DAG execution graph · {trace.trace_id}
          </span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
            Deterministic dependency topology
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <button
            onClick={() => setZoomLevel((z) => Math.max(0.7, z - 0.1))}
            style={{
              padding: "6px 10px",
              backgroundColor: "var(--color-chalk)",
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-1)",
              color: "var(--color-ink-2)",
            }}
          >
            <ZoomOut size={13} />
          </button>
          <button
            onClick={() => setZoomLevel((z) => Math.min(1.4, z + 0.1))}
            style={{
              padding: "6px 10px",
              backgroundColor: "var(--color-chalk)",
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-1)",
              color: "var(--color-ink-2)",
            }}
          >
            <ZoomIn size={13} />
          </button>
          <button
            onClick={() => setZoomLevel(1)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              fontFamily: "var(--font-mono)",
              fontSize: "12px",
              padding: "6px 12px",
              backgroundColor: "var(--color-chalk)",
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-1)",
              color: "var(--color-ink-2)",
            }}
          >
            <Maximize2 size={12} />
            <span>Fit View</span>
          </button>
        </div>
      </div>

      {/* Canvas Area */}
      <div
        style={{
          flex: 1,
          backgroundColor: "var(--color-chalk)",
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "48px",
          overflow: "auto",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "36px",
            transform: `scale(${zoomLevel})`,
            transition: "transform 0.2s ease",
          }}
        >
          {nodes.map((node, idx) => {
            const isSelected = selectedNode === node.id;
            const isFailed = node.status === "FAILED";
            const isTainted = node.status === "TAINTED";

            return (
              <React.Fragment key={node.id}>
                {/* Node Box */}
                <div
                  onClick={() => setSelectedNode(node.id)}
                  style={{
                    width: "230px",
                    backgroundColor: "var(--color-ground)",
                    border: isFailed
                      ? "2px solid var(--color-cinnabar)"
                      : isSelected
                      ? "2px solid var(--color-copper)"
                      : "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-1)",
                    boxShadow: isFailed
                      ? "0 4px 16px rgba(169, 58, 32, 0.15)"
                      : "0 2px 6px rgba(0,0,0,0.03)",
                    cursor: "pointer",
                    overflow: "hidden",
                    opacity: isTainted ? 0.7 : 1,
                    transition: "all 0.15s ease",
                  }}
                >
                  {/* Node Header */}
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "8px 12px",
                      backgroundColor: isFailed
                        ? "var(--color-cinnabar-tint)"
                        : "var(--color-chalk)",
                      borderBottom: `1px solid ${isFailed ? "var(--color-cinnabar)" : "var(--color-rule)"}`,
                    }}
                  >
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "10px",
                        fontWeight: 700,
                        color: isFailed ? "var(--color-cinnabar)" : "var(--color-ink)",
                      }}
                    >
                      {node.stage}
                    </span>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "10px",
                        fontWeight: 700,
                        color: isFailed
                          ? "var(--color-cinnabar)"
                          : isTainted
                          ? "var(--color-ochre)"
                          : "var(--color-copper)",
                      }}
                    >
                      {node.status}
                    </span>
                  </div>

                  {/* Node Content */}
                  <div style={{ padding: "14px" }}>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "14px",
                        fontWeight: 700,
                        color: isFailed ? "var(--color-cinnabar)" : "var(--color-ink)",
                      }}
                    >
                      {node.name}
                    </div>
                    <div style={{ fontSize: "12px", color: "var(--color-ink-2)", marginTop: "4px" }}>
                      {node.desc}
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "11px",
                        color: isFailed ? "var(--color-cinnabar)" : "var(--color-ink-3)",
                        marginTop: "10px",
                        display: "flex",
                        justifyContent: "space-between",
                      }}
                    >
                      <span>{node.duration}</span>
                      <span>{node.tokens}</span>
                    </div>
                  </div>
                </div>

                {/* Connector Arrow */}
                {idx < nodes.length - 1 && (() => {
                  const isFailingConnector = failingIndex !== -1 && idx === failingIndex - 1;
                  const isDownstreamConnector = failingIndex !== -1 && idx >= failingIndex;
                  return (
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        position: "relative",
                        width: "36px",
                      }}
                    >
                      <div
                        style={{
                          height: "2px",
                          width: "100%",
                          backgroundColor: isFailingConnector
                            ? "var(--color-cinnabar)"
                            : "var(--color-rule-strong)",
                          borderTop: isDownstreamConnector
                            ? "2px dashed var(--color-rule-strong)"
                            : "none",
                        }}
                      />
                      <div
                        style={{
                          position: "absolute",
                          right: 0,
                          width: 0,
                          height: 0,
                          borderTop: "4px solid transparent",
                          borderBottom: "4px solid transparent",
                          borderLeft: `6px solid ${isFailingConnector ? "var(--color-cinnabar)" : "var(--color-rule-strong)"}`,
                        }}
                      />
                    </div>
                  );
                })()}
              </React.Fragment>
            );
          })}
        </div>
      </div>

      {/* Selected Node Details Drawer */}
      <div
        style={{
          borderTop: "1px solid var(--color-rule)",
          backgroundColor: "var(--color-ground)",
          padding: "16px 28px",
          maxHeight: "180px",
          overflowY: "auto",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 700 }}>
            Node inspection: {selectedNode.toUpperCase()}
          </span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
            Click a node above to view its metadata
          </span>
        </div>
        <p style={{ margin: 0, fontSize: "13px", color: "var(--color-ink-2)" }}>
          {selectedNodeData
            ? `${selectedNodeData.stage}: ${selectedNodeData.tokens}, ${selectedNodeData.duration}. ${selectedNodeData.checks}. ${diagnosis?.root_cause_step === selectedNode ? "Identified as root cause by backward trace analysis." : selectedNodeData.status === "TAINTED" ? "Downstream of failed step - output may be corrupted." : "All checks passed."}`
            : "Select a node to view its metadata."}
        </p>
      </div>
    </div>
  );
};
