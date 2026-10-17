// App-side shapes: the generated wire types (src/generated/schema.ts) with every datetime as a Date and the
// v0.1 collections filled in, so components never deal with ISO strings or missing arrays.
import type {
  AuditEvent,
  Citation,
  Finding,
  Run,
  RunClause,
  RunSummary,
  Signoff,
  Step,
} from "../generated/schema";

export type {
  Check,
  CheckName,
  Citation,
  FindingLabel,
  MonitorAction,
  MonitorStatus,
  RunClause,
  RunStatus,
  SignoffDecision,
  StepKind,
  Verdict,
} from "../generated/schema";

export type RunSource = "runs" | "samples";

export type RunDoc = Omit<Run, "started_at" | "finished_at" | "clauses" | "summary" | "saved"> & {
  started_at: Date;
  finished_at: Date | null;
  clauses: RunClause[];
  /** fallback_clauses is null when the run doc does not record it (runs saved before the backup model existed). */
  summary: Required<Omit<RunSummary, "fallback_clauses">> & { fallback_clauses: number | null };
  saved: boolean;
};

export type StepDoc = Omit<Step, "at"> & { at: Date };

export type SignoffDoc = Omit<Signoff, "at"> & { at: Date };

export type FindingDoc = Omit<Finding, "signoff" | "replaced_citation" | "citation"> & {
  citation: Citation | null;
  replaced_citation: Citation | null;
  signoff: SignoffDoc | null;
};

export type AuditDoc = Omit<AuditEvent, "at"> & { at: Date };

/** One live Firestore read: loading until the first snapshot, then data or a visible error. */
export type Live<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; error: Error };
