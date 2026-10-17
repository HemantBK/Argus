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

/** The note on the Start screen while /health shows the agent model's breaker open. */
export const BREAKER_OPEN_NOTE =
  "Gemini is busy right now; reviews may use the backup model or take longer. Opening a saved run below does not need Gemini.";

/** Link text for one saved run in a list of several: which policy, and when it was recorded. */
export function savedRunOfferText(run: RunDoc): string {
  return `Open the saved run of ${run.policy_title}, recorded at ${recordedAt(run)}`;
}

/** Clause ids whose review failed because Gemini was busy, in the order of the findings. */
export function busyClauseIds(findings: readonly FindingDoc[]): string[] {
  return findings.filter((f) => f.error?.trimStart().startsWith(PROVIDER_BUSY_PREFIX) ?? false).map((f) => f.clause_id);
}

/**
 * The saved runs to offer when live review is busy, off or at its limit: the newest saved run of each sample
 * policy, in Start screen order (the demo policy first). Never the run already on screen.
 */
export function savedRunOffers(saved: readonly RunDoc[], policies: readonly SamplePolicy[], excludeRunId: string | null = null): RunDoc[] {
  const others = saved.filter((r) => r.run_id !== excludeRunId);
  return sampleCards(others, policies).flatMap((c) => (c.savedRuns[0] ? [c.savedRuns[0]] : []));
}
