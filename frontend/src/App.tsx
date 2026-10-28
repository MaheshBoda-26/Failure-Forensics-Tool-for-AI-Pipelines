import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api/client";
import type { AnalyticsSnapshot, Diagnosis, EvalRun, Trace } from "./api/types";
import { AnalyticsView } from "./components/AnalyticsView";
import { GraphPage } from "./components/GraphPage";
import { Header, NavTab } from "./components/Header";
import { Landing } from "./components/Landing";
import { Overview } from "./components/Overview";
import { TraceExplorer } from "./components/TraceExplorer";
import { isFailedTrace } from "./lib/trace";

const HASHES: Record<NavTab, string> = {
  home: "#/overview",
  explorer: "#/traces",
  dag: "#/graph",
  analytics: "#/analytics",
};

type Route = NavTab | "landing";

/**
 * `#/` is the landing page, `#/traces/<trace_id>` deep-links one trace. Keeping
 * the selection in the URL is what makes a mobile detail view survivable: a
 * reload or a shared link lands on the same trace.
 */
function parseHash(): { route: Route; traceId: string | null } {
  const [segment, id] = window.location.hash.replace(/^#\/?/, "").split("/");
  if (segment === "overview") return { route: "home", traceId: null };
  if (segment === "traces") return { route: "explorer", traceId: id || null };
  if (segment === "graph") return { route: "dag", traceId: null };
  if (segment === "analytics") return { route: "analytics", traceId: null };
  return { route: "landing", traceId: null };
}

/** The workspace keeps its state across tab changes; the landing page does not. */
const Workspace: React.FC = () => {
  const initial = parseHash();
  const [activeTab, setActiveTabState] = useState<NavTab>(
    initial.route === "landing" ? "home" : initial.route,
  );
  const [traces, setTraces] = useState<Trace[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(initial.traceId);
  const selectedIdRef = useRef<string | null>(null);
  const [currentDiagnosis, setCurrentDiagnosis] = useState<Diagnosis | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsSnapshot | null>(null);
  const [evalRuns, setEvalRuns] = useState<EvalRun[]>([]);
  const [backendOnline, setBackendOnline] = useState<boolean>(true);
  const [isRunningDemo, setIsRunningDemo] = useState<boolean>(false);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [openSignal, setOpenSignal] = useState(initial.traceId ? 1 : 0);

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
          // Prefer a failing trace that actually has spans: it is the product's
          // job, and a fixture row with no spans is a poor first thing to open.
          const withSpans = list.filter((trace) => (trace.spans?.length ?? 0) > 0);
          const preferred =
            withSpans.find((trace) => isFailedTrace(trace)) ?? withSpans[0] ?? list[0];
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

      // Re-fetch the diagnosis for the selected trace. Without this, confirming
      // or overriding a diagnosis left the panel showing the stale verdict.
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
      const next = parseHash();
      if (next.route === "landing") return;
      setActiveTabState(next.route);
      if (next.traceId) {
        selectedIdRef.current = next.traceId;
        setSelectedTraceId(next.traceId);
        setOpenSignal((value) => value + 1);
      }
    };
    window.addEventListener("hashchange", onHash);
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
    // `replaceState`, not a push: clicking through a list should not bury the
    // previous screen under thirty history entries.
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

  return (
    <div className="app">
      <Header
        activeTab={activeTab}
        onTabChange={setActiveTab}
        backendOnline={backendOnline}
        onRunDemo={handleRunDemo}
        isRunningDemo={isRunningDemo}
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

export const App: React.FC = () => {
  const [route, setRoute] = useState<Route>(() => parseHash().route);

  useEffect(() => {
    const onHash = () => setRoute(parseHash().route);
    window.addEventListener("hashchange", onHash);
    if (!window.location.hash) {
      window.history.replaceState(null, "", "#/");
    }
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (route === "landing") return <Landing />;
  return <Workspace />;
};
