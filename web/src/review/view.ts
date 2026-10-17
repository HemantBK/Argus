// Pure view logic for the Review screen (UI_SPEC 3.2, 6, 16). No React, no Firestore: tested in view.test.ts.
// Everything shown is derived from recorded fields; nothing is estimated or implied.
import type { SummaryCounts } from "../data/selectors";
import type { Citation, FindingDoc, MonitorAction, RunDoc, RunSource, SignoffDecision, StepDoc } from "../data/types";

/** "1 citation" / "2 citations". */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})/;

/** "2026-07-15" (or a full ISO timestamp) -> "15 Jul 2026". A value that is not an ISO date is shown as stored. */
export function formatIsoDate(value: string): string {
  const m = ISO_DATE.exec(value);
  if (!m) return value;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (Number.isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", { timeZone: "UTC", day: "2-digit", month: "short", year: "numeric" }).format(d);
}

/** First 8 hex characters of a content hash, for display; the full hash goes in the tooltip. */
export function shortHash(hash: string): string {
  return hash.length > 8 ? `${hash.slice(0, 8)}…` : hash;
}

export const HASH_EXPLAINED =
  "SHA-256 of the paragraph text in the dated RBI library. If even one character of the text changed, this hash would change, so it proves which exact text Argus checked.";

/** Link text for a source URL: "Open on rbi.org.in" for RBI pages, else the real host (never a label the URL does not match). */
export function sourceLinkLabel(url: string): string {
  let host: string;
  try {
    host = new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "Open source page";
  }
  return `Open on ${host}`;
}

/** Short label for a citation: "para III.10(2)". */
export function paraLabel(c: Citation): string {
  return `para ${c.para}`;
}

// ---------------------------------------------------------------- trace lines

export type TraceTone = "revise" | "stop" | "plain";

export interface TraceVerdictText {
  tone: TraceTone;
  icon: string;
  label: string;
}

const VERDICT_TEXT: Record<MonitorAction, TraceVerdictText> = {
  continue: { tone: "plain", icon: "✓", label: "Monitor: continue" },
  revise: { tone: "revise", icon: "⟲", label: "Monitor revised" },
  stop: { tone: "stop", icon: "■", label: "Monitor stopped" },
};

/** How a monitor action is shown, on a trace line and in the evidence report. */
export function verdictText(action: MonitorAction): TraceVerdictText {
  return VERDICT_TEXT[action];
}

/** The verdict shown on a trace line, or null for a step the monitor did not judge. */
export function traceVerdict(step: StepDoc): TraceVerdictText | null {
  return step.verdict ? verdictText(step.verdict.action) : null;
}

export const STEP_KIND_TEXT: Record<StepDoc["kind"], string> = {
  plan: "Plan",
  search: "Search",
  read: "Read",
  draft: "Draft",
  monitor: "Monitor",
  revise: "Revise",
  final: "Finding saved",
};

// ---------------------------------------------------------------- progress

/** "Reviewing: 3 of 9 clauses finished" while running; null otherwise. No time estimate: none is measured. */
export function progressText(run: RunDoc | null, counts: SummaryCounts): string | null {
  if (!run || run.status !== "running") return null;
  if (counts.clauses === 0) return "Reviewing: loading the sample policy's clauses";
  return `Reviewing: ${counts.finished} of ${plural(counts.clauses, "clause")} finished`;
}

/** Time of the last officer decision, shown as "Review complete <time>". */
export function lastSignoffAt(findings: readonly FindingDoc[]): Date | null {
  let last: Date | null = null;
  for (const f of findings) {
    if (f.signoff && (last === null || f.signoff.at > last)) last = f.signoff.at;
  }
  return last;
}

// ---------------------------------------------------------------- sign-off

export type SignoffAvailability = { allowed: true } | { allowed: false; reason: string };

/** Whether the officer can decide on this finding now, and if not, the plain-English reason shown beside the buttons. */
export function signoffAvailability(source: RunSource, run: RunDoc | null, finding: FindingDoc | null): SignoffAvailability {
  if (source === "samples" || run?.saved) {
    return { allowed: false, reason: "Saved runs are read-only. Start a live run to accept, reject or edit findings." };
  }
  if (!run) return { allowed: false, reason: "The run has not started yet." };
  if (run.status === "running") return { allowed: false, reason: "Sign off when the run has finished." };
  if (!finding) return { allowed: false, reason: "This clause has no finding yet." };
  return { allowed: true };
}

export const DECISION_TEXT: Record<SignoffDecision, string> = {
  accept: "Accepted",
  reject: "Rejected",
  edit: "Edited",
};

// ---------------------------------------------------------------- highlighting

export interface TextPart {
  text: string;
  mark: boolean;
}

/** Split `text` so the first occurrence of `span` is marked. No match: one unmarked part. */
export function highlight(text: string, span: string | null): TextPart[] {
  if (!span) return [{ text, mark: false }];
  const at = text.indexOf(span);
  if (at < 0) return [{ text, mark: false }];
  return [
    { text: text.slice(0, at), mark: false },
    { text: span, mark: true },
    { text: text.slice(at + span.length), mark: false },
  ].filter((p) => p.text.length > 0);
}

// ---------------------------------------------------------------- which model reviewed

/** How the screen names each model, in words; the same ids and names as MODEL_NAMES in backend config.py. */
const MODEL_NAMES: Record<string, string> = {
  "gemini-3.8-flash": "Gemini 3.8 Flash",
  "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite",
  "gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite",
};

/** A model id in words ("Gemini 3.8 Flash"), with or without the OpenRouter "google/" prefix; else the id itself. */
export function modelName(model: string): string {
  return MODEL_NAMES[model.replace(/^google\//, "")] ?? model;
}

/**
 * The line under a finding that the fallback model wrote: which model reviewed the clause and why, from the
 * finding's recorded fallback_reason when present. Null when the run's agent model wrote it, or when the finding
 * predates the record of which model wrote it.
 */
export function fallbackNote(finding: FindingDoc, runAgentModel: string): string | null {
  const by = finding.agent_model ?? null;
  if (by === null || by === runAgentModel) return null;
  const reason = finding.fallback_reason?.trim().replace(/\.+$/, "") || `${modelName(runAgentModel)} was busy`;
  return `Reviewed by ${modelName(by)} because ${reason}.`;
}

/** "2 clauses reviewed by the backup model", or null when the agent model reviewed every clause. */
export function fallbackSummary(run: RunDoc): string | null {
  const n = run.summary.fallback_clauses;
  return n > 0 ? `${plural(n, "clause")} reviewed by the backup model` : null;
}
