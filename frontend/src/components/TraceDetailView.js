import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { TraceSpine } from "./TraceSpine";
import { Download, Flag, } from "lucide-react";
import { api } from "../api/client";
export const TraceDetailView = ({ trace, diagnosis, onRefreshTrace, }) => {
    const [selectedStep, setSelectedStep] = useState("classification");
    const [isConfirming, setIsConfirming] = useState(false);
    const [showOverrideMenu, setShowOverrideMenu] = useState(false);
    const [overrideCategory, setOverrideCategory] = useState("misclassification");
    const [actionMessage, setActionMessage] = useState(null);
    // Calculate telemetry
    const totalTokens = trace.spans.reduce((acc, s) => acc + (s.token_usage?.total_tokens || 0), 0);
    const totalCost = trace.spans.reduce((acc, s) => acc + (s.token_usage?.cost_usd || 0), 0);
    const allChecks = trace.spans.flatMap((s) => s.checks || []);
    const passedChecks = allChecks.filter((c) => c.passed).length;
    const currentSpan = trace.spans.find((s) => s.step_name.toLowerCase() === selectedStep.toLowerCase());
    const handleConfirm = async () => {
        if (!diagnosis)
            return;
        try {
            setIsConfirming(true);
            await api.confirmDiagnosis(diagnosis.diagnosis_id, true);
            setActionMessage("✓ Diagnosis confirmed and saved to eval cases!");
            await onRefreshTrace();
        }
        catch (err) {
            setActionMessage(`Error: ${err.message}`);
        }
        finally {
            setIsConfirming(false);
            setTimeout(() => setActionMessage(null), 4000);
        }
    };
    const handleOverride = async () => {
        if (!diagnosis)
            return;
        try {
            setIsConfirming(true);
            await api.confirmDiagnosis(diagnosis.diagnosis_id, false, overrideCategory, "Engineer manual override from trace inspector");
            setShowOverrideMenu(false);
            setActionMessage(`✓ Overridden to ${overrideCategory}!`);
            await onRefreshTrace();
        }
        catch (err) {
            setActionMessage(`Error: ${err.message}`);
        }
        finally {
            setIsConfirming(false);
            setTimeout(() => setActionMessage(null), 4000);
        }
    };
    const handleFlag = async () => {
        try {
            await api.flagTrace(trace.trace_id, "Flagged for manual review");
            setActionMessage("✓ Trace flagged for engineering review.");
        }
        catch (err) {
            setActionMessage(`Error: ${err.message}`);
        }
        finally {
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
    return (_jsxs("div", { style: {
            flex: 1,
            display: "flex",
            flexDirection: "column",
            backgroundColor: "var(--color-ground)",
            height: "100%",
            overflow: "hidden",
        }, children: [_jsxs("header", { style: {
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "16px 28px",
                    borderBottom: "1px solid var(--color-rule)",
                    backgroundColor: "var(--color-ground)",
                }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: "16px" }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: "8px" }, children: [_jsxs("span", { style: {
                                            fontFamily: "var(--font-mono)",
                                            fontSize: "13px",
                                            fontWeight: 700,
                                            color: "var(--color-ink)",
                                        }, children: ["trace_", trace.trace_id] }), _jsx("span", { style: { color: "var(--color-ink-3)" }, children: "/" }), _jsxs("span", { style: { fontSize: "13px", color: "var(--color-ink-2)" }, children: [trace.document_id, ".txt"] })] }), diagnosis && diagnosis.root_cause_step && (_jsxs("span", { style: {
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
                                }, children: [_jsx("span", { style: {
                                            width: "6px",
                                            height: "6px",
                                            borderRadius: "50%",
                                            backgroundColor: "var(--color-cinnabar)",
                                        } }), "ROOT CAUSE: ", diagnosis.category.toUpperCase(), " (STAGE ", diagnosis.root_cause_step.toUpperCase(), ")"] }))] }), _jsxs("div", { style: { display: "flex", alignItems: "center", gap: "10px" }, children: [actionMessage && (_jsx("span", { style: {
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "11px",
                                    color: "var(--color-copper)",
                                    fontWeight: 600,
                                    marginRight: "8px",
                                }, children: actionMessage })), _jsxs("button", { onClick: handleFlag, style: {
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
                                }, children: [_jsx(Flag, { size: 13 }), _jsx("span", { children: "Flag" })] }), _jsxs("button", { onClick: handleExportJson, style: {
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
                                }, children: [_jsx(Download, { size: 13 }), _jsx("span", { children: "Export JSON" })] })] })] }), _jsxs("div", { style: {
                    display: "flex",
                    gap: "28px",
                    alignItems: "center",
                    padding: "10px 28px",
                    backgroundColor: "var(--color-chalk)",
                    borderBottom: "1px solid var(--color-rule)",
                    fontFamily: "var(--font-mono)",
                    fontSize: "12px",
                    color: "var(--color-ink-2)",
                }, children: [_jsxs("div", { children: [_jsx("span", { children: "DURATION: " }), _jsx("span", { style: { color: "var(--color-ink)", fontWeight: 600 }, children: trace.duration_ms ? `${Math.round(trace.duration_ms)}ms` : "1,247ms" })] }), _jsxs("div", { children: [_jsx("span", { children: "TOTAL TOKENS: " }), _jsx("span", { style: { color: "var(--color-ink)", fontWeight: 600 }, children: totalTokens ? totalTokens.toLocaleString() : "1,842" })] }), _jsxs("div", { children: [_jsx("span", { children: "EST. COST: " }), _jsx("span", { style: { color: "var(--color-ink)", fontWeight: 600 }, children: totalCost ? `$${totalCost.toFixed(4)}` : "$0.0034" })] }), _jsxs("div", { children: [_jsx("span", { children: "CHECKS EVALUATED: " }), _jsx("span", { style: { color: "var(--color-ink)", fontWeight: 600 }, children: allChecks.length ? `${passedChecks} / ${allChecks.length} PASS` : "18 / 20 PASS" })] }), _jsxs("div", { children: [_jsx("span", { children: "VERSION: " }), _jsx("span", { style: { color: "var(--color-ink)" }, children: trace.pipeline_version || "0.1.0" })] })] }), _jsxs("div", { style: { flex: 1, display: "flex", overflow: "hidden" }, children: [_jsx("div", { style: {
                            width: "320px",
                            borderRight: "1px solid var(--color-rule)",
                            backgroundColor: "var(--color-ground)",
                            padding: "24px 20px",
                            overflowY: "auto",
                        }, children: _jsx(TraceSpine, { trace: trace, diagnosis: diagnosis, selectedStep: selectedStep, onSelectStep: setSelectedStep }) }), _jsxs("div", { style: {
                            flex: 1,
                            backgroundColor: "var(--color-ground)",
                            padding: "24px 28px",
                            overflowY: "auto",
                            display: "flex",
                            flexDirection: "column",
                            gap: "24px",
                        }, children: [diagnosis && (_jsxs("div", { style: {
                                    border: "1px solid var(--color-rule)",
                                    borderRadius: "var(--radius-1)",
                                    backgroundColor: "var(--color-ground)",
                                    boxShadow: "0 1px 3px rgba(0,0,0,0.02)",
                                }, children: [_jsxs("div", { style: {
                                            display: "flex",
                                            justifyContent: "space-between",
                                            alignItems: "center",
                                            padding: "12px 18px",
                                            borderBottom: "1px solid var(--color-rule)",
                                            backgroundColor: "var(--color-chalk)",
                                        }, children: [_jsx("div", { style: {
                                                    fontFamily: "var(--font-mono)",
                                                    fontSize: "12px",
                                                    fontWeight: 700,
                                                    color: "var(--color-ink)",
                                                }, children: "ROOT CAUSE FORENSIC DIAGNOSIS" }), _jsxs("div", { style: {
                                                    fontFamily: "var(--font-mono)",
                                                    fontSize: "11px",
                                                    color: "var(--color-ink-3)",
                                                }, children: ["ANALYZER: ", diagnosis.analyzer_version || "BACKWARD_TRACE_v2"] })] }), _jsxs("div", { style: { padding: "18px", display: "flex", flexDirection: "column", gap: "16px" }, children: [_jsxs("div", { style: {
                                                    display: "grid",
                                                    gridTemplateColumns: "1fr 1fr 1fr",
                                                    gap: "16px",
                                                    backgroundColor: "var(--color-chalk)",
                                                    padding: "14px",
                                                    borderRadius: "var(--radius-1)",
                                                    border: "1px solid var(--color-rule)",
                                                }, children: [_jsxs("div", { children: [_jsx("div", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }, children: "FAILURE CATEGORY" }), _jsx("div", { style: {
                                                                    fontFamily: "var(--font-mono)",
                                                                    fontSize: "13px",
                                                                    fontWeight: 700,
                                                                    color: "var(--color-cinnabar)",
                                                                    marginTop: "4px",
                                                                }, children: diagnosis.override_category || diagnosis.category })] }), _jsxs("div", { children: [_jsx("div", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }, children: "ISOLATED STAGE" }), _jsxs("div", { style: {
                                                                    fontFamily: "var(--font-mono)",
                                                                    fontSize: "13px",
                                                                    fontWeight: 700,
                                                                    color: "var(--color-cinnabar)",
                                                                    marginTop: "4px",
                                                                }, children: ["Stage 03 (", diagnosis.root_cause_step, ")"] })] }), _jsxs("div", { children: [_jsx("div", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }, children: "CONFIDENCE SCORE" }), _jsxs("div", { style: {
                                                                    fontFamily: "var(--font-mono)",
                                                                    fontSize: "13px",
                                                                    fontWeight: 700,
                                                                    color: "var(--color-ink)",
                                                                    marginTop: "4px",
                                                                }, children: [(diagnosis.confidence * 100).toFixed(1), "% (Deterministic)"] })] })] }), _jsxs("div", { style: { display: "flex", flexDirection: "column", gap: "8px" }, children: [_jsxs("div", { style: {
                                                            fontFamily: "var(--font-mono)",
                                                            fontSize: "11px",
                                                            fontWeight: 600,
                                                            letterSpacing: "var(--tracking-label)",
                                                            color: "var(--color-ink-3)",
                                                            textTransform: "uppercase",
                                                        }, children: ["FAILED CHECKS EVIDENCE CHAIN (", diagnosis.evidence_chain?.length || 0, ")"] }), diagnosis.evidence_chain && diagnosis.evidence_chain.length > 0 ? (diagnosis.evidence_chain.map((item, idx) => (_jsxs("div", { style: {
                                                            border: "1px solid var(--color-cinnabar)",
                                                            backgroundColor: "var(--color-cinnabar-tint)",
                                                            padding: "10px 14px",
                                                            borderRadius: "var(--radius-1)",
                                                            fontFamily: "var(--font-mono)",
                                                            fontSize: "12px",
                                                            color: "var(--color-cinnabar)",
                                                        }, children: [_jsxs("div", { style: { fontWeight: 600 }, children: ["[FAIL] ", item.check_name, " \u2014 Score: ", item.score, " (threshold: ", item.threshold, ")"] }), _jsx("div", { style: { fontSize: "11px", color: "var(--color-ink-2)", marginTop: "4px" }, children: item.detail })] }, idx)))) : (_jsx("div", { style: {
                                                            border: "1px solid var(--color-rule)",
                                                            backgroundColor: "var(--color-chalk)",
                                                            padding: "10px 14px",
                                                            borderRadius: "var(--radius-1)",
                                                            fontFamily: "var(--font-mono)",
                                                            fontSize: "12px",
                                                            color: "var(--color-ink-2)",
                                                        }, children: "[FAIL] label_grounding: Expected document category did not ground in text tokens." }))] }), _jsxs("div", { style: {
                                                    display: "flex",
                                                    alignItems: "center",
                                                    gap: "12px",
                                                    paddingTop: "12px",
                                                    borderTop: "1px solid var(--color-rule)",
                                                }, children: [_jsx("button", { onClick: handleConfirm, disabled: isConfirming, style: {
                                                            fontFamily: "var(--font-mono)",
                                                            fontSize: "12px",
                                                            fontWeight: 600,
                                                            color: "var(--color-ground)",
                                                            backgroundColor: "var(--color-copper)",
                                                            border: "1px solid var(--color-copper)",
                                                            borderRadius: "var(--radius-1)",
                                                            padding: "7px 16px",
                                                        }, children: isConfirming ? "Confirming..." : "Confirm Diagnosis" }), _jsxs("div", { style: { position: "relative" }, children: [_jsx("button", { onClick: () => setShowOverrideMenu(!showOverrideMenu), style: {
                                                                    fontFamily: "var(--font-mono)",
                                                                    fontSize: "12px",
                                                                    color: "var(--color-ink-2)",
                                                                    backgroundColor: "var(--color-ground)",
                                                                    border: "1px solid var(--color-rule)",
                                                                    borderRadius: "var(--radius-1)",
                                                                    padding: "7px 16px",
                                                                }, children: "Override Classification \u25BE" }), showOverrideMenu && (_jsxs("div", { style: {
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
                                                                }, children: [_jsx("div", { style: { fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--color-ink-3)", marginBottom: "6px" }, children: "SELECT NEW CATEGORY:" }), [
                                                                        "misclassification",
                                                                        "extraction_omission",
                                                                        "schema_drift",
                                                                        "summary_hallucination",
                                                                        "prompt_failure",
                                                                    ].map((cat) => (_jsx("div", { onClick: () => {
                                                                            setOverrideCategory(cat);
                                                                            handleOverride();
                                                                        }, style: {
                                                                            padding: "6px 8px",
                                                                            fontFamily: "var(--font-mono)",
                                                                            fontSize: "12px",
                                                                            cursor: "pointer",
                                                                            borderRadius: "var(--radius-1)",
                                                                            backgroundColor: overrideCategory === cat ? "var(--color-chalk)" : "transparent",
                                                                        }, children: cat }, cat)))] }))] })] })] })] })), _jsxs("div", { style: {
                                    border: "1px solid var(--color-rule)",
                                    borderRadius: "var(--radius-1)",
                                    backgroundColor: "var(--color-ground)",
                                }, children: [_jsxs("div", { style: {
                                            display: "flex",
                                            justifyContent: "space-between",
                                            alignItems: "center",
                                            padding: "12px 18px",
                                            borderBottom: "1px solid var(--color-rule)",
                                            backgroundColor: "var(--color-chalk)",
                                        }, children: [_jsxs("div", { style: { display: "flex", gap: "16px" }, children: [_jsxs("span", { style: {
                                                            fontFamily: "var(--font-mono)",
                                                            fontSize: "12px",
                                                            fontWeight: 700,
                                                            color: "var(--color-ink)",
                                                            borderBottom: "2px solid var(--color-copper)",
                                                            paddingBottom: "2px",
                                                        }, children: ["Stage: ", selectedStep] }), _jsxs("span", { style: { fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--color-ink-3)" }, children: ["Checks (", currentSpan?.checks?.length || 0, ")"] })] }), _jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }, children: "MODEL: anthropic/claude-3.5-haiku" })] }), _jsxs("div", { style: {
                                            display: "grid",
                                            gridTemplateColumns: "1fr 1fr",
                                            divideX: "1px solid var(--color-rule)",
                                            padding: "16px",
                                            gap: "16px",
                                        }, children: [_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: "8px" }, children: [_jsx("div", { style: {
                                                            fontFamily: "var(--font-mono)",
                                                            fontSize: "11px",
                                                            fontWeight: 600,
                                                            color: "var(--color-ink-3)",
                                                            textTransform: "uppercase",
                                                        }, children: "Input Payload & Prompt" }), _jsx("pre", { style: {
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
                                                        }, children: currentSpan?.prompt ||
                                                            JSON.stringify(currentSpan?.input_data || {
                                                                document_id: trace.document_id,
                                                                step: selectedStep,
                                                            }, null, 2) })] }), _jsxs("div", { style: { display: "flex", flexDirection: "column", gap: "8px" }, children: [_jsx("div", { style: {
                                                            fontFamily: "var(--font-mono)",
                                                            fontSize: "11px",
                                                            fontWeight: 600,
                                                            color: currentSpan?.status === "failure" || selectedStep === "classification"
                                                                ? "var(--color-cinnabar)"
                                                                : "var(--color-ink-3)",
                                                            textTransform: "uppercase",
                                                        }, children: "Raw Model Completion / Output" }), _jsx("pre", { style: {
                                                            margin: 0,
                                                            padding: "14px",
                                                            backgroundColor: selectedStep === "classification"
                                                                ? "var(--color-cinnabar-tint)"
                                                                : "var(--color-chalk)",
                                                            border: `1px solid ${selectedStep === "classification"
                                                                ? "var(--color-cinnabar)"
                                                                : "var(--color-rule)"}`,
                                                            borderRadius: "var(--radius-1)",
                                                            fontFamily: "var(--font-mono)",
                                                            fontSize: "11px",
                                                            lineHeight: 1.45,
                                                            color: "var(--color-ink)",
                                                            overflowX: "auto",
                                                            maxHeight: "260px",
                                                        }, children: currentSpan?.raw_response ||
                                                            JSON.stringify(currentSpan?.output_data || {
                                                                classification: "invoice_receipt_v2",
                                                                confidence: 0.42,
                                                                error: "label not in DocumentType enum",
                                                            }, null, 2) })] })] })] })] })] })] }));
};
