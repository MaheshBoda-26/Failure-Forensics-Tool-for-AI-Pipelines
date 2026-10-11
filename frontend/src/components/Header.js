import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Play, CheckCircle2, AlertCircle } from "lucide-react";
export const Header = ({ activeTab, onTabChange, backendOnline, onRunDemo, isRunningDemo, }) => {
    return (_jsxs("header", { style: {
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "16px 32px",
            borderBottom: "1px solid var(--color-rule)",
            backgroundColor: "var(--color-ground)",
            position: "sticky",
            top: 0,
            zIndex: 100,
        }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: "36px" }, children: [_jsxs("div", { onClick: () => onTabChange("home"), style: {
                            cursor: "pointer",
                            display: "flex",
                            alignItems: "center",
                            gap: "10px",
                        }, children: [_jsx("div", { style: {
                                    width: "28px",
                                    height: "28px",
                                    border: "2px solid var(--color-copper)",
                                    borderRadius: "var(--radius-1)",
                                    display: "flex",
                                    alignItems: "center",
                                    justifyContent: "center",
                                    fontWeight: 800,
                                    fontFamily: "var(--font-display)",
                                    fontSize: "14px",
                                    color: "var(--color-copper)",
                                }, children: "FF" }), _jsx("span", { style: {
                                    fontFamily: "var(--font-display)",
                                    fontSize: "20px",
                                    fontWeight: 800,
                                    letterSpacing: "var(--tracking-tight)",
                                    color: "var(--color-ink)",
                                }, children: "Failure Forensics" })] }), _jsx("nav", { style: { display: "flex", gap: "24px", fontFamily: "var(--font-mono)", fontSize: "12px" }, children: [
                            { id: "home", label: "OVERVIEW" },
                            { id: "explorer", label: "TRACE EXPLORER" },
                            { id: "dag", label: "DAG GRAPH & DIFF" },
                            { id: "analytics", label: "EVAL & ANALYTICS" },
                        ].map((tab) => {
                            const isActive = activeTab === tab.id;
                            return (_jsx("button", { onClick: () => onTabChange(tab.id), style: {
                                    background: "none",
                                    border: "none",
                                    padding: "6px 0",
                                    cursor: "pointer",
                                    color: isActive ? "var(--color-ink)" : "var(--color-ink-3)",
                                    fontWeight: isActive ? 600 : 400,
                                    borderBottom: isActive ? "2px solid var(--color-copper)" : "2px solid transparent",
                                    letterSpacing: "0.04em",
                                }, children: tab.label }, tab.id));
                        }) })] }), _jsxs("div", { style: { display: "flex", alignItems: "center", gap: "16px" }, children: [_jsxs("div", { style: {
                            display: "flex",
                            alignItems: "center",
                            gap: "6px",
                            fontFamily: "var(--font-mono)",
                            fontSize: "11px",
                            color: backendOnline ? "var(--color-copper)" : "var(--color-cinnabar)",
                            padding: "4px 8px",
                            backgroundColor: backendOnline ? "var(--color-copper-tint)" : "var(--color-cinnabar-tint)",
                            borderRadius: "var(--radius-1)",
                            border: `1px solid ${backendOnline ? "var(--color-copper)" : "var(--color-cinnabar)"}`,
                        }, children: [backendOnline ? (_jsx(CheckCircle2, { size: 12, strokeWidth: 2.5 })) : (_jsx(AlertCircle, { size: 12, strokeWidth: 2.5 })), _jsx("span", { children: backendOnline ? "API CONNECTED" : "OFFLINE / LOCAL" })] }), _jsxs("button", { onClick: onRunDemo, disabled: isRunningDemo, style: {
                            display: "flex",
                            alignItems: "center",
                            gap: "6px",
                            fontFamily: "var(--font-mono)",
                            fontSize: "12px",
                            fontWeight: 600,
                            color: "var(--color-ground)",
                            backgroundColor: isRunningDemo ? "var(--color-ink-3)" : "var(--color-copper)",
                            border: `1px solid ${isRunningDemo ? "var(--color-ink-3)" : "var(--color-copper)"}`,
                            borderRadius: "var(--radius-1)",
                            padding: "7px 14px",
                            cursor: isRunningDemo ? "wait" : "pointer",
                        }, children: [_jsx(Play, { size: 12, fill: "currentColor" }), _jsx("span", { children: isRunningDemo ? "RUNNING PIPELINE..." : "RUN DEMO" })] })] })] }));
};
