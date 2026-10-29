import React, { useEffect, useMemo, useState } from "react";
import { ChevronRight, Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import type { Diagnosis, StepScore, Trace } from "../api/types";
import { STEP_BLURB, STEP_LABEL, STEP_ORDER, formatMs, stepIndex } from "../lib/trace";
import { Empty, StatusPill } from "./ui";

interface DAGGraphViewProps {
  trace: Trace;
  diagnosis: Diagnosis | null;
}

export const DAGGraphView: React.FC<DAGGraphViewProps> = ({ trace, diagnosis }) => {
  const [selectedNode, setSelectedNode] = useState<string>("intake");
  const [zoom, setZoom] = useState(1);

  const effectiveRootStep = diagnosis?.override_step || diagnosis?.root_cause_step;

  const nodes = useMemo(() => {
    if (!trace.spans || trace.spans.length === 0) return [];

    const scores = new Map<string, StepScore>();
    diagnosis?.step_scores?.forEach((score) => scores.set(score.step_name.toLowerCase(), score));

    return [...trace.spans]
      .sort(
        (a, b) =>
          STEP_ORDER.indexOf(a.step_name.toLowerCase() as (typeof STEP_ORDER)[number]) -
          STEP_ORDER.indexOf(b.step_name.toLowerCase() as (typeof STEP_ORDER)[number]),
      )
      .map((span, index) => {
        const score = scores.get(span.step_name.toLowerCase());
        const spanFailed = span.status === "failure";
        const isFailed = spanFailed || (score ? !score.local_pass : false);
        const isTainted =
          !isFailed && !!score && score.local_pass && !score.global_pass;
        const totalChecks = score ? Object.keys(score.deterministic_checks).length : 0;
        const passedChecks = score
          ? Object.values(score.deterministic_checks).filter(Boolean).length
          : 0;
        const failedChecks = totalChecks - passedChecks;

        return {
          id: span.step_name.toLowerCase(),
          stage: `Stage ${index + 1}`,
          name: STEP_LABEL[span.step_name.toLowerCase()] ?? span.step_name,
          desc: STEP_BLURB[span.step_name.toLowerCase()] ?? "Pipeline stage",
          tokens: `${(
            span.prompt_tokens +
            span.completion_tokens +
            span.reasoning_tokens
          ).toLocaleString()} tok`,
          duration: formatMs(span.latency_ms),
          status: isFailed ? "Failed" : isTainted ? "Tainted" : "Passed",
          tone: isFailed ? ("fail" as const) : isTainted ? ("warn" as const) : ("pass" as const),
          checks:
            totalChecks > 0
              ? isFailed
                ? `${failedChecks}/${totalChecks} checks failed`
                : `${passedChecks}/${totalChecks} checks passed`
              : spanFailed
                ? "Step raised an exception"
                : "No checks",
          isRoot: effectiveRootStep === span.step_name,
        };
      });
  }, [trace, diagnosis, effectiveRootStep]);

  useEffect(() => {
    const preferred =
      effectiveRootStep && effectiveRootStep !== "none"
        ? effectiveRootStep.toLowerCase()
        : nodes[0]?.id;
    if (preferred) setSelectedNode(preferred);
  }, [trace.trace_id, effectiveRootStep, nodes]);

  const selected = nodes.find((node) => node.id === selectedNode);
  const failingIndex = effectiveRootStep
    ? stepIndex(effectiveRootStep)
    : -1;

  return (
    <section className="panel">
      <div className="panel__head">
        <div>
          <h2 className="panel__title">Stage graph</h2>
          <p className="panel__hint">Stage order and the first break</p>
        </div>
        <div className="page-actions">
          <button
            className="icon-btn"
            onClick={() => setZoom((value) => Math.max(0.7, value - 0.1))}
            aria-label="Zoom out"
          >
            <ZoomOut size={14} />
          </button>
          <span className="faint num" style={{ fontSize: 12 }}>
            {Math.round(zoom * 100)}%
          </span>
          <button
            className="icon-btn"
            onClick={() => setZoom((value) => Math.min(1.4, value + 0.1))}
            aria-label="Zoom in"
          >
            <ZoomIn size={14} />
          </button>
          <button className="btn btn--sm" onClick={() => setZoom(1)}>
            <Maximize2 size={12} /> Fit
          </button>
        </div>
      </div>

      <div className="dag-canvas">
        {nodes.length === 0 ? (
          <Empty>No spans recorded for this trace.</Empty>
        ) : (
          <div className="dag-nodes" style={{ transform: `scale(${zoom})` }}>
            {nodes.map((node, index) => {
              const isFailed = node.status === "Failed";
              const isTainted = node.status === "Tainted";
              const isSelected = selectedNode === node.id;
              const linkFails = failingIndex !== -1 && index === failingIndex - 1;
              const linkDownstream = failingIndex !== -1 && index >= failingIndex;

              return (
                <React.Fragment key={node.id}>
                  <button
                    className={`dag-node${isSelected ? " is-selected" : ""}${
                      isFailed ? " is-fail" : ""
                    }${isTainted ? " is-tainted" : ""}`}
                    onClick={() => setSelectedNode(node.id)}
                    aria-pressed={isSelected}
                  >
                    <span className="dag-node__head">
                      <span className="dag-node__stage">{node.stage}</span>
                      <StatusPill tone={node.tone}>{node.status}</StatusPill>
                    </span>
                    <span className="dag-node__name">{node.name}</span>
                    <span className="dag-node__desc">{node.desc}</span>
                    <span className="dag-node__meta">
                      <span>{node.duration}</span>
                      <span>{node.tokens}</span>
                    </span>
                  </button>

                  {index < nodes.length - 1 ? (
                    <span
                      className={`dag-link${linkFails ? " is-fail" : ""}${
                        linkDownstream ? " is-downstream" : ""
                      }`}
                      aria-hidden="true"
                    >
                      <span className="dag-link__line" />
                      <ChevronRight size={14} />
                    </span>
                  ) : null}
                </React.Fragment>
              );
            })}
          </div>
        )}
      </div>

      <div className="dag-inspect">
        <span className="dag-inspect__name">{selected?.name ?? "No stage"}</span>
        <span className="dag-inspect__facts">
          {selected ? `${selected.duration}, ${selected.tokens}, ${selected.checks}` : "—"}
        </span>
        <span className="dag-inspect__note">
          {selected
            ? selected.isRoot
              ? "Identified as the root cause by the backward trace."
              : selected.status === "Tainted"
                ? "Downstream of the failing stage. Output may be corrupted."
                : "All local checks passed."
            : "Select a stage to inspect it."}
        </span>
      </div>
    </section>
  );
};
