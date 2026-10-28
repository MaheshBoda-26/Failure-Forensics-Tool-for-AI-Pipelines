import React from "react";
import { ArrowRight } from "lucide-react";
import type { Diagnosis, Trace } from "../api/types";
import { shortId } from "../lib/trace";
import { DAGGraphView } from "./DAGGraphView";
import { TokenDiffView } from "./TokenDiffView";
import { Empty } from "./ui";

interface GraphPageProps {
  trace: Trace | null;
  diagnosis: Diagnosis | null;
  onOpenExplorer: () => void;
}

export const GraphPage: React.FC<GraphPageProps> = ({ trace, diagnosis, onOpenExplorer }) => (
  <div className="page">
    <div className="page-head">
      <div>
        <h1 className="page-title">Execution graph</h1>
        <p className="page-sub">
          {trace ? (
            <>
              {trace.document_id} <span className="mono">{shortId(trace.trace_id)}</span>
            </>
          ) : (
            "No trace selected."
          )}
        </p>
      </div>
      <div className="page-actions">
        <button className="btn" onClick={onOpenExplorer}>
          Change trace <ArrowRight size={14} />
        </button>
      </div>
    </div>

    {trace ? (
      <div className="stack">
        <DAGGraphView trace={trace} diagnosis={diagnosis} />
        <TokenDiffView trace={trace} diagnosis={diagnosis} />
      </div>
    ) : (
      <Empty
        action={
          <button className="btn btn--sm" onClick={onOpenExplorer}>
            Open traces
          </button>
        }
      >
        Select a trace to see its stage graph and output comparison.
      </Empty>
    )}
  </div>
);
