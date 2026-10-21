import React, { useState, useEffect, useMemo } from "react";
import { Header, NavTab } from "./components/Header";
import { TraceDetailView } from "./components/TraceDetailView";
import { DAGGraphView } from "./components/DAGGraphView";
import { TokenDiffView } from "./components/TokenDiffView";
import { AnalyticsView } from "./components/AnalyticsView";
import { HonestyMatrix } from "./components/HonestyMatrix";
import { Trace, Diagnosis, AnalyticsSnapshot, EvalRun } from "./api/types";
import { api } from "./api/client";
import {
  Search,
  RefreshCw,
  Play,
  ArrowRight,
  Shield,
  Layers,
  Activity,
  FileText,
  AlertTriangle,
  CheckCircle2,
} from "lucide-react";

/** A trace failed when the run errored *or* its diagnosis blames a step. */
function isFailedTrace(trace: Trace): boolean {
  if (trace.status === "failure") return true;
  return !!trace.root_cause_step && trace.root_cause_step !== "none";
}

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<NavTab>("home");
  const [traces, setTraces] = useState<Trace[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [currentDiagnosis, setCurrentDiagnosis] = useState<Diagnosis | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsSnapshot | null>(null);
  const [evalRuns, setEvalRuns] = useState<EvalRun[]>([]);
  const [backendOnline, setBackendOnline] = useState<boolean>(true);
  const [isRunningDemo, setIsRunningDemo] = useState<boolean>(false);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [searchFilter, setSearchFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<"all" | "failure" | "success">("all");

  // Load initial backend state
  const loadData = async () => {
    setIsRefreshing(true);
    try {
      const [fetchedTraces, fetchedAnalytics, fetchedEvalRuns] = await Promise.allSettled([
        api.getTraces(100),
        api.getAnalytics(),
        api.getEvalRuns(),
      ]);

      let resolvedTraceId = selectedTraceId;

      if (fetchedTraces.status === "fulfilled") {
        setTraces(fetchedTraces.value);
        setBackendOnline(true);
        if (fetchedTraces.value.length > 0 && !selectedTraceId) {
          resolvedTraceId = fetchedTraces.value[0].trace_id;
          setSelectedTraceId(resolvedTraceId);
        }
      } else {
        setBackendOnline(false);
      }

      if (fetchedAnalytics.status === "fulfilled") {
        setAnalytics(fetchedAnalytics.value);
      }

      if (fetchedEvalRuns.status === "fulfilled") {
        setEvalRuns(fetchedEvalRuns.value);
      }

      // Re-fetch the diagnosis for the selected trace. `loadData` used to fetch
      // only traces/analytics/eval runs, so after confirming or overriding a
      // diagnosis the panel kept showing the stale verdict (the trace id had
      // not changed, so the selected-trace effect never re-ran).
      if (resolvedTraceId) {
        try {
          setCurrentDiagnosis(await api.getDiagnosis(resolvedTraceId));
        } catch {
          setCurrentDiagnosis(null);
        }
      }
    } catch {
      setBackendOnline(false);
    } finally {
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // Fetch diagnosis when selected trace changes
  useEffect(() => {
    if (!selectedTraceId) return;
    let isMounted = true;
    api
      .getDiagnosis(selectedTraceId)
      .then((diag) => {
        if (isMounted) setCurrentDiagnosis(diag);
      })
      .catch(() => {
        if (isMounted) setCurrentDiagnosis(null);
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
      const matchesSearch =
        t.trace_id.toLowerCase().includes(searchFilter.toLowerCase()) ||
        t.document_id.toLowerCase().includes(searchFilter.toLowerCase());
      // A seeded failure completes its run without raising, so `status` is
      // "success" while the diagnosis blames a step. Both signals count.
      const isFailure = isFailedTrace(t);
      const matchesStatus =
        statusFilter === "all" || (statusFilter === "failure" ? isFailure : !isFailure);
      return matchesSearch && matchesStatus;
    });
  }, [traces, searchFilter, statusFilter]);

  const handleRunDemo = async () => {
    try {
      setIsRunningDemo(true);
      await api.runDemoPipeline();
      await loadData();
    } catch (err: unknown) {
      alert(`Demo run error: ${(err as Error).message}`);
    } finally {
      setIsRunningDemo(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        backgroundColor: "var(--color-ground)",
        color: "var(--color-ink)",
        overflow: "hidden",
      }}
    >
      <Header
        activeTab={activeTab}
        onTabChange={setActiveTab}
        backendOnline={backendOnline}
        onRunDemo={handleRunDemo}
        isRunningDemo={isRunningDemo}
        onApiKeyChange={loadData}
      />

      {/* Main Tab Content */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* OVERVIEW / MARKETING HOME TAB */}
        {activeTab === "home" && (
          <div
            style={{
              flex: 1,
              overflowY: "auto",
              padding: "48px 64px",
              display: "flex",
              flexDirection: "column",
              gap: "64px",
              maxWidth: "1340px",
              margin: "0 auto",
              width: "100%",
            }}
          >
            {/* Hero Section */}
            <section style={{ display: "flex", flexDirection: "column", gap: "32px" }}>
              <div style={{ maxWidth: "800px", display: "flex", flexDirection: "column", gap: "16px" }}>
                <div
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "12px",
                    fontWeight: 600,
                    color: "var(--color-copper)",
                    letterSpacing: "var(--tracking-label)",
                    textTransform: "uppercase",
                  }}
                >
                  DETERMINISTIC LLM PIPELINE FAILURE FORENSICS
                </div>
                <h1
                  style={{
                    fontFamily: "var(--font-display)",
                    fontSize: "var(--text-display)",
                    fontWeight: 800,
                    letterSpacing: "var(--tracking-display)",
                    lineHeight: "1.08",
                    color: "var(--color-ink)",
                    margin: 0,
                  }}
                >
                  Trace every step.
                  <br />
                  Localize the exact break.
                </h1>
                <p
                  style={{
                    fontSize: "var(--text-lg)",
                    color: "var(--color-ink-2)",
                    lineHeight: "1.6",
                    margin: 0,
                    maxWidth: "680px",
                  }}
                >
                  When a multi-step LLM pipeline produces corrupted outputs, debugging by inspection takes
                  hours. Failure Forensics tracks the deterministic evidence chain from intake to summarization —
                  pinpointing the root-cause failure in seconds.
                </p>
              </div>

              {/* Action Buttons */}
              <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
                <button
                  onClick={() => setActiveTab("explorer")}
                  style={{
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
                  }}
                >
                  <span>Open Trace Explorer</span>
                  <ArrowRight size={14} />
                </button>
                <button
                  onClick={handleRunDemo}
                  disabled={isRunningDemo}
                  style={{
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
                  }}
                >
                  <Play size={13} fill="currentColor" />
                  <span>{isRunningDemo ? "Running 15 Traces..." : "Run Demo (15 Traces)"}</span>
                </button>
                <button
                  onClick={() => setActiveTab("analytics")}
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "13px",
                    color: "var(--color-ink-2)",
                    backgroundColor: "transparent",
                    border: "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-1)",
                    padding: "12px 20px",
                  }}
                >
                  View Reliability Analytics
                </button>
              </div>

              {/* Interactive Hero Preview Card */}
              <div
                style={{
                  border: "1px solid var(--color-rule)",
                  borderRadius: "var(--radius-1)",
                  backgroundColor: "var(--color-chalk)",
                  padding: "24px 32px",
                  display: "grid",
                  gridTemplateColumns: "1.2fr 1fr",
                  gap: "36px",
                  alignItems: "center",
                }}
              >
                {/* Stratigraphic Mini-Spine */}
                <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", fontWeight: 700, color: "var(--color-ink-3)", textTransform: "uppercase" }}>
                    SAMPLE TRACE: invoice_001.txt
                  </div>
                  {[
                    { name: "01 intake", status: "PASS", lat: "42ms", desc: "Clean normalization · no LLM" },
                    { name: "02 extraction", status: "PASS", lat: "385ms", desc: "7 entities extracted · grounded" },
                    { name: "03 classification", status: "FAIL", lat: "512ms", desc: "DocumentType validation failed · ROOT CAUSE" },
                    { name: "04 summarization", status: "TAINTED", lat: "308ms", desc: "Downstream propagation tainted" },
                  ].map((s, idx) => (
                    <div
                      key={s.name}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "12px",
                        padding: "8px 12px",
                        backgroundColor: s.status === "FAIL" ? "var(--color-cinnabar-tint)" : "var(--color-ground)",
                        border: `1px solid ${s.status === "FAIL" ? "var(--color-cinnabar)" : "var(--color-rule)"}`,
                        borderRadius: "var(--radius-1)",
                      }}
                    >
                      <div
                        style={{
                          width: "10px",
                          height: "10px",
                          borderRadius: "50%",
                          backgroundColor: s.status === "FAIL" ? "var(--color-cinnabar)" : s.status === "PASS" ? "var(--color-copper)" : "var(--color-rule-strong)",
                        }}
                      />
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 600, color: s.status === "FAIL" ? "var(--color-cinnabar)" : "var(--color-ink)" }}>
                        {s.name}
                      </span>
                      <span style={{ fontSize: "11px", color: "var(--color-ink-2)", flex: 1 }}>{s.desc}</span>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-ink-3)" }}>{s.lat}</span>
                    </div>
                  ))}
                </div>

                {/* Root Cause Banner */}
                <div
                  style={{
                    backgroundColor: "var(--color-ground)",
                    border: "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-1)",
                    padding: "20px",
                    display: "flex",
                    flexDirection: "column",
                    gap: "12px",
                  }}
                >
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", fontWeight: 700, color: "var(--color-cinnabar)" }}>
                    DIAGNOSIS ENGINE VERDICT
                  </div>
                  <div style={{ fontFamily: "var(--font-display)", fontSize: "20px", fontWeight: 800, color: "var(--color-ink)" }}>
                    Isolated Root Cause: Misclassification
                  </div>
                  <div style={{ fontSize: "13px", color: "var(--color-ink-2)", lineHeight: 1.5 }}>
                    The model hallucinated composite subtype <code>invoice_receipt_v2</code>. Backward trace
                    prunes downstream false positives and localizes failure to Stage 03 with 92.4% confidence.
                  </div>
                  <div style={{ display: "flex", gap: "8px", marginTop: "4px" }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", backgroundColor: "var(--color-chalk)", padding: "3px 8px", borderRadius: "2px" }}>
                      Analyzer: BACKWARD_TRACE_v2
                    </span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", backgroundColor: "var(--color-chalk)", padding: "3px 8px", borderRadius: "2px" }}>
                      Confidence: 92.4%
                    </span>
                  </div>
                </div>
              </div>
            </section>

            {/* Stratigraphy Pipeline Explainer */}
            <section style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
              <h2 style={{ fontFamily: "var(--font-display)", fontSize: "26px", fontWeight: 800, margin: 0 }}>
                Four Pipeline Strata. Exact Trace Telemetry.
              </h2>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "20px" }}>
                {[
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
                ].map((col) => (
                  <div
                    key={col.num}
                    style={{
                      padding: "20px",
                      backgroundColor: "var(--color-chalk)",
                      border: "1px solid var(--color-rule)",
                      borderRadius: "var(--radius-1)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "8px",
                    }}
                  >
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--color-copper)" }}>
                      {col.num}
                    </span>
                    <h3 style={{ fontFamily: "var(--font-display)", fontSize: "16px", fontWeight: 700, margin: 0 }}>
                      {col.title}
                    </h3>
                    <p style={{ fontSize: "13px", color: "var(--color-ink-2)", margin: 0, lineHeight: 1.5 }}>
                      {col.desc}
                    </p>
                  </div>
                ))}
              </div>
            </section>

            {/* Technical Honesty Matrix */}
            <section style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
              <h2 style={{ fontFamily: "var(--font-display)", fontSize: "26px", fontWeight: 800, margin: 0 }}>
                Engineering Guarantees & Known Boundaries
              </h2>
              <HonestyMatrix />
            </section>
          </div>
        )}

        {/* TRACE EXPLORER TAB */}
        {activeTab === "explorer" && (
          <div style={{ flex: 1, display: "flex", height: "100%", overflow: "hidden" }}>
            {/* Sidebar: Trace List (340px) */}
            <aside
              style={{
                width: "340px",
                borderRight: "1px solid var(--color-rule)",
                backgroundColor: "var(--color-ground)",
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
              }}
            >
              {/* Sidebar Header */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "14px 18px",
                  borderBottom: "1px solid var(--color-rule)",
                }}
              >
                <div style={{ fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--color-ink)" }}>
                  Traces ({filteredTraces.length})
                </div>
                <button
                  onClick={loadData}
                  disabled={isRefreshing}
                  style={{
                    background: "none",
                    border: "none",
                    color: "var(--color-ink-3)",
                    padding: "4px",
                    display: "flex",
                    alignItems: "center",
                  }}
                >
                  <RefreshCw size={13} className={isRefreshing ? "spin" : ""} />
                </button>
              </div>

              {/* Filter / Search Bar */}
              <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--color-rule)", display: "flex", flexDirection: "column", gap: "10px" }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    padding: "6px 10px",
                    backgroundColor: "var(--color-chalk)",
                    border: "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-1)",
                  }}
                >
                  <Search size={13} color="var(--color-ink-3)" />
                  <input
                    type="text"
                    placeholder="Search trace or doc ID..."
                    value={searchFilter}
                    onChange={(e) => setSearchFilter(e.target.value)}
                    style={{
                      border: "none",
                      background: "none",
                      outline: "none",
                      fontSize: "12px",
                      fontFamily: "var(--font-mono)",
                      width: "100%",
                      color: "var(--color-ink)",
                    }}
                  />
                </div>

                <div style={{ display: "flex", gap: "6px" }}>
                  {(["all", "failure", "success"] as const).map((filter) => (
                    <button
                      key={filter}
                      onClick={() => setStatusFilter(filter)}
                      style={{
                        flex: 1,
                        padding: "4px 0",
                        fontFamily: "var(--font-mono)",
                        fontSize: "11px",
                        borderRadius: "var(--radius-1)",
                        border: statusFilter === filter ? "1px solid var(--color-ink)" : "1px solid var(--color-rule)",
                        backgroundColor: statusFilter === filter ? "var(--color-ink)" : "var(--color-ground)",
                        color: statusFilter === filter ? "var(--color-ground)" : "var(--color-ink-2)",
                        textTransform: "uppercase",
                      }}
                    >
                      {filter}
                    </button>
                  ))}
                </div>
              </div>

              {/* Trace List Items */}
              <div style={{ flex: 1, overflowY: "auto" }}>
                {filteredTraces.length === 0 ? (
                  <div style={{ padding: "32px 20px", textAlign: "center", color: "var(--color-ink-3)", fontSize: "13px" }}>
                    No traces found. Click "Run Demo" to generate traces.
                  </div>
                ) : (
                  filteredTraces.map((t) => {
                    const isSelected = t.trace_id === selectedTrace?.trace_id;
                    const isFail = isFailedTrace(t);
                    return (
                      <div
                        key={t.trace_id}
                        onClick={() => setSelectedTraceId(t.trace_id)}
                        style={{
                          padding: "12px 18px",
                          borderBottom: "1px solid var(--color-rule)",
                          cursor: "pointer",
                          backgroundColor: isSelected ? "var(--color-chalk)" : "transparent",
                          borderLeft: isSelected ? "3px solid var(--color-copper)" : "3px solid transparent",
                          transition: "background-color 0.1s ease",
                        }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                          <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", fontWeight: 700, color: "var(--color-ink)" }}>
                            {t.trace_id.slice(0, 10)}
                          </span>
                          <span
                            style={{
                              fontFamily: "var(--font-mono)",
                              fontSize: "10px",
                              fontWeight: 700,
                              color: isFail ? "var(--color-cinnabar)" : "var(--color-copper)",
                            }}
                          >
                            ● {isFail ? "failure" : t.status}
                          </span>
                        </div>
                        <div style={{ fontSize: "12px", color: "var(--color-ink-2)", marginTop: "2px" }}>
                          {t.document_id}.txt
                        </div>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", color: "var(--color-ink-3)", marginTop: "6px", fontFamily: "var(--font-mono)" }}>
                          <span>{t.spans?.length ?? 0} spans</span>
                          <span>{t.total_latency_ms != null ? `${Math.round(t.total_latency_ms)}ms` : "—"}</span>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </aside>

            {/* Right Pane: Trace Detail View */}
            {selectedTrace ? (
              <TraceDetailView
                trace={selectedTrace}
                diagnosis={currentDiagnosis}
                onRefreshTrace={loadData}
              />
            ) : (
              <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--color-ink-3)" }}>
                Select a trace from the sidebar to inspect.
              </div>
            )}
          </div>
        )}

        {/* DAG GRAPH & TOKEN DIFF TAB */}
        {activeTab === "dag" && (
          <div style={{ flex: 1, display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
            {selectedTrace ? (
              <>
                <div style={{ flex: 1.2, minHeight: 0 }}>
                  <DAGGraphView trace={selectedTrace} diagnosis={currentDiagnosis} />
                </div>
                <div style={{ flex: 1, minHeight: 0, borderTop: "1px solid var(--color-rule)" }}>
                  <TokenDiffView trace={selectedTrace} diagnosis={currentDiagnosis} />
                </div>
              </>
            ) : (
              <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--color-ink-3)" }}>
                No active trace loaded.
              </div>
            )}
          </div>
        )}

        {/* EVAL & ANALYTICS TAB */}
        {activeTab === "analytics" && (
          <AnalyticsView
            analytics={analytics}
            evalRuns={evalRuns}
            onRefreshAnalytics={loadData}
          />
        )}
      </div>
    </div>
  );
};
