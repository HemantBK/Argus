// Pure functions from stored documents to what the screens show. No React, no Firestore: unit-tested in
// selectors.test.ts. Every state shown comes from a recorded field; nothing is implied (UI_SPEC 16.2).
import { FINDING_LABEL_VALUES } from "../generated/schema";
import { withdrawalOf } from "./convert";
import type { Check, CheckName, Citation, FindingDoc, FindingLabel, RunClause, RunDoc, StepDoc, Verdict } from "./types";

// ---------------------------------------------------------------- finding type chip (UI_SPEC 4.1)

export type StatusTone = "outdated" | "gap" | "ok" | "review";

export interface Chip {
  tone: StatusTone;
  icon: string;
  label: string;
}

/** One vocabulary everywhere. Gap and conflict share the red ⚠ chip but keep their own label. */
export const LABEL_CHIPS: Record<FindingLabel, Chip> = {
  outdated_reference: { tone: "outdated", icon: "⟲", label: "Outdated reference" },
  gap: { tone: "gap", icon: "⚠", label: "Gap" },
  conflict: { tone: "gap", icon: "⚠", label: "Conflict" },
  compliant: { tone: "ok", icon: "✓", label: "Compliant" },
  insufficient_evidence: { tone: "review", icon: "?", label: "Needs review" },
};

export const FAILED_CHIP: Chip = { tone: "review", icon: "!", label: "Couldn't complete" };

export function chipFor(finding: FindingDoc): Chip {
  return finding.error ? FAILED_CHIP : LABEL_CHIPS[finding.label];
}

// ---------------------------------------------------------------- summary bar

export interface SummaryCounts {
  /** Clauses in the policy (run.clauses when present, else findings seen so far). */
  clauses: number;
  /** Findings recorded so far, failed ones included. */
  finished: number;
  /** Per label, counting only clauses whose review completed (failed ones are counted in `failed`). */
  byLabel: Record<FindingLabel, number>;
  /** Findings that record an error, plus clauses left without a finding by a run that has ended. */
  failed: number;
  monitorRevised: number;
  monitorStopped: number;
  signedOff: number;
  /** Every clause has a finding and every finding has an officer decision. */
  reviewComplete: boolean;
}

export function summarize(run: RunDoc | null, findings: readonly FindingDoc[], steps: readonly StepDoc[]): SummaryCounts {
  const byLabel = Object.fromEntries(FINDING_LABEL_VALUES.map((l) => [l, 0])) as Record<FindingLabel, number>;
  let failed = 0;
  let revised = 0;
  let signedOff = 0;
  for (const f of findings) {
    if (f.error) failed += 1;
    else byLabel[f.label] += 1;
    if (f.monitor_status === "revised") revised += 1;
    if (f.signoff) signedOff += 1;
  }
  const stoppedClauses = new Set(
    steps.filter((s) => s.clause_id !== null && s.verdict?.action === "stop").map((s) => s.clause_id),
  );
  const clauses = run && run.clauses.length > 0 ? run.clauses.length : findings.length;
  if (run && run.status !== "running") {
    const seen = new Set(findings.map((f) => f.clause_id));
    failed += run.clauses.filter((c) => !seen.has(c.clause_id)).length;
  }
  return {
    clauses,
    finished: findings.length,
    byLabel,
    failed,
    monitorRevised: revised,
    monitorStopped: stoppedClauses.size,
    signedOff,
    reviewComplete: clauses > 0 && findings.length >= clauses && signedOff >= clauses,
  };
}

// ---------------------------------------------------------------- monitor badge (UI_SPEC 3.2.1 + 16.2)

export type BadgeState = "stopped" | "revised" | "needs_review" | "verified" | "failed";

export const BADGE_TEXT: Record<BadgeState, { icon: string; label: string }> = {
  verified: { icon: "✓", label: "Verified" },
  revised: { icon: "⟲", label: "Revised by monitor" },
  stopped: { icon: "■", label: "Stopped — needs your review" },
  needs_review: { icon: "?", label: "Needs human review" },
  failed: { icon: "!", label: "Couldn't complete" },
};

export interface RecordedVerdict {
  seq: number;
  verdict: Verdict;
}

export interface Badge {
  state: BadgeState;
  icon: string;
  label: string;
  /** Plain-English reason: the recorded verdict reason, the failure, or what the monitor did. */
  reason: string;
  /** Checks that passed in the last recorded verdict for the clause (only checks actually recorded). */
  verifiedChecks: CheckName[];
  /** Checks that failed in the verdict behind this badge (stop or revise). */
  failedChecks: Check[];
  /** Every recorded verdict for the clause, in seq order, for the "show why" view. */
  verdicts: RecordedVerdict[];
}

/** Recorded monitor verdicts for one clause, in seq order. */
export function verdictsFor(clauseId: string, steps: readonly StepDoc[]): RecordedVerdict[] {
  return steps
    .filter((s): s is StepDoc & { verdict: Verdict } => s.clause_id === clauseId && s.verdict !== null && s.verdict !== undefined)
    .map((s) => ({ seq: s.seq, verdict: s.verdict }))
    .sort((a, b) => a.seq - b.seq);
}

export function badgeFor(finding: FindingDoc, steps: readonly StepDoc[]): Badge {
  const verdicts = verdictsFor(finding.clause_id, steps);
  const last = verdicts.at(-1);
  const verifiedChecks = last ? last.verdict.checks.filter((c) => c.passed).map((c) => c.name) : [];
  const make = (state: BadgeState, reason: string, failedChecks: Check[]): Badge => ({
    state,
    ...BADGE_TEXT[state],
    reason,
    verifiedChecks,
    failedChecks,
    verdicts,
  });

  if (finding.error) return make("failed", finding.error, []);

  const stop = verdicts.find((v) => v.verdict.action === "stop");
  if (stop) return make("stopped", stop.verdict.reason, stop.verdict.checks.filter((c) => !c.passed));

  if (finding.monitor_status === "revised") {
    const revise = verdicts.find((v) => v.verdict.action === "revise");
    return make(
      "revised",
      revise ? revise.verdict.reason : "The monitor revised this finding; no revise verdict was recorded in the trace.",
      revise ? revise.verdict.checks.filter((c) => !c.passed) : [],
    );
  }

  if (finding.label === "insufficient_evidence" || finding.monitor_status === "needs_review") {
    const reason =
      finding.label === "insufficient_evidence"
        ? "No supporting RBI text was found in the library; Argus does not guess."
        : (last?.verdict.reason ?? "The monitor sent this finding to a person.");
    return make("needs_review", reason, last ? last.verdict.checks.filter((c) => !c.passed) : []);
  }

  return make(
    "verified",
    verifiedChecks.length > 0
      ? "The monitor recorded these checks as passed."
      : "The monitor passed this finding; no individual checks were recorded.",
    [],
  );
}

/** Plain-English names for the recorded checks (UI_SPEC 3.2.1 wording). */
export const CHECK_TEXT: Record<CheckName, string> = {
  paragraph_exists: "Paragraph exists in the RBI library",
  quote_matches: "Quote matches the RBI text",
  in_force: "Instrument in force",
  supported: "Text supports the finding",
  no_injection: "No hidden instruction in the document",
  on_task: "Step stayed on task",
};

// ---------------------------------------------------------------- outdated reference chain

export interface OutdatedChain {
  /** What the policy cites. */
  cited: Citation;
  /** When and by which circular the cited instrument was withdrawn; null when it is still in force. */
  withdrawal: { by: string; on: string } | null;
  /** The paragraph in force on the library date, when Argus found one. */
  current: Citation | null;
}

/** Policy citation -> withdrawn (by / on) -> current paragraph. Null when the finding has no replaced citation. */
export function outdatedChain(finding: FindingDoc): OutdatedChain | null {
  const cited = finding.replaced_citation;
  if (!cited) return null;
  return { cited, withdrawal: withdrawalOf(cited, `finding ${finding.clause_id}.replaced_citation`), current: finding.citation };
}

// ---------------------------------------------------------------- injection quote

export interface InjectionCatch {
  /** The no_injection check detail, verbatim. */
  detail: string;
  /** The quoted offending text: the first quoted span in the detail, else the whole detail. */
  quote: string;
  seq: number;
}

const QUOTED = /[“"]([^”"]+)[”"]/;

/** The first failed no_injection check recorded for the clause, with the offending text it quotes. */
export function injectionFor(clauseId: string, steps: readonly StepDoc[]): InjectionCatch | null {
  for (const { seq, verdict } of verdictsFor(clauseId, steps)) {
    const check = verdict.checks.find((c) => c.name === "no_injection" && !c.passed);
    if (check) {
      const m = QUOTED.exec(check.detail);
      return { detail: check.detail, quote: m?.[1] ?? check.detail, seq };
    }
  }
  return null;
}

// ---------------------------------------------------------------- clause list

/**
 * What a clause row can honestly say, from recorded data only:
 * - loading: the findings (or the run) have not been read yet, so nothing is known about the clause;
 * - queued: the run is running and the trace has no step for this clause yet;
 * - reviewing: the run is running and the trace has steps for this clause, but no finding is saved;
 * - done / failed: a finding is saved (failed when it records an error);
 * - incomplete: the run has ended (done or failed) without a finding for this clause.
 */
export type ClauseRowState = "loading" | "queued" | "reviewing" | "done" | "failed" | "incomplete";

export interface ClauseRow {
  clauseId: string;
  title: string;
  text: string;
  finding: FindingDoc | null;
  state: ClauseRowState;
}

/** What has been read so far. `steps` is null while the trace has not loaded. */
export interface RowContext {
  findingsLoaded: boolean;
  steps: readonly StepDoc[] | null;
}

const LOADED: RowContext = { findingsLoaded: true, steps: [] };

/** Natural order for clause ids ("c2" before "c10") when the run has no clause list. */
export function compareClauseIds(a: string, b: string): number {
  return a.localeCompare(b, "en", { numeric: true });
}

/** The state of a clause row (see ClauseRowState). */
export function rowState(run: RunDoc | null, clauseId: string, finding: FindingDoc | null, ctx: RowContext): ClauseRowState {
  if (finding) return finding.error ? "failed" : "done";
  if (!ctx.findingsLoaded || run === null) return "loading";
  if (run.status !== "running") return "incomplete";
  if (ctx.steps === null) return "loading";
  return ctx.steps.some((s) => s.clause_id === clauseId) ? "reviewing" : "queued";
}

/**
 * One row per clause in policy order: run.clauses when present (clauses without a finding get a state from the run
 * status and the trace), else the findings seen so far, sorted by clause id.
 */
export function clauseRows(run: RunDoc | null, findings: readonly FindingDoc[], ctx: RowContext = LOADED): ClauseRow[] {
  const byId = new Map(findings.map((f) => [f.clause_id, f]));
  const row = (clauseId: string, title: string, text: string, f: FindingDoc | null): ClauseRow => ({
    clauseId,
    title,
    text,
    finding: f,
    state: rowState(run, clauseId, f, ctx),
  });
  if (run && run.clauses.length > 0) {
    const known = new Set(run.clauses.map((c) => c.clause_id));
    const rows: ClauseRow[] = run.clauses.map((c: RunClause) => row(c.clause_id, c.title, c.text, byId.get(c.clause_id) ?? null));
    // A finding for a clause the run does not list is still shown, never dropped.
    for (const f of [...findings].filter((x) => !known.has(x.clause_id)).sort((a, b) => compareClauseIds(a.clause_id, b.clause_id))) {
      rows.push(row(f.clause_id, f.clause_id, f.clause_text, f));
    }
    return rows;
  }
  return [...findings].sort((a, b) => compareClauseIds(a.clause_id, b.clause_id)).map((f) => row(f.clause_id, f.clause_id, f.clause_text, f));
}

/** Findings in policy order (see clauseRows). */
export function sortFindings(run: RunDoc | null, findings: readonly FindingDoc[]): FindingDoc[] {
  return clauseRows(run, findings).flatMap((r) => (r.finding ? [r.finding] : []));
}

// ---------------------------------------------------------------- audit log

export function actorLabel(actor: string): string {
  if (actor === "agent") return "Argus agent";
  if (actor === "monitor") return "Monitor";
  if (actor === "verifier") return "Verifier";
  if (actor.startsWith("officer:") || actor === "officer") return "Officer";
  return actor;
}

/** "12:04:31 IST" style time for the trace and audit log (UI_SPEC 3.3: time in IST). */
export function formatIst(d: Date, withDate = false): string {
  const opts: Intl.DateTimeFormatOptions = {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    ...(withDate ? { day: "2-digit", month: "short", year: "numeric" } : {}),
  };
  return `${new Intl.DateTimeFormat("en-IN", opts).format(d)} IST`;
}
