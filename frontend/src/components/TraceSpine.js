import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { X } from "lucide-react";
export const TraceSpine = ({ trace, diagnosis, selectedStep, onSelectStep, }) => {
    const steps = [
        { name: "intake", index: 1, label: "01 intake", desc: "Document normalization · no LLM" },
        { name: "extraction", index: 2, label: "02 extraction", desc: "Entity extraction · grounded" },
        { name: "classification", index: 3, label: "03 classification", desc: "DocumentType enum validation" },
        { name: "summarization", index: 4, label: "04 summarization", desc: "Summary & synthesis" },
    ];
    const rootCauseStep = diagnosis?.root_cause_step?.toLowerCase() || "";
    const failingIndex = steps.findIndex((s) => s.name === rootCauseStep);
    return (_jsxs("div", { style: {
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            fontFamily: "var(--font-body)",
        }, children: [_jsx("div", { style: {
                    fontFamily: "var(--font-mono)",
                    fontSize: "11px",
                    fontWeight: 600,
                    letterSpacing: "var(--tracking-label)",
                    color: "var(--color-ink-3)",
                    textTransform: "uppercase",
                }, children: "EXECUTION STRATIGRAPHY (4 STAGES)" }), _jsx("div", { style: { display: "flex", flexDirection: "column", position: "relative" }, children: steps.map((step, idx) => {
                    const span = trace.spans.find((s) => s.step_name.toLowerCase() === step.name);
                    const isFailing = rootCauseStep === step.name;
                    const isDownstream = failingIndex !== -1 && idx > failingIndex;
                    const isPassed = !isFailing && !isDownstream;
                    const isSelected = selectedStep === step.name;
                    return (_jsxs("div", { onClick: () => onSelectStep(step.name), style: {
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
                        }, children: [_jsxs("div", { style: {
                                    display: "flex",
                                    flexDirection: "column",
                                    alignItems: "center",
                                    width: "16px",
                                    flexShrink: 0,
                                    marginTop: "2px",
                                }, children: [_jsx("div", { style: {
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
                                        }, children: isFailing && _jsx(X, { size: 9, color: "#FFFFFF", strokeWidth: 3 }) }), idx < steps.length - 1 && (_jsx("div", { style: {
                                            width: "2px",
                                            height: "46px",
                                            backgroundColor: isFailing
                                                ? "var(--color-cinnabar)"
                                                : isDownstream
                                                    ? "var(--color-rule)"
                                                    : "var(--color-rule)",
                                            borderLeft: isDownstream ? "2px dashed var(--color-rule-strong)" : "none",
                                            margin: "4px 0",
                                        } }))] }), _jsxs("div", { style: { flex: 1 }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" }, children: [_jsx("span", { style: {
                                                    fontFamily: "var(--font-mono)",
                                                    fontSize: "13px",
                                                    fontWeight: isSelected || isFailing ? 700 : 500,
                                                    color: isFailing ? "var(--color-cinnabar)" : "var(--color-ink)",
                                                }, children: step.label }), _jsx("span", { style: {
                                                    fontFamily: "var(--font-mono)",
                                                    fontSize: "11px",
                                                    color: isFailing ? "var(--color-cinnabar)" : "var(--color-ink-3)",
                                                }, children: span ? `${Math.round(span.latency_ms)}ms` : "—" })] }), _jsx("div", { style: {
                                            fontSize: "12px",
                                            color: isDownstream ? "var(--color-ink-3)" : "var(--color-ink-2)",
                                            marginTop: "2px",
                                        }, children: step.desc }), _jsx("div", { style: { marginTop: "4px" }, children: isFailing ? (_jsx("span", { style: {
                                                fontFamily: "var(--font-mono)",
                                                fontSize: "10px",
                                                fontWeight: 700,
                                                color: "var(--color-cinnabar)",
                                                letterSpacing: "0.04em",
                                            }, children: "\u2717 ROOT CAUSE \u00B7 CHECKS FAILED" })) : isDownstream ? (_jsx("span", { style: {
                                                fontFamily: "var(--font-mono)",
                                                fontSize: "10px",
                                                color: "var(--color-ink-3)",
                                            }, children: "\u26A0 DOWNSTREAM TAINTED" })) : (_jsxs("span", { style: {
                                                fontFamily: "var(--font-mono)",
                                                fontSize: "10px",
                                                color: "var(--color-copper)",
                                            }, children: ["\u2713 ", span?.checks?.length ?? 3, " checks passed"] })) })] })] }, step.name));
                }) })] }));
};
