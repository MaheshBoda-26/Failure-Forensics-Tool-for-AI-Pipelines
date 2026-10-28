import type { Span, Trace } from "../api/types";

export const STEP_ORDER = ["intake", "extraction", "classification", "summarization"] as const;

export type StepName = (typeof STEP_ORDER)[number];

export const STEP_LABEL: Record<string, string> = {
  intake: "Intake",
  extraction: "Extraction",
  classification: "Classification",
  summarization: "Summarization",
};

export const STEP_BLURB: Record<string, string> = {
  intake: "Normalize and validate",
  extraction: "Pull grounded entities",
  classification: "Map to a document type",
  summarization: "Synthesize the result",
};

/** A trace failed when the run errored *or* its diagnosis blames a step. */
export function isFailedTrace(trace: Trace): boolean {
  if (trace.status === "failure") return true;
  return !!trace.root_cause_step && trace.root_cause_step !== "none";
}

export function stepIndex(name: string | null | undefined): number {
  return name ? STEP_ORDER.indexOf(name.toLowerCase() as StepName) : -1;
}

export function stepLabel(name: string | null | undefined): string {
  if (!name) return "—";
  return STEP_LABEL[name.toLowerCase()] ?? name;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatCost(usd: number | null | undefined): string {
  if (usd == null || Number.isNaN(usd)) return "—";
  return `$${usd.toFixed(4)}`;
}

export function formatPct(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function shortId(id: string | null | undefined, length = 8): string {
  if (!id) return "—";
  return id.length <= length ? id : `${id.slice(0, length)}…`;
}

export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  if (value === "none") return "No failure";
  return value.replace(/_/g, " ");
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return "—";
  const minutes = Math.round((Date.now() - time) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} d ago`;
  return new Date(time).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function findSpan(trace: Trace, step: string): Span | undefined {
  return trace.spans?.find((span) => span.step_name.toLowerCase() === step.toLowerCase());
}

export function totalTokens(trace: Trace): number {
  if (trace.total_tokens) return trace.total_tokens;
  return (trace.spans ?? []).reduce(
    (sum, span) => sum + span.prompt_tokens + span.completion_tokens + span.reasoning_tokens,
    0,
  );
}
