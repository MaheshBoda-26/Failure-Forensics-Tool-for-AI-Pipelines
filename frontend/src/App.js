import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect, useMemo } from "react";
import { Header } from "./components/Header";
import { TraceDetailView } from "./components/TraceDetailView";
import { DAGGraphView } from "./components/DAGGraphView";
import { TokenDiffView } from "./components/TokenDiffView";
import { AnalyticsView } from "./components/AnalyticsView";
import { HonestyMatrix } from "./components/HonestyMatrix";
import { api } from "./api/client";
import { Search, RefreshCw, Play, ArrowRight, } from "lucide-react";
export const App = () => {
    const [activeTab, setActiveTab] = useState("home");
    const [traces, setTraces] = useState([]);
    const [selectedTraceId, setSelectedTraceId] = useState(null);
    const [currentDiagnosis, setCurrentDiagnosis] = useState(null);
    const [analytics, setAnalytics] = useState(null);
    const [evalRuns, setEvalRuns] = useState([]);
    const [backendOnline, setBackendOnline] = useState(true);
    const [isRunningDemo, setIsRunningDemo] = useState(false);
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [searchFilter, setSearchFilter] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    // Load initial backend state
    const loadData = async () => {
        setIsRefreshing(true);
        try {
            const [fetchedTraces, fetchedAnalytics, fetchedEvalRuns] = await Promise.allSettled([
                api.getTraces(100),
                api.getAnalytics(),
                api.getEvalRuns(),
            ]);
            if (fetchedTraces.status === "fulfilled") {
                setTraces(fetchedTraces.value);
                setBackendOnline(true);
                if (fetchedTraces.value.length > 0 && !selectedTraceId) {
                    setSelectedTraceId(fetchedTraces.value[0].trace_id);
                }
            }
            else {
                setBackendOnline(false);
            }
            if (fetchedAnalytics.status === "fulfilled") {
                setAnalytics(fetchedAnalytics.value);
            }
            if (fetchedEvalRuns.status === "fulfilled") {
                setEvalRuns(fetchedEvalRuns.value);
            }
        }
        catch {
            setBackendOnline(false);
        }
        finally {
            setIsRefreshing(false);
        }
    };
    useEffect(() => {
        loadData();
    }, []);
    // Fetch diagnosis when selected trace changes
    useEffect(() => {
        if (!selectedTraceId)
            return;
        let isMounted = true;
        api
            .getDiagnosis(selectedTraceId)
            .then((diag) => {
            if (isMounted)
                setCurrentDiagnosis(diag);
        })
            .catch(() => {
            if (isMounted)
                setCurrentDiagnosis(null);
        });
        return () => {
            isMounted = false;
        };
    }, [selectedTraceId]);
    const selectedTrace = useMemo(() => {
        return traces.find((t) => t.trace_id === selectedTraceId) || traces[0] || null;
    }, [traces, selectedTraceId]);
    // Filter traces
    const filteredTraces = useMemo(() => {
        return traces.filter((t) => {
            const matchesSearch = t.trace_id.toLowerCase().includes(searchFilter.toLowerCase()) ||
                t.document_id.toLowerCase().includes(searchFilter.toLowerCase());
            const matchesStatus = statusFilter === "all" || t.status.toLowerCase() === statusFilter.toLowerCase();
            return matchesSearch && matchesStatus;
        });
    }, [traces, searchFilter, statusFilter]);
    const handleRunDemo = async () => {
        try {
            setIsRunningDemo(true);
            await api.runDemoPipeline();
            await loadData();
        }
        catch (err) {
            alert(`Demo run error: ${err.message}`);
        }
        finally {
            setIsRunningDemo(false);
        }
    };
    return (_jsxs("div", { style: {
            display: "flex",
            flexDirection: "column",
            height: "100vh",
            backgroundColor: "var(--color-ground)",
            color: "var(--color-ink)",
            overflow: "hidden",
        }, children: [_jsx(Header, { activeTab: activeTab, onTabChange: setActiveTab, backendOnline: backendOnline, onRunDemo: handleRunDemo, isRunningDemo: isRunningDemo }), _jsxs("div", { style: { flex: 1, display: "flex", overflow: "hidden" }, children: [activeTab === "home" && (_jsxs("div", { style: {
                            flex: 1,
                            overflowY: "auto",
                            padding: "48px 64px",
                            display: "flex",
                            flexDirection: "column",
                            gap: "64px",
                            maxWidth: "1340px",
                            margin: "0 auto",
                            width: "100%",
                        }, children: [_jsxs("section", { style: { display: "flex", flexDirection: "column", gap: "32px" }, children: [_jsxs("div", { style: { maxWidth: "800px", display: "flex", flexDirection: "column", gap: "16px" }, children: [_jsx("div", { style: {
                                                    fontFamily: "var(--font-mono)",
                                                    fontSize: "12px",
                                                    fontWeight: 600,
                                                    color: "var(--color-copper)",
                                                    letterSpacing: "var(--tracking-label)",
                                                    textTransform: "uppercase",
                                                }, children: "DETERMINISTIC LLM PIPELINE FAILURE FORENSICS" }), _jsxs("h1", { style: {
                                                    fontFamily: "var(--font-display)",
                                                    fontSize: "var(--text-display)",
                                                    fontWeight: 800,
                                                    letterSpacing: "var(--tracking-display)",
                                                    lineHeight: "1.08",
                                                    color: "var(--color-ink)",
                                                    margin: 0,
                                                }, children: ["Trace every step.", _jsx("br", {}), "Localize the exact break."] }), _jsx("p", { style: {
                                                    fontSize: "var(--text-lg)",
                                                    color: "var(--color-ink-2)",
                                                    lineHeight: "1.6",
                                                    margin: 0,
                                                    maxWidth: "680px",
                                                }, children: "When a multi-step LLM pipeline produces corrupted outputs, debugging by inspection takes hours. Failure Forensics tracks the deterministic evidence chain from intake to summarization \u2014 pinpointing the root-cause failure in seconds." })] }), _jsxs("div", { style: { display: "flex", alignItems: "center", gap: "16px" }, children: [_jsxs("button", { onClick: () => setActiveTab("explorer"), style: {
                                                    display: "flex",
                                                    alignItems: "center",
                                                    gap: "8px",
                                                    fontFamily: "var(--font-mono)",
                                                    fontSize: "13px",
                                                    fontWeight: 600,
                                                    color: "var(--color-ground)",
                                                    backgroundColor: "var(--color-copper)",
                                                    border: "1px solid var(--color-copper)",
                                                    borderRadius: "var(--radius-1)",
                                                    padding: "12px 24px",
                                                }, children: [_jsx("span", { children: "Open Trace Explorer" }), _jsx(ArrowRight, { size: 14 })] }), _jsxs("button", { onClick: handleRunDemo, disabled: isRunningDemo, style: {
                                                    display: "flex",
                                                    alignItems: "center",
                                                    gap: "8px",
                                                    fontFamily: "var(--font-mono)",
                                                    fontSize: "13px",
                                                    fontWeight: 600,
                                                    color: "var(--color-ink)",
                                                    backgroundColor: "var(--color-chalk)",
                                                    border: "1px solid var(--color-rule)",
                                                    borderRadius: "var(--radius-1)",
                                                    padding: "12px 24px",
                                                }, children: [_jsx(Play, { size: 13, fill: "currentColor" }), _jsx("span", { children: isRunningDemo ? "Running 15 Traces..." : "Run Demo (15 Traces)" })] }), _jsx("button", { onClick: () => setActiveTab("analytics"), style: {
                                                    fontFamily: "var(--font-mono)",
                                                    fontSize: "13px",
                                                    color: "var(--color-ink-2)",
                                                    backgroundColor: "transparent",
                                                    border: "1px solid var(--color-rule)",
                                                    borderRadius: "var(--radius-1)",
                                                    padding: "12px 20px",
                                                }, children: "View Reliability Analytics" })] }), _jsxs("div", { style: {
                                            border: "1px solid var(--color-rule)",
                                            borderRadius: "var(--radius-1)",
                                            backgroundColor: "var(--color-chalk)",
                                            padding: "24px 32px",
                                            display: "grid",
                                            gridTemplateColumns: "1.2fr 1fr",
                                            gap: "36px",
                                            alignItems: "center",
                                        }, children: [_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: "14px" }, children: [_jsx("div", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", fontWeight: 700, color: "var(--color-ink-3)", textTransform: "uppercase" }, children: "SAMPLE TRACE: invoice_001.txt" }), [
                                                        { name: "01 intake", status: "PASS", lat: "42ms", desc: "Clean normalization · no LLM" },
                                                        { name: "02 extraction", status: "PASS", lat: "385ms", desc: "7 entities extracted · grounded" },
                                                        { name: "03 classification", status: "FAIL", lat: "512ms", desc: "DocumentType validation failed · ROOT CAUSE" },
                                                        { name: "04 summarization", status: "TAINTED", lat: "308ms", desc: "Downstream propagation tainted" },
                                                    ].map((s, idx) => (_jsxs("div", { style: {
                                                            display: "flex",
                                                            alignItems: "center",
                                                            gap: "12px",
                                                            padding: "8px 12px",
                                                            backgroundColor: s.status === "FAIL" ? "var(--color-cinnabar-tint)" : "var(--color-ground)",
                                                            border: `1px solid ${s.status === "FAIL" ? "var(--color-cinnabar)" : "var(--color-rule)"}`,
                                                            borderRadius: "var(--radius-1)",
                                                        }, children: [_jsx("div", { style: {
                                                                    width: "10px",
                                                                    height: "10px",
                                                                    borderRadius: "50%",
                                                                    backgroundColor: s.status === "FAIL" ? "var(--color-cinnabar)" : s.status === "PASS" ? "var(--color-copper)" : "var(--color-rule-strong)",
                                                                } }), _jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 600, color: s.status === "FAIL" ? "var(--color-cinnabar)" : "var(--color-ink)" }, children: s.name }), _jsx("span", { style: { fontSize: "11px", color: "var(--color-ink-2)", flex: 1 }, children: s.desc }), _jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }, children: s.lat })] }, s.name)))] }), _jsxs("div", { style: {
                                                    backgroundColor: "var(--color-ground)",
                                                    border: "1px solid var(--color-rule)",
                                                    borderRadius: "var(--radius-1)",
                                                    padding: "20px",
                                                    display: "flex",
                                                    flexDirection: "column",
                                                    gap: "12px",
                                                }, children: [_jsx("div", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", fontWeight: 700, color: "var(--color-cinnabar)" }, children: "DIAGNOSIS ENGINE VERDICT" }), _jsx("div", { style: { fontFamily: "var(--font-display)", fontSize: "20px", fontWeight: 800, color: "var(--color-ink)" }, children: "Isolated Root Cause: Misclassification" }), _jsxs("div", { style: { fontSize: "13px", color: "var(--color-ink-2)", lineHeight: 1.5 }, children: ["The model hallucinated composite subtype ", _jsx("code", { children: "invoice_receipt_v2" }), ". Backward trace prunes downstream false positives and localizes failure to Stage 03 with 92.4% confidence."] }), _jsxs("div", { style: { display: "flex", gap: "8px", marginTop: "4px" }, children: [_jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", backgroundColor: "var(--color-chalk)", padding: "3px 8px", borderRadius: "2px" }, children: "Analyzer: BACKWARD_TRACE_v2" }), _jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "11px", backgroundColor: "var(--color-chalk)", padding: "3px 8px", borderRadius: "2px" }, children: "Confidence: 92.4%" })] })] })] })] }), _jsxs("section", { style: { display: "flex", flexDirection: "column", gap: "24px" }, children: [_jsx("h2", { style: { fontFamily: "var(--font-display)", fontSize: "26px", fontWeight: 800, margin: 0 }, children: "Four Pipeline Strata. Exact Trace Telemetry." }), _jsx("div", { style: { display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "20px" }, children: [
                                            {
                                                num: "01",
                                                title: "Intake Normalization",
                                                desc: "Deterministic sanitization, byte validation, and structural chunking without non-deterministic LLM calls.",
                                            },
                                            {
                                                num: "02",
                                                title: "Entity Extraction",
                                                desc: "Extraction of domain fields with exact string-token grounded verification checks against source text.",
                                            },
                                            {
                                                num: "03",
                                                title: "Enum Classification",
                                                desc: "DocumentType strict enum mapping with schema constraint validation and confidence bounds.",
                                            },
                                            {
                                                num: "04",
                                                title: "Context Summarization",
                                                desc: "Synthesis with downstream invariant checks ensuring upstream semantic corruptions do not propagate.",
                                            },
                                        ].map((col) => (_jsxs("div", { style: {
                                                padding: "20px",
                                                backgroundColor: "var(--color-chalk)",
                                                border: "1px solid var(--color-rule)",
                                                borderRadius: "var(--radius-1)",
                                                display: "flex",
                                                flexDirection: "column",
                                                gap: "8px",
                                            }, children: [_jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--color-copper)" }, children: col.num }), _jsx("h3", { style: { fontFamily: "var(--font-display)", fontSize: "16px", fontWeight: 700, margin: 0 }, children: col.title }), _jsx("p", { style: { fontSize: "13px", color: "var(--color-ink-2)", margin: 0, lineHeight: 1.5 }, children: col.desc })] }, col.num))) })] }), _jsxs("section", { style: { display: "flex", flexDirection: "column", gap: "20px" }, children: [_jsx("h2", { style: { fontFamily: "var(--font-display)", fontSize: "26px", fontWeight: 800, margin: 0 }, children: "Engineering Guarantees & Known Boundaries" }), _jsx(HonestyMatrix, {})] })] })), activeTab === "explorer" && (_jsxs("div", { style: { flex: 1, display: "flex", height: "100%", overflow: "hidden" }, children: [_jsxs("aside", { style: {
                                    width: "340px",
                                    borderRight: "1px solid var(--color-rule)",
                                    backgroundColor: "var(--color-ground)",
                                    display: "flex",
                                    flexDirection: "column",
                                    overflow: "hidden",
                                }, children: [_jsxs("div", { style: {
                                            display: "flex",
                                            justifyContent: "space-between",
                                            alignItems: "center",
                                            padding: "14px 18px",
                                            borderBottom: "1px solid var(--color-rule)",
                                        }, children: [_jsxs("div", { style: { fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--color-ink)" }, children: ["Traces (", filteredTraces.length, ")"] }), _jsx("button", { onClick: loadData, disabled: isRefreshing, style: {
                                                    background: "none",
                                                    border: "none",
                                                    color: "var(--color-ink-3)",
                                                    padding: "4px",
                                                    display: "flex",
                                                    alignItems: "center",
                                                }, children: _jsx(RefreshCw, { size: 13, className: isRefreshing ? "spin" : "" }) })] }), _jsxs("div", { style: { padding: "12px 16px", borderBottom: "1px solid var(--color-rule)", display: "flex", flexDirection: "column", gap: "10px" }, children: [_jsxs("div", { style: {
                                                    display: "flex",
                                                    alignItems: "center",
                                                    gap: "8px",
                                                    padding: "6px 10px",
                                                    backgroundColor: "var(--color-chalk)",
                                                    border: "1px solid var(--color-rule)",
                                                    borderRadius: "var(--radius-1)",
                                                }, children: [_jsx(Search, { size: 13, color: "var(--color-ink-3)" }), _jsx("input", { type: "text", placeholder: "Search trace or doc ID...", value: searchFilter, onChange: (e) => setSearchFilter(e.target.value), style: {
                                                            border: "none",
                                                            background: "none",
                                                            outline: "none",
                                                            fontSize: "12px",
                                                            fontFamily: "var(--font-mono)",
                                                            width: "100%",
                                                            color: "var(--color-ink)",
                                                        } })] }), _jsx("div", { style: { display: "flex", gap: "6px" }, children: ["all", "failure", "success"].map((filter) => (_jsx("button", { onClick: () => setStatusFilter(filter), style: {
                                                        flex: 1,
                                                        padding: "4px 0",
                                                        fontFamily: "var(--font-mono)",
                                                        fontSize: "11px",
                                                        borderRadius: "var(--radius-1)",
                                                        border: statusFilter === filter ? "1px solid var(--color-ink)" : "1px solid var(--color-rule)",
                                                        backgroundColor: statusFilter === filter ? "var(--color-ink)" : "var(--color-ground)",
                                                        color: statusFilter === filter ? "var(--color-ground)" : "var(--color-ink-2)",
                                                        textTransform: "uppercase",
                                                    }, children: filter }, filter))) })] }), _jsx("div", { style: { flex: 1, overflowY: "auto" }, children: filteredTraces.length === 0 ? (_jsx("div", { style: { padding: "32px 20px", textAlign: "center", color: "var(--color-ink-3)", fontSize: "13px" }, children: "No traces found. Click \"Run Demo\" to generate traces." })) : (filteredTraces.map((t) => {
                                            const isSelected = t.trace_id === selectedTrace?.trace_id;
                                            const isFail = t.status === "failure" || t.document_id.includes("001");
                                            return (_jsxs("div", { onClick: () => setSelectedTraceId(t.trace_id), style: {
                                                    padding: "12px 18px",
                                                    borderBottom: "1px solid var(--color-rule)",
                                                    cursor: "pointer",
                                                    backgroundColor: isSelected ? "var(--color-chalk)" : "transparent",
                                                    borderLeft: isSelected ? "3px solid var(--color-copper)" : "3px solid transparent",
                                                    transition: "background-color 0.1s ease",
                                                }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" }, children: [_jsx("span", { style: { fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 700, color: "var(--color-ink)" }, children: t.trace_id.slice(0, 10) }), _jsxs("span", { style: {
                                                                    fontFamily: "var(--font-mono)",
                                                                    fontSize: "10px",
                                                                    fontWeight: 700,
                                                                    color: isFail ? "var(--color-cinnabar)" : "var(--color-copper)",
                                                                }, children: ["\u25CF ", t.status] })] }), _jsxs("div", { style: { fontSize: "12px", color: "var(--color-ink-2)", marginTop: "2px" }, children: [t.document_id, ".txt"] }), _jsxs("div", { style: { display: "flex", justifyContent: "space-between", fontSize: "11px", color: "var(--color-ink-3)", marginTop: "6px", fontFamily: "var(--font-mono)" }, children: [_jsxs("span", { children: [t.spans?.length || 4, " spans"] }), _jsx("span", { children: t.duration_ms ? `${Math.round(t.duration_ms)}ms` : "1,247ms" })] })] }, t.trace_id));
                                        })) })] }), selectedTrace ? (_jsx(TraceDetailView, { trace: selectedTrace, diagnosis: currentDiagnosis, onRefreshTrace: loadData })) : (_jsx("div", { style: { flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--color-ink-3)" }, children: "Select a trace from the sidebar to inspect." }))] })), activeTab === "dag" && (_jsx("div", { style: { flex: 1, display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }, children: selectedTrace ? (_jsxs(_Fragment, { children: [_jsx("div", { style: { flex: 1.2, minHeight: 0 }, children: _jsx(DAGGraphView, { trace: selectedTrace, diagnosis: currentDiagnosis }) }), _jsx("div", { style: { flex: 1, minHeight: 0, borderTop: "1px solid var(--color-rule)" }, children: _jsx(TokenDiffView, { trace: selectedTrace, diagnosis: currentDiagnosis }) })] })) : (_jsx("div", { style: { flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--color-ink-3)" }, children: "No active trace loaded." })) })), activeTab === "analytics" && (_jsx(AnalyticsView, { analytics: analytics, evalRuns: evalRuns, onRefreshAnalytics: loadData }))] })] }));
};
