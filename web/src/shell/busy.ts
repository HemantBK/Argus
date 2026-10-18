// "Gemini is busy" (UI_SPEC 6): when live review cannot help right now, the screen says so and offers the saved
// runs, which are read from Firestore and make no Gemini call when opened. Pure; tested in busy.test.ts.
import type { FindingDoc, RunDoc } from "../data/types";
import { recordedAt } from "./format";
import { sampleCards } from "../start/catalog";
import type { SamplePolicy } from "../start/samplePolicies";

/**
 * How the API starts a clause's error when Gemini's capacity, not Argus, failed the review (backend runs.py
 * PROVIDER_BUSY, "Gemini capacity was busy; try again shortly."). Matched as a prefix, so the sentence after it
 * may change without breaking the match.
 */
export const PROVIDER_BUSY_PREFIX = "Gemini capacity was busy";

/**
 * How the API starts a clause's error when Gemini answered but its reply could not be used, even after one more
 * try (backend runs.py MODEL_OUTPUT, "Gemini returned an unusable reply; a person must review this clause.").
 * Matched as a prefix, like PROVIDER_BUSY_PREFIX. Not a capacity failure, so it never reads as "busy".
 */
export const MODEL_OUTPUT_PREFIX = "Gemini returned an unusable reply";

/**
 * The Start screen while /health shows the agent model's breaker open and the demo policy has a saved run: the
 * screen leads with that saved run ("Open the saved run"), and live review comes second ("Start a live review
 * anyway"). "Complete review": a run with a failed clause is never saved (backend runs.py sample_refusal).
 */
export const BREAKER_OPEN_TITLE = "Gemini is busy right now.";
export const BREAKER_OPEN_SAVED = "Opening the saved run below does not need Gemini; it is a complete review recorded earlier on this app.";

/** The note on the Start screen while /health shows the agent model's breaker open and no saved run can lead. */
export const BREAKER_OPEN_NOTE =
  "Gemini is busy right now; reviews may use the backup model or take longer. Opening a saved run below does not need Gemini.";

/** Link text for one saved run in a list of several: which policy, and when it was recorded. */
export function savedRunOfferText(run: RunDoc): string {
  return `Open the saved run of ${run.policy_title}, recorded at ${recordedAt(run)}`;
}

/** Why a clause's review failed, read from the start of its recorded error: Gemini busy, an unusable reply, or else. */
export type FailureCause = "busy" | "model_output" | "other";

export function failureCause(error: string): FailureCause {
  const text = error.trimStart();
  if (text.startsWith(PROVIDER_BUSY_PREFIX)) return "busy";
  if (text.startsWith(MODEL_OUTPUT_PREFIX)) return "model_output";
  return "other";
}

/** Clause ids whose review failed because Gemini was busy, in the order of the findings. */
export function busyClauseIds(findings: readonly FindingDoc[]): string[] {
  return findings.filter((f) => f.error && failureCause(f.error) === "busy").map((f) => f.clause_id);
}

/** A clause whose review failed (its finding records an error), and why. */
export interface FailedClause {
  clauseId: string;
  cause: FailureCause;
}

/** The failed clauses of a run, in the order of the findings. */
export function failedClauses(findings: readonly FindingDoc[]): FailedClause[] {
  return findings.flatMap((f) => (f.error ? [{ clauseId: f.clause_id, cause: failureCause(f.error) }] : []));
}

/** How many clauses a finished run reviewed, out of how many, and how many failed: all from run.summary. */
export interface RunCounts {
  reviewed: number;
  total: number;
  failed: number;
}

/**
 * The counts of a run the API has finished, from its summary (written once, when the run ends): reviewed is the
 * sum of the label counts, which cover only clauses whose review completed. A run still running, or one the API
 * stopped on an internal error before it wrote the summary, counts zero reviewed.
 */
export function runCounts(run: RunDoc): RunCounts {
  const s = run.summary;
  const reviewed = s.outdated_reference + s.gap + s.conflict + s.compliant + s.insufficient_evidence;
  return { reviewed, total: s.clauses > 0 ? s.clauses : run.clauses.length, failed: s.failed };
}

/**
 * Whether a run reviewed some clauses and failed others. Read from the summary, never from run.status, so it holds
 * both for the API that marks such a run "failed" and for one that marks it "done" with summary.failed above zero.
 */
export function isPartialRun(counts: RunCounts): boolean {
  return counts.reviewed > 0 && counts.failed > 0;
}

/**
 * The saved runs to offer when live review is busy, off or at its limit: the newest saved run of each sample
 * policy, in Start screen order (the demo policy first). Never the run already on screen.
 */
export function savedRunOffers(saved: readonly RunDoc[], policies: readonly SamplePolicy[], excludeRunId: string | null = null): RunDoc[] {
  const others = saved.filter((r) => r.run_id !== excludeRunId);
  return sampleCards(others, policies).flatMap((c) => (c.savedRuns[0] ? [c.savedRuns[0]] : []));
}
