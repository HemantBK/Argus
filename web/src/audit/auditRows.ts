// Audit log rows (UI_SPEC 3.3): one row per recorded AuditEvent, chronological, nothing hidden. Officer edits
// show before/after from the recorded finding (the agent's rationale is kept, the officer's is stored beside it).
// Pure; tested in auditRows.test.ts.
import { actorLabel } from "../data/selectors";
import type { AuditDoc, FindingDoc, RunClause, StepKind } from "../data/types";

const STEP_TEXT: Record<StepKind, string> = {
  plan: "Planned the review",
  search: "Searched the RBI library",
  read: "Read an RBI paragraph",
  draft: "Drafted a finding",
  monitor: "Monitor checked a step",
  revise: "Revised after the monitor",
  final: "Final finding",
};

const EVENT_TEXT: Record<string, string> = {
  run_started: "Run started",
  run_finished: "Run finished",
  run_failed: "Run failed",
  finding_recorded: "Finding recorded",
  clause_failed: "Clause could not be completed",
  signoff_accept: "Accepted finding",
  signoff_reject: "Rejected finding",
  signoff_edit: "Edited finding",
};

/** Plain-English action for a recorded event name. An unknown name is shown readably, never dropped. */
export function actionLabel(event: string): string {
  const known = EVENT_TEXT[event];
  if (known) return known;
  if (event.startsWith("step_")) {
    const kind = event.slice("step_".length);
    if (kind in STEP_TEXT) return STEP_TEXT[kind as StepKind];
  }
  const words = event.replaceAll("_", " ").trim();
  return words === "" ? "(no action recorded)" : words.charAt(0).toUpperCase() + words.slice(1);
}

export type ActorKind = "agent" | "monitor" | "verifier" | "officer" | "other";

export function actorKind(actor: string): ActorKind {
  if (actor === "agent" || actor === "monitor" || actor === "verifier") return actor;
  if (actor === "officer" || actor.startsWith("officer:")) return "officer";
  return "other";
}

export interface EditDiff {
  before: string;
  after: string;
}

export interface AuditRow {
  key: string;
  seq: number;
  at: Date;
  actor: string;
  actorKind: ActorKind;
  action: string;
  clauseId: string | null;
  clauseTitle: string | null;
  detail: string;
  model: string | null;
  /** For an officer edit: the agent's rationale and the officer's, from the recorded finding. */
  edit: EditDiff | null;
}

export function auditRows(events: readonly AuditDoc[], findings: readonly FindingDoc[], clauses: readonly RunClause[]): AuditRow[] {
  const findingById = new Map(findings.map((f) => [f.clause_id, f]));
  const titleById = new Map(clauses.map((c) => [c.clause_id, c.title]));
  return [...events]
    .sort((a, b) => a.seq - b.seq)
    .map((e) => {
      const clauseId = e.clause_id ?? null;
      const f = clauseId === null ? undefined : findingById.get(clauseId);
      const edited = f?.signoff?.decision === "edit" ? f.signoff.edited_rationale : null;
      return {
        key: `${e.seq}`,
        seq: e.seq,
        at: e.at,
        actor: actorLabel(e.actor),
        actorKind: actorKind(e.actor),
        action: actionLabel(e.event),
        clauseId,
        clauseTitle: clauseId === null ? null : (titleById.get(clauseId) ?? null),
        detail: e.detail ?? "",
        model: e.model ?? null,
        edit: e.event === "signoff_edit" && f && edited ? { before: f.rationale, after: edited } : null,
      };
    });
}
