// The evidence report (UI_SPEC 3.2 take-away): what the Review screen shows, laid out for paper, one clause after
// another, then a short audit summary. Pure: built from the recorded run, findings, trace and audit log, with the
// same wording and formatters as the screen, so the paper never says more than the screen. Tested in report.test.ts.
import { CHECK_TEXT, FAILED_CHIP, LABEL_CHIPS, actorLabel, badgeFor, chipFor, clauseRows, formatIst, outdatedChain, summarize, type ClauseRow } from "../data/selectors";
import type { AuditDoc, Citation, FindingDoc, FindingLabel, RunDoc, StepDoc } from "../data/types";
import { citationLibrary, libraryLabel, runLibrary } from "../shell/format";
import { DECISION_TEXT, fallbackSummary, formatIsoDate, modelName, paraLabel, plural, verdictText } from "./view";

export const REPORT_TITLE = "Argus evidence report: not legal advice";
export const REPORT_TITLE_SYNTHETIC = "Argus evidence report: not legal advice; synthetic policy";

export interface ReportCitation {
  instrument: string;
  instrumentRef: string;
  para: string;
  quote: string;
  /** libraryLabel: RBI's updated-as-on date and the fetch date, or when and by what it was withdrawn. */
  dates: string;
  url: string;
}

export interface ReportChain {
  cited: ReportCitation;
  /** "Withdrawn by X on D", or the in-force case where another paragraph applies. */
  withdrawal: string;
  /** The paragraph in force that replaces it, or null when Argus found none. */
  current: ReportCitation | null;
}

export interface ReportVerdict {
  seq: number;
  /** "Monitor: continue", "Monitor revised", "Monitor stopped" (the trace's wording). */
  action: string;
  reason: string;
  passed: string[];
  failed: string[];
}

export interface ReportClause {
  clauseId: string;
  title: string;
  text: string;
  /** The finding chip's label ("Outdated reference", "Couldn't complete", ...), or why there is none. */
  label: string;
  /** Argus's rationale; null when the review did not complete or no finding was saved. */
  rationale: string | null;
  /** The officer's rationale, when the officer edited the finding (Argus's stays in `rationale`). */
  officerRationale: string | null;
  /** The recorded error of a review that did not complete. */
  error: string | null;
  citation: ReportCitation | null;
  chain: ReportChain | null;
  /** Which model wrote the finding, in words; null when no finding was saved. */
  writtenBy: string | null;
  /** The finding itself records which model wrote it (Finding.agent_model). */
  modelRecorded: boolean;
  /** The backup (fallback) model wrote it. */
  backup: boolean;
  /** The monitor badge's label, and every verdict recorded for the review that produced the finding. */
  monitor: { badge: string; verdicts: ReportVerdict[] } | null;
  /** The officer's decision, in words. */
  decision: string;
}

export interface ReportSummary {
  /** One line per figure, each counted from the recorded documents. */
  lines: string[];
}

export interface EvidenceReport {
  title: string;
  policyTitle: string;
  runKind: string;
  runId: string;
  library: string;
  models: string;
  printedAt: string;
  clauses: ReportClause[];
  summary: ReportSummary;
}

function reportCitation(c: Citation): ReportCitation {
  return {
    instrument: c.instrument,
    instrumentRef: c.instrument_ref,
    para: paraLabel(c),
    quote: c.quote,
    dates: libraryLabel(citationLibrary(c)),
    url: c.url,
  };
}

/**
 * Who wrote the finding: the run's agent model, or the backup model and why (FindingPane fallbackNote's reason).
 * A saved run's finished finding with no agent_model was recorded before findings named their model (the API
 * sets it on every finished finding since); it prints the run's agent model and says so, never more.
 */
function writtenBy(finding: FindingDoc, run: RunDoc): { text: string; backup: boolean; recorded: boolean } {
  const runAgentModel = run.agent_model;
  const by = finding.agent_model ?? null;
  if (by === null && run.saved && !finding.error) {
    return { text: `${modelName(runAgentModel)} (run's agent model; recorded before per-finding labels)`, backup: false, recorded: false };
  }
  if (by === null) return { text: `Not recorded on this finding (the run's agent model is ${modelName(runAgentModel)})`, backup: false, recorded: false };
  if (by === runAgentModel) return { text: `${modelName(by)} (the run's agent model)`, backup: false, recorded: true };
  const reason = finding.fallback_reason?.trim().replace(/\.+$/, "") || `${modelName(runAgentModel)} was busy`;
  return { text: `${modelName(by)} (backup model), because ${reason}`, backup: true, recorded: true };
}

function decisionText(finding: FindingDoc | null, saved: boolean): string {
  const s = finding?.signoff ?? null;
  if (s) return `${DECISION_TEXT[s.decision]} by the officer at ${formatIst(s.at, true)}${s.note ? ` · Note: ${s.note}` : ""}`;
  return saved ? "No officer decision recorded (saved runs are read-only)" : "No officer decision recorded";
}

function clauseOf(row: ClauseRow, run: RunDoc, steps: readonly StepDoc[]): ReportClause {
  const f = row.finding;
  const base = { clauseId: row.clauseId, title: row.title, text: row.text, decision: decisionText(f, run.saved) };
  if (!f) {
    return {
      ...base,
      label: row.state === "incomplete" ? FAILED_CHIP.label : "No finding saved yet",
      rationale: null,
      officerRationale: null,
      error: null,
      citation: null,
      chain: null,
      writtenBy: null,
      modelRecorded: false,
      backup: false,
      monitor: null,
    };
  }
  const chain = outdatedChain(f);
  const badge = badgeFor(f, steps);
  const model = writtenBy(f, run);
  return {
    ...base,
    label: chipFor(f).label,
    rationale: f.error ? null : f.rationale,
    officerRationale: f.signoff?.decision === "edit" ? (f.signoff.edited_rationale ?? null) : null,
    error: f.error ?? null,
    citation: f.citation ? reportCitation(f.citation) : null,
    chain: chain
      ? {
          cited: reportCitation(chain.cited),
          withdrawal: chain.withdrawal
            ? `Withdrawn by ${chain.withdrawal.by} on ${formatIsoDate(chain.withdrawal.on)}`
            : "The cited instrument is still in force; another paragraph applies",
          current: chain.current ? reportCitation(chain.current) : null,
        }
      : null,
    writtenBy: model.text,
    modelRecorded: model.recorded,
    backup: model.backup,
    monitor: {
      badge: badge.label,
      verdicts: badge.verdicts.map(({ seq, verdict }) => ({
        seq,
        action: verdictText(verdict.action).label,
        reason: verdict.reason,
        passed: verdict.checks.filter((c) => c.passed).map((c) => CHECK_TEXT[c.name]),
        failed: verdict.checks.filter((c) => !c.passed).map((c) => `${CHECK_TEXT[c.name]}: ${c.detail}`),
      })),
    },
  };
}

/** Label order, as in the summary bar: what needs attention first. */
const ORDER: FindingLabel[] = ["outdated_reference", "gap", "conflict", "compliant", "insufficient_evidence"];

function summaryOf(
  run: RunDoc,
  findings: readonly FindingDoc[],
  steps: readonly StepDoc[],
  audit: readonly AuditDoc[],
  clauses: readonly ReportClause[],
): ReportSummary {
  const counts = summarize(run, findings, steps);
  const byLabel = ORDER.map((l) => `${counts.byLabel[l]} ${LABEL_CHIPS[l].label}`).join(" · ");
  const decisions = { accept: 0, reject: 0, edit: 0 };
  for (const f of findings) if (f.signoff) decisions[f.signoff.decision] += 1;
  const sorted = [...audit].sort((a, b) => a.seq - b.seq);
  const first = sorted[0];
  const last = sorted.at(-1);
  // The API records the run's start under the officer who pressed Run (runs.py execute, "run_started"); that is
  // not something the officer decided on a finding, so "by the officer" counts every other officer event only.
  const officerEvents = sorted.filter((e) => actorLabel(e.actor) === "Officer" && e.event !== "run_started").length;
  return {
    lines: [
      `${plural(counts.clauses, "clause")}: ${byLabel}${counts.failed > 0 ? ` · ${counts.failed} ${FAILED_CHIP.label}` : ""}`,
      `Monitor: ${plural(counts.monitorRevised, "finding")} revised · ${counts.monitorStopped} stopped for human review`,
      backupLine(run, clauses),
      `Officer decisions: ${decisions.accept} ${DECISION_TEXT.accept.toLowerCase()} · ${decisions.reject} ${DECISION_TEXT.reject.toLowerCase()} · ${decisions.edit} ${DECISION_TEXT.edit.toLowerCase()} · ${counts.clauses - counts.signedOff} without a decision`,
      first && last
        ? `Audit log: ${plural(sorted.length, "recorded event")} (${officerEvents} by the officer), from ${formatIst(first.at, true)} to ${formatIst(last.at, true)}`
        : "Audit log: no event recorded",
    ],
  };
}

/**
 * How many clauses the backup model reviewed. Counted from the findings themselves when any of them records its
 * model, so the summary always agrees with the "(backup model)" label printed on each clause; otherwise from the
 * run's summary, as the summary bar does. "None" only when that count is recorded and 0: a run whose summary does
 * not record it (saved before the backup model existed) says so instead of claiming none.
 */
function backupLine(run: RunDoc, clauses: readonly ReportClause[]): string {
  const recorded = clauses.some((c) => c.modelRecorded);
  const n = recorded ? clauses.filter((c) => c.backup).length : run.summary.fallback_clauses;
  if (n === null) return "Backup model use is not recorded for this run";
  if (n === 0) return "No clause was reviewed by the backup model";
  return fallbackSummary({ ...run, summary: { ...run.summary, fallback_clauses: n } }) ?? "";
}

export interface ReportInput {
  run: RunDoc;
  findings: readonly FindingDoc[];
  steps: readonly StepDoc[];
  audit: readonly AuditDoc[];
  /** The run reviews a sample policy that is synthetic (its sample file says so). */
  synthetic: boolean;
  printedAt: Date;
}

export function evidenceReport({ run, findings, steps, audit, synthetic, printedAt }: ReportInput): EvidenceReport {
  const library = runLibrary(run);
  const clauses = clauseRows(run, findings).map((row) => clauseOf(row, run, steps));
  return {
    title: synthetic ? REPORT_TITLE_SYNTHETIC : REPORT_TITLE,
    policyTitle: run.policy_title,
    runKind: run.saved ? `Saved run (recorded ${formatIst(run.started_at, true)})` : `Live run, started ${formatIst(run.started_at, true)}`,
    runId: run.run_id,
    library: library ? libraryLabel(library) : "RBI library dates not recorded",
    models: `Agent ${modelName(run.agent_model)} · Monitor ${modelName(run.monitor_model)}`,
    printedAt: `Printed ${formatIst(printedAt, true)}`,
    clauses,
    summary: summaryOf(run, findings, steps, audit, clauses),
  };
}
