import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api/client";
import type {
  AnalyticsSnapshot,
  Diagnosis,
  EvalRun,
  PipelineRunRequest,
  Trace,
} from "./api/types";
import { AnalyticsView } from "./components/AnalyticsView";
import { GraphPage } from "./components/GraphPage";
import { Header, NavTab } from "./components/Header";
import { Overview } from "./components/Overview";
import { TraceExplorer } from "./components/TraceExplorer";
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
  const [backendOnline, setBackendOnline] = useState<boolean>(true);
  const [isRunningDemo, setIsRunningDemo] = useState<boolean>(false);
  const [isRunningDocument, setIsRunningDocument] = useState<boolean>(false);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [openSignal, setOpenSignal] = useState(0);

  useEffect(() => {
    selectedIdRef.current = selectedTraceId;
  }, [selectedTraceId]);

  const loadData = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const [traceResult, analyticsResult, evalResult] = await Promise.allSettled([
        api.getTraces(100),
        api.getAnalytics(),
        api.getEvalRuns(),
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

  return (
    <div className="app">
      <Header
        activeTab={activeTab}
        onTabChange={setActiveTab}
        backendOnline={backendOnline}
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
    </div>
  );
};
