// What the Start screen knows about the sample policies: the sample policy files the API reviews (samplePolicies.ts)
// plus the saved runs stored in Firestore (UI_SPEC 16.6: names, clause counts and dates come from data, never from
// the page source). Pure; tested in catalog.test.ts.
import type { RunDoc } from "../data/types";
import { runLibrary, type LibraryDates } from "../shell/format";
import type { SamplePolicy } from "./samplePolicies";

/** The policy the big "Run demo" button reviews (task brief: policy_a). */
export const DEMO_POLICY_ID = "policy_a";

export interface SampleCard {
  policyId: string;
  /** The sample file's title; for a policy with saved runs but no file, policy_title of its newest saved run. */
  title: string;
  /** Clauses in the sample file; else the count recorded on the newest saved run, or null when none was recorded. */
  clauses: number | null;
  /** The sample file's synthetic flag; null for a policy known only from saved runs. */
  synthetic: boolean | null;
  /** Saved runs of this policy, newest first. */
  savedRuns: RunDoc[];
}

function newestFirst(a: RunDoc, b: RunDoc): number {
  return b.started_at.getTime() - a.started_at.getTime();
}

export function clauseCount(run: RunDoc): number | null {
  if (run.clauses.length > 0) return run.clauses.length;
  if (run.summary.clauses > 0) return run.summary.clauses;
  return null;
}

/**
 * One card per sample policy file, with its saved runs, then one per other policy that has saved runs (never
 * dropped). The demo policy first, then by policy id.
 */
export function sampleCards(saved: readonly RunDoc[], policies: readonly SamplePolicy[] = []): SampleCard[] {
  const byPolicy = new Map<string, RunDoc[]>();
  for (const r of saved) {
    const list = byPolicy.get(r.policy_id) ?? [];
    list.push(r);
    byPolicy.set(r.policy_id, list);
  }
  const cards: SampleCard[] = [];
  for (const p of policies) {
    const runs = [...(byPolicy.get(p.policyId) ?? [])].sort(newestFirst);
    byPolicy.delete(p.policyId);
    cards.push({ policyId: p.policyId, title: p.title, clauses: p.clauses, synthetic: p.synthetic, savedRuns: runs });
  }
  for (const [policyId, runs] of byPolicy) {
    const sorted = [...runs].sort(newestFirst);
    const newest = sorted[0];
    if (!newest) continue;
    cards.push({ policyId, title: newest.policy_title, clauses: clauseCount(newest), synthetic: null, savedRuns: sorted });
  }
  return cards.sort((a, b) => {
    if (a.policyId === DEMO_POLICY_ID) return -1;
    if (b.policyId === DEMO_POLICY_ID) return 1;
    return a.policyId.localeCompare(b.policyId, "en", { numeric: true });
  });
}

/**
 * The newest saved run of a policy: what a failed or unavailable live run offers instead. Never the run already
 * on screen (a saved run that failed does not offer itself).
 */
export function savedRunFor(policyId: string, saved: readonly RunDoc[], excludeRunId: string | null = null): RunDoc | null {
  return [...saved].filter((r) => r.policy_id === policyId && r.run_id !== excludeRunId).sort(newestFirst)[0] ?? null;
}

/** The RBI fetch date of the newest saved run, for screens that have no run of their own. */
export function latestCorpusAsOn(saved: readonly RunDoc[]): string | null {
  const newest = [...saved].sort(newestFirst)[0];
  return newest && newest.corpus_as_on.trim() !== "" ? newest.corpus_as_on : null;
}

/** The library dates (libraryLabel) of the newest saved run, for screens that have no run of their own. */
export function latestLibrary(saved: readonly RunDoc[]): LibraryDates | null {
  const newest = [...saved].sort(newestFirst)[0];
  return newest ? runLibrary(newest) : null;
}
