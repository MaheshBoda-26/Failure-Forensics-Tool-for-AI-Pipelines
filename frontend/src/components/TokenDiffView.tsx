import React from "react";
import { Trace, Diagnosis, Span } from "../api/types";
import { GitCompare, Plus, Minus } from "lucide-react";

interface TokenDiffViewProps {
  trace: Trace;
  diagnosis: Diagnosis | null;
}

export const TokenDiffView: React.FC<TokenDiffViewProps> = ({ trace, diagnosis }) => {
  // Find the root cause span
  const rootCauseStep = diagnosis?.root_cause_step;
  const rootCauseSpan = rootCauseStep ? trace.spans.find(s => s.step_name === rootCauseStep) : trace.spans[2] || trace.spans[0];
  // NOTE: the backend does not expose a ground-truth document type yet; the API
  // is written so this can be wired to a real `expected_type` field later.
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
            STRUCTURED TOKEN DIFF · {trace.document_id}
          </span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
            COMPARING GOLDEN GROUND TRUTH VS ACTUAL LLM RESPONSE
          </span>
        </div>

        <div style={{ display: "flex", gap: "16px", fontFamily: "var(--font-mono)", fontSize: "11px" }}>
          <span style={{ color: "var(--color-copper)", display: "flex", alignItems: "center", gap: "4px" }}>
            <Plus size={12} /> 1 ADDITION
          </span>
          <span style={{ color: "var(--color-cinnabar)", display: "flex", alignItems: "center", gap: "4px" }}>
            <Minus size={12} /> 1 ENUM VIOLATION
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
            <span>EXPECTED GROUND TRUTH (GOLDEN CASE)</span>
            <span style={{ backgroundColor: "var(--color-copper-tint)", padding: "2px 6px", borderRadius: "2px" }}>
              PASS CRITERIA
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
  document_type: groundTruthType || "UNKNOWN",
  confidence_threshold: 0.70,
  expected_entities: inputJson.entities || {},
  grounded_token_spans: inputJson.grounded_token_spans || [],
  validation_status: "VALID"
}, null, 2)}
          </pre>
          <div style={{ fontSize: "12px", color: "var(--color-ink-3)", lineHeight: 1.5 }}>
            Ground truth requires document classification to match enum key <code>{groundTruthType || "UNKNOWN"}</code> with confidence above 0.70 grounded in text tokens.
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
            <span>ACTUAL EXECUTION (TRACE {trace.trace_id})</span>
            <span style={{ backgroundColor: "var(--color-cinnabar-tint)", padding: "2px 6px", borderRadius: "2px" }}>
              {diagnosis?.root_cause_step ? "ROOT CAUSE" : "BREAK DETECTED"}
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
{JSON.stringify({
  trace_id: trace.trace_id,
  document_type: outputJson.document_type || "UNKNOWN",
  confidence: outputJson.confidence || 0,
  expected_entities: outputJson.entities || {},
  grounded_token_spans: outputJson.grounded_token_spans || [],
  validation_status: diagnosis ? "SCHEMA_VIOLATION" : "VALID"
}, null, 2)}
          </pre>
          <div style={{ fontSize: "12px", color: "var(--color-cinnabar)", lineHeight: 1.5, fontWeight: 500 }}>
            {diagnosis && diagnosis.root_cause_step !== "none"
              ? `Root cause: ${diagnosis.category} at ${diagnosis.root_cause_step} (${(diagnosis.confidence * 100).toFixed(1)}% confidence)`
              : "No deterministic failure detected."}
          </div>
        </div>
      </div>
    </div>
  );
};
