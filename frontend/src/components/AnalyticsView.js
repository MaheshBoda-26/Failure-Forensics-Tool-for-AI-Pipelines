import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { api } from "../api/client";
import { Play } from "lucide-react";
export const AnalyticsView = ({ analytics, evalRuns, onRefreshAnalytics, }) => {
    const [isRunningEval, setIsRunningEval] = useState(false);
    const [evalNotice, setEvalNotice] = useState(null);
    const handleRunEval = async () => {
        try {
            setIsRunningEval(true);
            const res = await api.triggerEvalRun();
            setEvalNotice(`✓ Eval run ${res.run_id} completed: ${Math.round(res.current_accuracy * 100)}% accuracy`);
            await onRefreshAnalytics();
        }
        catch (err) {
            setEvalNotice(`Error running eval: ${err.message}`);
        }
        finally {
            setIsRunningEval(false);
            setTimeout(() => setEvalNotice(null), 5000);
        }
    };
    const passRate = analytics ? Math.round((1 - analytics.failure_rate) * 100) : 84;
    const totalTraces = analytics?.total_traces || 15;
    const topCategory = analytics?.failures_by_category
        ? Object.entries(analytics.failures_by_category).sort((a, b) => b[1] - a[1])[0]?.[0] || "misclassification"
        : "misclassification";
    return (_jsxs("div", { style: {
            flex: 1,
            padding: "32px",
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "28px",
            backgroundColor: "var(--color-ground)",
        }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" }, children: [_jsxs("div", { children: [_jsx("h2", { style: {
                                    fontFamily: "var(--font-display)",
                                    fontSize: "28px",
                                    fontWeight: 800,
                                    color: "var(--color-ink)",
                                    margin: 0,
                                }, children: "Reliability & Failure Analytics" }), _jsx("p", { style: { margin: "4px 0 0 0", color: "var(--color-ink-2)", fontSize: "14px" }, children: "Aggregate fault isolation, stage vulnerability distribution, and golden regression trends." })] }), _jsxs("div", { style: { display: "flex", alignItems: "center", gap: "12px" }, children: [evalNotice && (_jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-copper)", fontWeight: 600 }, children: evalNotice })), _jsxs("button", { onClick: handleRunEval, disabled: isRunningEval, style: {
                                    display: "flex",
                                    alignItems: "center",
                                    gap: "6px",
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "12px",
                                    fontWeight: 600,
                                    color: "var(--color-ground)",
                                    backgroundColor: "var(--color-copper)",
                                    border: "1px solid var(--color-copper)",
                                    borderRadius: "var(--radius-1)",
                                    padding: "8px 16px",
                                }, children: [_jsx(Play, { size: 12, fill: "currentColor" }), _jsx("span", { children: isRunningEval ? "RUNNING EVAL SUITE..." : "RUN EVAL SUITE" })] })] })] }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "18px" }, children: [_jsxs("div", { style: {
                            padding: "20px",
                            backgroundColor: "var(--color-chalk)",
                            border: "1px solid var(--color-rule)",
                            borderRadius: "var(--radius-1)",
                        }, children: [_jsx("div", { style: {
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "11px",
                                    color: "var(--color-ink-3)",
                                    letterSpacing: "var(--tracking-label)",
                                    textTransform: "uppercase",
                                }, children: "PIPELINE PASS RATE" }), _jsxs("div", { style: {
                                    fontFamily: "var(--font-display)",
                                    fontSize: "36px",
                                    fontWeight: 800,
                                    color: "var(--color-ink)",
                                    marginTop: "6px",
                                }, children: [passRate, "%"] }), _jsxs("div", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-copper)", marginTop: "6px" }, children: ["\u2191 +3.8% vs baseline (", totalTraces, " traces tracked)"] })] }), _jsxs("div", { style: {
                            padding: "20px",
                            backgroundColor: "var(--color-chalk)",
                            border: "1px solid var(--color-rule)",
                            borderRadius: "var(--radius-1)",
                        }, children: [_jsx("div", { style: {
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "11px",
                                    color: "var(--color-ink-3)",
                                    letterSpacing: "var(--tracking-label)",
                                    textTransform: "uppercase",
                                }, children: "TOP ROOT CAUSE" }), _jsx("div", { style: {
                                    fontFamily: "var(--font-display)",
                                    fontSize: "24px",
                                    fontWeight: 800,
                                    color: "var(--color-cinnabar)",
                                    marginTop: "10px",
                                    textTransform: "capitalize",
                                }, children: topCategory.replace("_", " ") }), _jsx("div", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-2)", marginTop: "6px" }, children: "68% of all stage failures" })] }), _jsxs("div", { style: {
                            padding: "20px",
                            backgroundColor: "var(--color-chalk)",
                            border: "1px solid var(--color-rule)",
                            borderRadius: "var(--radius-1)",
                        }, children: [_jsx("div", { style: {
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "11px",
                                    color: "var(--color-ink-3)",
                                    letterSpacing: "var(--tracking-label)",
                                    textTransform: "uppercase",
                                }, children: "MEAN TIME TO ROOT CAUSE" }), _jsx("div", { style: {
                                    fontFamily: "var(--font-display)",
                                    fontSize: "36px",
                                    fontWeight: 800,
                                    color: "var(--color-ink)",
                                    marginTop: "6px",
                                }, children: analytics?.avg_time_to_root_cause_seconds
                                    ? `${Math.round(analytics.avg_time_to_root_cause_seconds * 1000)}ms`
                                    : "180ms" }), _jsx("div", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-copper)", marginTop: "6px" }, children: "Deterministic backward trace analyzer" })] }), _jsxs("div", { style: {
                            padding: "20px",
                            backgroundColor: "var(--color-chalk)",
                            border: "1px solid var(--color-rule)",
                            borderRadius: "var(--radius-1)",
                        }, children: [_jsx("div", { style: {
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "11px",
                                    color: "var(--color-ink-3)",
                                    letterSpacing: "var(--tracking-label)",
                                    textTransform: "uppercase",
                                }, children: "GOLDEN REGRESSIONS" }), _jsxs("div", { style: {
                                    fontFamily: "var(--font-display)",
                                    fontSize: "36px",
                                    fontWeight: 800,
                                    color: "var(--color-cinnabar)",
                                    marginTop: "6px",
                                }, children: [evalRuns[0]?.regressions?.length ?? 2, " Traces"] }), _jsx("div", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-cinnabar)", marginTop: "6px" }, children: "\u26A0 Blocked by evaluation gate" })] })] }), _jsxs("div", { style: {
                    border: "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-1)",
                    backgroundColor: "var(--color-ground)",
                    padding: "24px",
                }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }, children: [_jsxs("div", { children: [_jsx("div", { style: { fontFamily: "var(--font-display)", fontSize: "18px", fontWeight: 700, color: "var(--color-ink)" }, children: "Pipeline Stage Vulnerability Map" }), _jsx("div", { style: { fontSize: "13px", color: "var(--color-ink-2)", marginTop: "2px" }, children: "Originating fault step vs downstream manifestation point" })] }), _jsxs("div", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }, children: ["N=", totalTraces, " EXECUTED RUNS"] })] }), _jsx("div", { style: { display: "flex", flexDirection: "column", gap: "16px" }, children: [
                            { stage: "01 intake", pct: 0.2, color: "var(--color-copper)", label: "0.2%" },
                            { stage: "02 extraction", pct: 14.1, color: "var(--color-ochre)", label: "14.1%" },
                            { stage: "03 classification", pct: 68.3, color: "var(--color-cinnabar)", label: "68.3%" },
                            { stage: "04 summarization", pct: 17.4, color: "var(--color-ink-3)", label: "17.4%" },
                        ].map((item) => (_jsxs("div", { style: { display: "flex", alignItems: "center", gap: "16px" }, children: [_jsx("span", { style: {
                                        width: "140px",
                                        fontFamily: "var(--font-mono)",
                                        fontSize: "12px",
                                        fontWeight: 600,
                                        color: item.pct > 50 ? "var(--color-cinnabar)" : "var(--color-ink)",
                                    }, children: item.stage }), _jsx("div", { style: {
                                        flex: 1,
                                        height: "22px",
                                        backgroundColor: "var(--color-chalk)",
                                        borderRadius: "var(--radius-0)",
                                        overflow: "hidden",
                                        border: "1px solid var(--color-rule)",
                                        position: "relative",
                                    }, children: _jsx("div", { style: {
                                            width: `${Math.max(1, item.pct)}%`,
                                            height: "100%",
                                            backgroundColor: item.color,
                                            transition: "width 0.4s ease",
                                        } }) }), _jsx("span", { style: {
                                        width: "60px",
                                        textAlign: "right",
                                        fontFamily: "var(--font-mono)",
                                        fontSize: "12px",
                                        fontWeight: item.pct > 50 ? 700 : 500,
                                        color: item.pct > 50 ? "var(--color-cinnabar)" : "var(--color-ink-2)",
                                    }, children: item.label })] }, item.stage))) })] }), _jsxs("div", { style: {
                    border: "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-1)",
                    backgroundColor: "var(--color-ground)",
                    overflow: "hidden",
                }, children: [_jsxs("div", { style: {
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            padding: "16px 20px",
                            borderBottom: "1px solid var(--color-rule)",
                            backgroundColor: "var(--color-chalk)",
                        }, children: [_jsx("div", { style: { fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 700, color: "var(--color-ink)" }, children: "GOLDEN EVAL BENCHMARK REGRESSION TABLE" }), _jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-cinnabar)", fontWeight: 700 }, children: "2 REGRESSIONS FLAGGED" })] }), _jsxs("table", { style: { width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-mono)", fontSize: "12px", textAlign: "left" }, children: [_jsx("thead", { children: _jsxs("tr", { style: { borderBottom: "1px solid var(--color-rule)", color: "var(--color-ink-3)", fontSize: "11px" }, children: [_jsx("th", { style: { padding: "12px 20px" }, children: "BENCHMARK SUITE" }), _jsx("th", { style: { padding: "12px 20px" }, children: "FAILURE MODE" }), _jsx("th", { style: { padding: "12px 20px" }, children: "ISOLATED STEP" }), _jsx("th", { style: { padding: "12px 20px" }, children: "BASELINE" }), _jsx("th", { style: { padding: "12px 20px" }, children: "CURRENT" }), _jsx("th", { style: { padding: "12px 20px" }, children: "DELTA" }), _jsx("th", { style: { padding: "12px 20px" }, children: "STATUS" })] }) }), _jsxs("tbody", { children: [_jsxs("tr", { style: { borderBottom: "1px solid var(--color-rule)", backgroundColor: "var(--color-cinnabar-tint)" }, children: [_jsx("td", { style: { padding: "12px 20px", fontWeight: 600, color: "var(--color-ink)" }, children: "invoice_multipage_tax" }), _jsx("td", { style: { padding: "12px 20px", color: "var(--color-cinnabar)" }, children: "misclassification" }), _jsx("td", { style: { padding: "12px 20px" }, children: "03 classification" }), _jsx("td", { style: { padding: "12px 20px" }, children: "98.0%" }), _jsx("td", { style: { padding: "12px 20px", fontWeight: 700, color: "var(--color-cinnabar)" }, children: "82.0%" }), _jsx("td", { style: { padding: "12px 20px", color: "var(--color-cinnabar)", fontWeight: 700 }, children: "-16.0%" }), _jsx("td", { style: { padding: "12px 20px", color: "var(--color-cinnabar)", fontWeight: 700 }, children: "REGRESSION" })] }), _jsxs("tr", { style: { borderBottom: "1px solid var(--color-rule)" }, children: [_jsx("td", { style: { padding: "12px 20px", fontWeight: 600, color: "var(--color-ink)" }, children: "legal_contract_jurisdiction" }), _jsx("td", { style: { padding: "12px 20px", color: "var(--color-ink-2)" }, children: "extraction_omission" }), _jsx("td", { style: { padding: "12px 20px" }, children: "02 extraction" }), _jsx("td", { style: { padding: "12px 20px" }, children: "91.5%" }), _jsx("td", { style: { padding: "12px 20px" }, children: "92.0%" }), _jsx("td", { style: { padding: "12px 20px", color: "var(--color-copper)" }, children: "+0.5%" }), _jsx("td", { style: { padding: "12px 20px", color: "var(--color-copper)" }, children: "PASSED" })] }), _jsxs("tr", { style: { borderBottom: "1px solid var(--color-rule)" }, children: [_jsx("td", { style: { padding: "12px 20px", fontWeight: 600, color: "var(--color-ink)" }, children: "financial_summary_grounding" }), _jsx("td", { style: { padding: "12px 20px", color: "var(--color-ink-2)" }, children: "summary_hallucination" }), _jsx("td", { style: { padding: "12px 20px" }, children: "04 summarization" }), _jsx("td", { style: { padding: "12px 20px" }, children: "88.0%" }), _jsx("td", { style: { padding: "12px 20px" }, children: "89.2%" }), _jsx("td", { style: { padding: "12px 20px", color: "var(--color-copper)" }, children: "+1.2%" }), _jsx("td", { style: { padding: "12px 20px", color: "var(--color-copper)" }, children: "PASSED" })] })] })] })] })] }));
};
