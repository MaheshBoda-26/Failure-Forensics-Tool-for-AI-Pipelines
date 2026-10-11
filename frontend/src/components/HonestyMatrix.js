import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export const HonestyMatrix = () => {
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
    return (_jsxs("div", { style: {
            border: "1px solid var(--color-rule)",
            borderRadius: "var(--radius-1)",
            backgroundColor: "var(--color-ground)",
            overflow: "hidden",
        }, children: [_jsxs("div", { style: {
                    padding: "16px 24px",
                    borderBottom: "1px solid var(--color-rule)",
                    backgroundColor: "var(--color-chalk)",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                }, children: [_jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 700, color: "var(--color-ink)" }, children: "TECHNICAL BOUNDARY & HONESTY MATRIX" }), _jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }, children: "SPECIFICATION INTEGRITY" })] }), _jsxs("table", { style: { width: "100%", borderCollapse: "collapse", fontSize: "13px", textAlign: "left" }, children: [_jsx("thead", { children: _jsxs("tr", { style: { borderBottom: "1px solid var(--color-rule)", color: "var(--color-ink-3)", fontFamily: "var(--font-mono)", fontSize: "11px" }, children: [_jsx("th", { style: { padding: "12px 24px", width: "220px" }, children: "CAPABILITY" }), _jsx("th", { style: { padding: "12px 24px" }, children: "DETERMINISTIC GUARANTEE" }), _jsx("th", { style: { padding: "12px 24px" }, children: "V0 BOUNDARY / LIMITATION" })] }) }), _jsx("tbody", { children: guarantees.map((g, idx) => (_jsxs("tr", { style: { borderBottom: idx < guarantees.length - 1 ? "1px solid var(--color-rule)" : "none" }, children: [_jsx("td", { style: { padding: "14px 24px", fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 600, color: "var(--color-ink)" }, children: g.feature }), _jsx("td", { style: { padding: "14px 24px", color: "var(--color-ink)", lineHeight: 1.5 }, children: _jsxs("div", { style: { display: "flex", gap: "8px", alignItems: "flex-start" }, children: [_jsx("span", { style: { color: "var(--color-copper)", fontWeight: 700, fontFamily: "var(--font-mono)" }, children: "\u2713" }), _jsx("span", { children: g.guaranteed })] }) }), _jsx("td", { style: { padding: "14px 24px", color: "var(--color-ink-2)", lineHeight: 1.5 }, children: _jsxs("div", { style: { display: "flex", gap: "8px", alignItems: "flex-start" }, children: [_jsx("span", { style: { color: "var(--color-ink-3)", fontWeight: 700, fontFamily: "var(--font-mono)" }, children: "\u2014" }), _jsx("span", { children: g.limit })] }) })] }, idx))) })] })] }));
};
