import React, { useEffect, useMemo, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import type { Diagnosis, Trace } from "../api/types";
import { formatMs, isFailedTrace, relativeTime, shortId } from "../lib/trace";
import { TraceDetailView } from "./TraceDetailView";
import { Empty, StatusPill } from "./ui";

interface TraceExplorerProps {
  traces: Trace[];
  selectedTrace: Trace | null;
  selectedTraceId: string | null;
  onSelect: (traceId: string) => void;
  diagnosis: Diagnosis | null;
  onRefreshTrace: () => Promise<void>;
  isLoading: boolean;
  onRunDemo: () => Promise<void>;
  isRunningDemo: boolean;
  /** Incremented when another screen asks to open a specific trace. */
  openSignal: number;
}

type StatusFilter = "all" | "failure" | "success";

export const TraceExplorer: React.FC<TraceExplorerProps> = ({
  traces,
  selectedTrace,
  selectedTraceId,
  onSelect,
  diagnosis,
  onRefreshTrace,
  isLoading,
  onRunDemo,
  isRunningDemo,
  openSignal,
}) => {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [mobileDetail, setMobileDetail] = useState(false);

  useEffect(() => {
    if (openSignal > 0) setMobileDetail(true);
  }, [openSignal]);

  const counts = useMemo(() => {
    const failures = traces.filter(isFailedTrace).length;
    return { all: traces.length, failures, passing: traces.length - failures };
  }, [traces]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return traces.filter((trace) => {
      const matchesQuery =
        needle.length === 0 ||
        trace.trace_id.toLowerCase().includes(needle) ||
        trace.document_id.toLowerCase().includes(needle);
      const failed = isFailedTrace(trace);
      const matchesFilter =
        filter === "all" || (filter === "failure" ? failed : !failed);
      return matchesQuery && matchesFilter;
    });
  }, [traces, query, filter]);

  const select = (traceId: string) => {
    onSelect(traceId);
    setMobileDetail(true);
  };

  return (
    <div className={`explorer${mobileDetail ? " is-detail" : ""}`}>
      <div className="explorer__head">
        <div>
          <h1 className="page-title">Traces</h1>
          <p className="page-sub">
            {traces.length === 0
              ? "No traces loaded."
              : `Showing ${filtered.length} of ${traces.length} traces.`}
          </p>
        </div>
        <div className="page-actions">
          <button
            className="icon-btn"
            onClick={onRefreshTrace}
            disabled={isLoading}
            aria-label="Refresh traces"
          >
            <RefreshCw size={15} className={isLoading ? "spin" : undefined} />
          </button>
        </div>
      </div>

      <div className="explorer__body">
        <div className="explorer__list">
          <div className="list-tools">
            <div className="search">
              <Search size={13} />
              <input
                className="input"
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search trace or document"
                aria-label="Search traces"
              />
            </div>

            <div className="tabs" role="group" aria-label="Filter traces">
              {(
                [
                  { id: "all", label: "All", count: counts.all },
                  { id: "failure", label: "Failures", count: counts.failures },
                  { id: "success", label: "Passing", count: counts.passing },
                ] as const
              ).map((tab) => (
                <button
                  key={tab.id}
                  className="tab"
                  onClick={() => setFilter(tab.id)}
                  aria-pressed={filter === tab.id}
                >
                  {tab.label}
                  <span className="tab__count">{tab.count}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="list-scroll">
            {filtered.length === 0 ? (
              <div className="panel__body">
                <Empty
                  action={
                    traces.length === 0 ? (
                      <button
                        className="btn btn--sm"
                        onClick={onRunDemo}
                        disabled={isRunningDemo}
                      >
                        {isRunningDemo ? "Running demo" : "Run demo"}
                      </button>
                    ) : null
                  }
                >
                  {traces.length === 0
                    ? "No traces yet. Run the demo to generate them."
                    : "No traces match this filter."}
                </Empty>
              </div>
            ) : (
              filtered.map((trace) => {
                const failed = isFailedTrace(trace);
                const degraded = trace.status === "degraded";
                return (
                  <button
                    className="trace-row"
                    key={trace.trace_id}
                    onClick={() => select(trace.trace_id)}
                    aria-current={trace.trace_id === selectedTraceId ? "true" : undefined}
                  >
                    <span className="trace-row__top">
                      <span className="trace-row__id">{shortId(trace.trace_id)}</span>
                      <StatusPill tone={failed ? "fail" : degraded ? "warn" : "pass"}>
                        {failed ? "Failure" : degraded ? "Degraded" : "Passed"}
                      </StatusPill>
                    </span>
                    <span className="trace-row__doc">{trace.document_id}</span>
                    <span className="trace-row__meta">
                      <span className="trace-row__meta-left">
                        <span>{trace.spans?.length ?? 0} spans</span>
                        <span>{relativeTime(trace.created_at)}</span>
                      </span>
                      <span>{formatMs(trace.total_latency_ms)}</span>
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </div>

        <div className="explorer__detail">
          {selectedTrace ? (
            <TraceDetailView
              trace={selectedTrace}
              diagnosis={diagnosis}
              onRefreshTrace={onRefreshTrace}
              onBack={() => setMobileDetail(false)}
            />
          ) : (
            <div className="detail">
              <Empty
                action={
                  <button className="btn btn--sm" onClick={onRunDemo} disabled={isRunningDemo}>
                    {isRunningDemo ? "Running demo" : "Run demo"}
                  </button>
                }
              >
                Select a trace to inspect it.
              </Empty>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
