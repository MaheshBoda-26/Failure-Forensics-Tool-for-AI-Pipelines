import React from "react";
import { Check, X } from "lucide-react";

export const HonestyMatrix: React.FC = () => {
  const guarantees = [
    {
      feature: "Root cause isolation",
      guaranteed: "Deterministic backward trace identifies the earliest failing check in the sequence.",
      limit: "Heuristic classification if multiple unrelated steps violate schemas independently.",
    },
    {
      feature: "Cascading failure detection",
      guaranteed: "Marks downstream spans as tainted when ancestor span contracts break.",
      limit: "Does not simulate counterfactual runs (what would have happened if step 2 succeeded).",
    },
    {
      feature: "Telemetry capture",
      guaranteed: "Exact span timings, raw prompt, raw response, token counts, and check evaluations.",
      limit: "Depends on client telemetry instrumentation wrapping model invocations.",
    },
    {
      feature: "Eval suite regression gating",
      guaranteed: "Golden test suites compare baseline vs current release accuracy on exact seeds.",
      limit: "v0 runs offline SQLite; multi-tenant cloud persistence scheduled for v1.",
    },
  ];

  return (
    <div
      style={{
        border: "1px solid var(--color-rule)",
        borderRadius: "var(--radius-1)",
        backgroundColor: "var(--color-ground)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "16px 24px",
          borderBottom: "1px solid var(--color-rule)",
          backgroundColor: "var(--color-chalk)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 700, color: "var(--color-ink)" }}>
          TECHNICAL BOUNDARY & HONESTY MATRIX
        </span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>
          SPECIFICATION INTEGRITY
        </span>
      </div>

      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", textAlign: "left" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--color-rule)", color: "var(--color-ink-3)", fontFamily: "var(--font-mono)", fontSize: "11px" }}>
            <th style={{ padding: "12px 24px", width: "220px" }}>CAPABILITY</th>
            <th style={{ padding: "12px 24px" }}>DETERMINISTIC GUARANTEE</th>
            <th style={{ padding: "12px 24px" }}>V0 BOUNDARY / LIMITATION</th>
          </tr>
        </thead>
        <tbody>
          {guarantees.map((g, idx) => (
            <tr key={idx} style={{ borderBottom: idx < guarantees.length - 1 ? "1px solid var(--color-rule)" : "none" }}>
              <td style={{ padding: "14px 24px", fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 600, color: "var(--color-ink)" }}>
                {g.feature}
              </td>
              <td style={{ padding: "14px 24px", color: "var(--color-ink)", lineHeight: 1.5 }}>
                <div style={{ display: "flex", gap: "8px", alignItems: "flex-start" }}>
                  <span style={{ color: "var(--color-copper)", fontWeight: 700, fontFamily: "var(--font-mono)" }}>✓</span>
                  <span>{g.guaranteed}</span>
                </div>
              </td>
              <td style={{ padding: "14px 24px", color: "var(--color-ink-2)", lineHeight: 1.5 }}>
                <div style={{ display: "flex", gap: "8px", alignItems: "flex-start" }}>
                  <span style={{ color: "var(--color-ink-3)", fontWeight: 700, fontFamily: "var(--font-mono)" }}>—</span>
                  <span>{g.limit}</span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
