import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import React, { useState } from "react";
import { ZoomIn, ZoomOut, Maximize2 } from "lucide-react";
export const DAGGraphView = ({ trace, diagnosis }) => {
    const [selectedNode, setSelectedNode] = useState("classification");
    const [zoomLevel, setZoomLevel] = useState(1);
    const nodes = [
        {
            id: "intake",
            stage: "STAGE 01",
            name: "intake",
            desc: "Document normalization · UTF-8 validation",
            tokens: "0 tokens",
            duration: "42ms",
            status: "PASSED",
            checks: "3/3 checks passed",
        },
        {
            id: "extraction",
            stage: "STAGE 02",
            name: "extraction",
            desc: "Grounded entity extraction (7 entities)",
            tokens: "820 tokens",
            duration: "385ms",
            status: "PASSED",
            checks: "7/7 checks passed",
        },
        {
            id: "classification",
            stage: "STAGE 03 · ROOT CAUSE",
            name: "classification",
            desc: "DocumentType strict enum mapping",
            tokens: "410 tokens",
            duration: "512ms",
            status: "FAILED",
            checks: "2 checks failed (label_enum, label_grounding)",
        },
        {
            id: "summarization",
            stage: "STAGE 04",
            name: "summarization",
            desc: "Context synthesis & downstream consumption",
            tokens: "612 tokens",
            duration: "308ms",
            status: "TAINTED",
            checks: "Corrupted input propagation check failed",
        },
    ];
    return (_jsxs("div", { style: {
            display: "flex",
            flexDirection: "column",
            height: "100%",
            backgroundColor: "var(--color-ground)",
            overflow: "hidden",
        }, children: [_jsxs("div", { style: {
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "12px 28px",
                    borderBottom: "1px solid var(--color-rule)",
                    backgroundColor: "var(--color-ground)",
                }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: "16px" }, children: [_jsxs("span", { style: { fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--color-ink)" }, children: ["DAG Execution Graph \u00B7 ", trace.trace_id] }), _jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }, children: "Deterministic Dependency Topology" })] }), _jsxs("div", { style: { display: "flex", alignItems: "center", gap: "8px" }, children: [_jsx("button", { onClick: () => setZoomLevel((z) => Math.max(0.7, z - 0.1)), style: {
                                    padding: "6px 10px",
                                    backgroundColor: "var(--color-chalk)",
                                    border: "1px solid var(--color-rule)",
                                    borderRadius: "var(--radius-1)",
                                    color: "var(--color-ink-2)",
                                }, children: _jsx(ZoomOut, { size: 13 }) }), _jsx("button", { onClick: () => setZoomLevel((z) => Math.min(1.4, z + 0.1)), style: {
                                    padding: "6px 10px",
                                    backgroundColor: "var(--color-chalk)",
                                    border: "1px solid var(--color-rule)",
                                    borderRadius: "var(--radius-1)",
                                    color: "var(--color-ink-2)",
                                }, children: _jsx(ZoomIn, { size: 13 }) }), _jsxs("button", { onClick: () => setZoomLevel(1), style: {
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
                                }, children: [_jsx(Maximize2, { size: 12 }), _jsx("span", { children: "Fit View" })] })] })] }), _jsx("div", { style: {
                    flex: 1,
                    backgroundColor: "var(--color-chalk)",
                    position: "relative",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    padding: "48px",
                    overflow: "auto",
                }, children: _jsx("div", { style: {
                        display: "flex",
                        alignItems: "center",
                        gap: "36px",
                        transform: `scale(${zoomLevel})`,
                        transition: "transform 0.2s ease",
                    }, children: nodes.map((node, idx) => {
                        const isSelected = selectedNode === node.id;
                        const isFailed = node.status === "FAILED";
                        const isTainted = node.status === "TAINTED";
                        return (_jsxs(React.Fragment, { children: [_jsxs("div", { onClick: () => setSelectedNode(node.id), style: {
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
                                    }, children: [_jsxs("div", { style: {
                                                display: "flex",
                                                justifyContent: "space-between",
                                                alignItems: "center",
                                                padding: "8px 12px",
                                                backgroundColor: isFailed
                                                    ? "var(--color-cinnabar-tint)"
                                                    : "var(--color-chalk)",
                                                borderBottom: `1px solid ${isFailed ? "var(--color-cinnabar)" : "var(--color-rule)"}`,
                                            }, children: [_jsx("span", { style: {
                                                        fontFamily: "var(--font-mono)",
                                                        fontSize: "10px",
                                                        fontWeight: 700,
                                                        color: isFailed ? "var(--color-cinnabar)" : "var(--color-ink)",
                                                    }, children: node.stage }), _jsx("span", { style: {
                                                        fontFamily: "var(--font-mono)",
                                                        fontSize: "10px",
                                                        fontWeight: 700,
                                                        color: isFailed
                                                            ? "var(--color-cinnabar)"
                                                            : isTainted
                                                                ? "var(--color-ochre)"
                                                                : "var(--color-copper)",
                                                    }, children: node.status })] }), _jsxs("div", { style: { padding: "14px" }, children: [_jsx("div", { style: {
                                                        fontFamily: "var(--font-mono)",
                                                        fontSize: "14px",
                                                        fontWeight: 700,
                                                        color: isFailed ? "var(--color-cinnabar)" : "var(--color-ink)",
                                                    }, children: node.name }), _jsx("div", { style: { fontSize: "12px", color: "var(--color-ink-2)", marginTop: "4px" }, children: node.desc }), _jsxs("div", { style: {
                                                        fontFamily: "var(--font-mono)",
                                                        fontSize: "11px",
                                                        color: isFailed ? "var(--color-cinnabar)" : "var(--color-ink-3)",
                                                        marginTop: "10px",
                                                        display: "flex",
                                                        justifyContent: "space-between",
                                                    }, children: [_jsx("span", { children: node.duration }), _jsx("span", { children: node.tokens })] })] })] }), idx < nodes.length - 1 && (_jsxs("div", { style: {
                                        display: "flex",
                                        alignItems: "center",
                                        position: "relative",
                                        width: "36px",
                                    }, children: [_jsx("div", { style: {
                                                height: "2px",
                                                width: "100%",
                                                backgroundColor: idx === 1 ? "var(--color-cinnabar)" : "var(--color-rule-strong)",
                                                borderTop: idx === 2 ? "2px dashed var(--color-rule-strong)" : "none",
                                            } }), _jsx("div", { style: {
                                                position: "absolute",
                                                right: 0,
                                                width: 0,
                                                height: 0,
                                                borderTop: "4px solid transparent",
                                                borderBottom: "4px solid transparent",
                                                borderLeft: `6px solid ${idx === 1 ? "var(--color-cinnabar)" : "var(--color-rule-strong)"}`,
                                            } })] }))] }, node.id));
                    }) }) }), _jsxs("div", { style: {
                    borderTop: "1px solid var(--color-rule)",
                    backgroundColor: "var(--color-ground)",
                    padding: "16px 28px",
                    maxHeight: "180px",
                    overflowY: "auto",
                }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }, children: [_jsxs("span", { style: { fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 700 }, children: ["NODE INSPECTION: ", selectedNode.toUpperCase()] }), _jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }, children: "CLICK A NODE ABOVE TO VIEW ITS METADATA" })] }), _jsx("p", { style: { margin: 0, fontSize: "13px", color: "var(--color-ink-2)" }, children: selectedNode === "classification"
                            ? "Stage 03 executed Anthropic Claude 3.5 Haiku. The model returned 'invoice_receipt_v2', which violated the strict enum domain. Backward trace analysis isolated this node with 92.4% confidence as the single root cause."
                            : selectedNode === "extraction"
                                ? "Stage 02 extracted 7 key entities (vendor, invoice_number, total_amount, tax_id) from the intake buffer. All 7 grounding checks passed cleanly."
                                : selectedNode === "intake"
                                    ? "Stage 01 sanitized input text, validated UTF-8 encoding, and computed character count. Zero LLM calls made."
                                    : "Stage 04 generated document summary. Because input classification failed at Stage 03, the generated summary contains tainted semantics." })] })] }));
};
