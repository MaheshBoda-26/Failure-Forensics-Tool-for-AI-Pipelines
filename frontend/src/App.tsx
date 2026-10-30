import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api/client";
import type {
  AnalyticsSnapshot,
  Diagnosis,
  EvalRun,
  PipelineRunRequest,
  RuntimeStatus,
  Trace,
} from "./api/types";
import { AnalyticsView } from "./components/AnalyticsView";
import { GraphPage } from "./components/GraphPage";
import { Header, NavTab } from "./components/Header";
import { Overview } from "./components/Overview";
import { TraceExplorer } from "./components/TraceExplorer";
import { Empty, StatusPill } from "./components/ui";
import { X } from "lucide-react";
import { isFailedTrace } from "./lib/trace";

const HASHES: Record<NavTab, string> = {
  home: "#/overview",
  explorer: "#/traces",
  dag: "#/graph",
  analytics: "#/analytics",
};

function tabFromHash(): NavTab {
  const [segment] = window.location.hash.replace(/^#\/?/, "").split("/");
  if (segment === "traces") return "explorer";
  if (segment === "graph") return "dag";
  if (segment === "analytics") return "analytics";
  return "home";
}

export const App: React.FC = () => {
  const [activeTab, setActiveTabState] = useState<NavTab>(() => tabFromHash());
  const [traces, setTraces] = useState<Trace[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const selectedIdRef = useRef<string | null>(null);
  const [currentDiagnosis, setCurrentDiagnosis] = useState<Diagnosis | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsSnapshot | null>(null);
  const [evalRuns, setEvalRuns] = useState<EvalRun[]>([]);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [backendOnline, setBackendOnline] = useState<boolean>(true);
  const [isRunningDemo, setIsRunningDemo] = useState<boolean>(false);
  const [isRunningDocument, setIsRunningDocument] = useState<boolean>(false);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [openSignal, setOpenSignal] = useState(0);
  const [shortcutHelpOpen, setShortcutHelpOpen] = useState(false);
  const keyBufferRef = useRef<string>("");
  const keyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    selectedIdRef.current = selectedTraceId;
  }, [selectedTraceId]);

  const loadData = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const [traceResult, analyticsResult, evalResult, runtimeResult] = await Promise.allSettled([
        api.getTraces(100),
        api.getAnalytics(),
        api.getEvalRuns(),
        api.getRuntimeStatus(),
      ]);

      let nextSelected = selectedIdRef.current;

      if (traceResult.status === "fulfilled") {
        const list = traceResult.value;
        setTraces(list);
        setBackendOnline(true);
        if (list.length === 0) {
          nextSelected = null;
          setSelectedTraceId(null);
        } else {
          const withSpans = list.filter((trace) => (trace.spans?.length ?? 0) > 0);
          const preferred =
            withSpans.find((trace) => isFailedTrace(trace)) ??
            withSpans[0] ??
            list[0];
          if (!nextSelected || !list.some((trace) => trace.trace_id === nextSelected)) {
            nextSelected = preferred.trace_id;
            setSelectedTraceId(nextSelected);
          }
        }
        setLastUpdated(new Date());
      } else {
        setBackendOnline(false);
      }

      if (analyticsResult.status === "fulfilled") {
        setAnalytics(analyticsResult.value);
      }

      if (evalResult.status === "fulfilled") {
        setEvalRuns(evalResult.value);
      }

      if (runtimeResult.status === "fulfilled") {
        setRuntime(runtimeResult.value);
      }

      if (nextSelected) {
        try {
          setCurrentDiagnosis(await api.getDiagnosis(nextSelected));
        } catch {
          setCurrentDiagnosis(null);
        }
      } else {
        setCurrentDiagnosis(null);
      }
    } catch {
      setBackendOnline(false);
    } finally {
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    const onHash = () => {
      const [segment, id] = window.location.hash.replace(/^#\/?/, "").split("/");
      if (segment === "traces") {
        setActiveTabState("explorer");
        if (id) {
          selectedIdRef.current = id;
          setSelectedTraceId(id);
          setOpenSignal((value) => value + 1);
        }
      } else if (segment === "graph") {
        setActiveTabState("dag");
      } else if (segment === "analytics") {
        setActiveTabState("analytics");
      } else {
        setActiveTabState("home");
      }
    };
    window.addEventListener("hashchange", onHash);
    if (!window.location.hash) {
      window.history.replaceState(null, "", HASHES.home);
    }
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!selectedTraceId) {
      setCurrentDiagnosis(null);
      return;
    }
    let alive = true;
    api
      .getDiagnosis(selectedTraceId)
      .then((diagnosis) => {
        if (alive) setCurrentDiagnosis(diagnosis);
      })
      .catch(() => {
        if (alive) setCurrentDiagnosis(null);
      });
    return () => {
      alive = false;
    };
  }, [selectedTraceId]);

  const setActiveTab = (tab: NavTab) => {
    setActiveTabState(tab);
    if (window.location.hash !== HASHES[tab]) {
      window.location.hash = HASHES[tab];
    }
  };

  const selectedTrace = useMemo(
    () => traces.find((trace) => trace.trace_id === selectedTraceId) ?? traces[0] ?? null,
    [traces, selectedTraceId],
  );

  const selectTrace = (traceId: string) => {
    selectedIdRef.current = traceId;
    setSelectedTraceId(traceId);
    const next = `#/traces/${traceId}`;
    if (window.location.hash !== next) {
      window.history.replaceState(null, "", next);
    }
  };

  const handleInspect = (traceId: string) => {
    setOpenSignal((value) => value + 1);
    setActiveTabState("explorer");
    selectTrace(traceId);
  };

  const handleRunDemo = async () => {
    try {
      setIsRunningDemo(true);
      await api.runDemoPipeline();
      await loadData();
      setActiveTab("explorer");
    } catch (err: unknown) {
      alert(`Demo run error: ${(err as Error).message}`);
    } finally {
      setIsRunningDemo(false);
    }
  };

  const handleRunDocument = async (payload: PipelineRunRequest) => {
    try {
      setIsRunningDocument(true);
      const res = await api.runPipeline(payload);
      if (res.trace_id) {
        selectedIdRef.current = res.trace_id;
        setSelectedTraceId(res.trace_id);
      }
      await loadData();
      if (res.trace_id) {
        handleInspect(res.trace_id);
      } else {
        setActiveTab("explorer");
      }
    } finally {
      setIsRunningDocument(false);
    }
  };

  // Keyboard shortcuts:
  //   g o  → overview,  g t  → traces,  g a  → analytics
  //   /    → (no search component yet; opens help)
  //   ?    → help dialog
  //   Escape → close help dialog
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // Only handle shortcuts when not typing into an input.
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
        return;
      }

      if (event.key === "Escape") {
        setShortcutHelpOpen(false);
        return;
      }

      if (event.key === "?") {
        event.preventDefault();
        setShortcutHelpOpen(true);
        return;
      }

      if (event.key === "/") {
        event.preventDefault();
        setShortcutHelpOpen(true);
        return;
      }

      if (event.key === "g") {
        keyBufferRef.current = "g";
        keyTimerRef.current = window.setTimeout(() => {
          keyBufferRef.current = "";
        }, 800);
        return;
      }

      if (keyBufferRef.current === "g") {
        window.clearTimeout(keyTimerRef.current ?? undefined);
        keyBufferRef.current = "";
        if (event.key === "o") {
          event.preventDefault();
          setActiveTab("home");
        } else if (event.key === "t") {
          event.preventDefault();
          setActiveTab("explorer");
        } else if (event.key === "a") {
          event.preventDefault();
          setActiveTab("analytics");
        }
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setActiveTab]);

  return (
    <div className="app">
      <Header
        activeTab={activeTab}
        onTabChange={setActiveTab}
        backendOnline={backendOnline}
        runtime={runtime}
        onRunDemo={handleRunDemo}
        isRunningDemo={isRunningDemo}
        onRunDocument={handleRunDocument}
        isRunningDocument={isRunningDocument}
        onApiKeyChange={loadData}
        lastUpdated={lastUpdated}
      />

      <main className="main">
        <div className="sheet">
          {activeTab === "home" ? (
            <Overview
              traces={traces}
              analytics={analytics}
              evalRuns={evalRuns}
              onInspect={handleInspect}
              onOpenExplorer={() => setActiveTab("explorer")}
              onRunDemo={handleRunDemo}
              isRunningDemo={isRunningDemo}
              isRefreshing={isRefreshing}
              lastUpdated={lastUpdated}
              onRefresh={loadData}
            />
          ) : null}

          {activeTab === "explorer" ? (
            <TraceExplorer
              traces={traces}
              selectedTrace={selectedTrace}
              selectedTraceId={selectedTraceId}
              onSelect={selectTrace}
              diagnosis={currentDiagnosis}
              onRefreshTrace={loadData}
              isLoading={isRefreshing}
              onRunDemo={handleRunDemo}
              isRunningDemo={isRunningDemo}
              openSignal={openSignal}
            />
          ) : null}

          {activeTab === "dag" ? (
            <GraphPage
              trace={selectedTrace}
              diagnosis={currentDiagnosis}
              onOpenExplorer={() => setActiveTab("explorer")}
            />
          ) : null}

          {activeTab === "analytics" ? (
            <AnalyticsView
              analytics={analytics}
              evalRuns={evalRuns}
              onRefreshAnalytics={loadData}
            />
          ) : null}
        </div>
      </main>

      {shortcutHelpOpen ? (
        <div
          className="menu-sheet"
          role="dialog"
          aria-modal="true"
          aria-label="Keyboard shortcuts"
          onClick={() => setShortcutHelpOpen(false)}
        >
          <div className="menu-sheet__head">
            <h2 className="panel__title">Keyboard shortcuts</h2>
            <button
              className="icon-btn"
              onClick={() => setShortcutHelpOpen(false)}
              aria-label="Close help"
            >
              <X size={18} />
            </button>
          </div>
          <div className="menu-sheet__foot">
            <table className="table table--scores">
              <thead>
                <tr>
                  <th scope="col">Key</th>
                  <th scope="col">Action</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><code>g o</code></td>
                  <td>Go to overview</td>
                </tr>
                <tr>
                  <td><code>g t</code></td>
                  <td>Go to traces</td>
                </tr>
                <tr>
                  <td><code>g a</code></td>
                  <td>Go to analytics</td>
                </tr>
                <tr>
                  <td><code>/</code></td>
                  <td>Open help (search not yet implemented)</td>
                </tr>
                <tr>
                  <td><code>?</code></td>
                  <td>Open help dialog</td>
                </tr>
                <tr>
                  <td><code>Escape</code></td>
                  <td>Close dialogs / help</td>
                </tr>
              </tbody>
            </table>
            <p className="faint" style={{ marginTop: 12 }}>
              Shortcuts work app-wide when not typing into an input.
            </p>
          </div>
        </div>
      ) : null}

    </div>
  );
};
