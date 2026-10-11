import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { GitCompare, Plus, Minus } from "lucide-react";
export const TokenDiffView = ({ trace, diagnosis }) => {
    return (_jsxs("div", { style: {
            display: "flex",
            flexDirection: "column",
            height: "100%",
            backgroundColor: "var(--color-ground)",
        }, children: [_jsxs("div", { style: {
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "14px 28px",
                    borderBottom: "1px solid var(--color-rule)",
                    backgroundColor: "var(--color-chalk)",
                }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: "12px" }, children: [_jsx(GitCompare, { size: 16, color: "var(--color-ink)" }), _jsxs("span", { style: { fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--color-ink)" }, children: ["STRUCTURED TOKEN DIFF \u00B7 ", trace.document_id] }), _jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }, children: "COMPARING GOLDEN GROUND TRUTH VS ACTUAL LLM RESPONSE" })] }), _jsxs("div", { style: { display: "flex", gap: "16px", fontFamily: "var(--font-mono)", fontSize: "11px" }, children: [_jsxs("span", { style: { color: "var(--color-copper)", display: "flex", alignItems: "center", gap: "4px" }, children: [_jsx(Plus, { size: 12 }), " 1 ADDITION"] }), _jsxs("span", { style: { color: "var(--color-cinnabar)", display: "flex", alignItems: "center", gap: "4px" }, children: [_jsx(Minus, { size: 12 }), " 1 ENUM VIOLATION"] })] })] }), _jsxs("div", { style: {
                    flex: 1,
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    divideX: "1px solid var(--color-rule)",
                    overflowY: "auto",
                }, children: [_jsxs("div", { style: { padding: "24px 28px", borderRight: "1px solid var(--color-rule)", display: "flex", flexDirection: "column", gap: "12px" }, children: [_jsxs("div", { style: {
                                    display: "flex",
                                    justifyContent: "space-between",
                                    alignItems: "center",
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "11px",
                                    fontWeight: 700,
                                    color: "var(--color-copper)",
                                    letterSpacing: "var(--tracking-label)",
                                    textTransform: "uppercase",
                                }, children: [_jsx("span", { children: "EXPECTED GROUND TRUTH (GOLDEN CASE)" }), _jsx("span", { style: { backgroundColor: "var(--color-copper-tint)", padding: "2px 6px", borderRadius: "2px" }, children: "PASS CRITERIA" })] }), _jsxs("pre", { style: {
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
                                }, children: [`{
  "trace_id": "golden_${trace.document_id}",
  "document_type": `, _jsx("span", { style: { backgroundColor: "var(--color-copper-tint)", color: "var(--color-copper)", fontWeight: 700, padding: "2px 4px" }, children: "\"INVOICE\"" }), `,
  "confidence_threshold": 0.70,
  "expected_entities": {
    "vendor": "Acme Industrial Supplies",
    "invoice_number": "INV-2026-081",
    "total_amount": 1420.50
  },
  "grounded_token_spans": [
    { "start": 0, "end": 11, "token": "TAX INVOICE" }
  ],
  "validation_status": "VALID"
}`] }), _jsxs("div", { style: { fontSize: "12px", color: "var(--color-ink-3)", lineHeight: 1.5 }, children: ["Ground truth requires document classification to match enum key ", _jsx("code", { children: "INVOICE" }), " with confidence above 0.70 grounded in text tokens."] })] }), _jsxs("div", { style: { padding: "24px 28px", display: "flex", flexDirection: "column", gap: "12px" }, children: [_jsxs("div", { style: {
                                    display: "flex",
                                    justifyContent: "space-between",
                                    alignItems: "center",
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "11px",
                                    fontWeight: 700,
                                    color: "var(--color-cinnabar)",
                                    letterSpacing: "var(--tracking-label)",
                                    textTransform: "uppercase",
                                }, children: [_jsxs("span", { children: ["ACTUAL EXECUTION (TRACE ", trace.trace_id, ")"] }), _jsx("span", { style: { backgroundColor: "var(--color-cinnabar-tint)", padding: "2px 6px", borderRadius: "2px" }, children: "BREAK DETECTED" })] }), _jsxs("pre", { style: {
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
                                }, children: [`{
  "trace_id": "${trace.trace_id}",
  "document_type": `, _jsx("span", { style: { backgroundColor: "var(--color-cinnabar)", color: "#FFFFFF", fontWeight: 700, padding: "2px 6px" }, children: "\"invoice_receipt_v2\"" }), ` /* ✗ INVALID ENUM */,
  "confidence": 0.42 /* ✗ BELOW THRESHOLD */,
  "expected_entities": {
    "vendor": "Acme Industrial Supplies",
    "invoice_number": "INV-2026-081",
    "total_amount": 1420.50
  },
  "grounded_token_spans": [] /* ✗ UNGROUNDED */,
  "validation_status": "SCHEMA_VIOLATION"
}`] }), _jsxs("div", { style: { fontSize: "12px", color: "var(--color-cinnabar)", lineHeight: 1.5, fontWeight: 500 }, children: ["Model generated hallucinated composite type ", _jsx("code", { children: "\"invoice_receipt_v2\"" }), " not present in Pydantic schema enum domain."] })] })] })] }));
};
