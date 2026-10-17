// Firestore documents -> app types. The API stores model_dump(mode="json"), so datetimes arrive as ISO strings;
// Firestore Timestamps and Dates are accepted too. A document that breaks the contract throws a DataError
// naming the document and field: the screen shows it instead of rendering half a finding.
import type { DocumentData, FirestoreDataConverter, QueryDocumentSnapshot } from "firebase/firestore";
import {
  CHECK_NAME_VALUES,
  FINDING_LABEL_VALUES,
  MONITOR_ACTION_VALUES,
  MONITOR_STATUS_VALUES,
  RUN_STATUS_VALUES,
  SIGNOFF_DECISION_VALUES,
  STEP_KIND_VALUES,
} from "../generated/schema";
import type { AuditDoc, Check, Citation, FindingDoc, RunClause, RunDoc, SignoffDoc, StepDoc, Verdict } from "./types";

export class DataError extends Error {
  override name = "DataError";
  constructor(
    readonly path: string,
    message: string,
  ) {
    super(`${path}: ${message}`);
  }
}

type Obj = Record<string, unknown>;

function obj(v: unknown, path: string): Obj {
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new DataError(path, "expected an object");
  return v as Obj;
}

function str(o: Obj, key: string, path: string): string {
  const v = o[key];
  if (typeof v !== "string") throw new DataError(`${path}.${key}`, `expected a string, got ${describe(v)}`);
  return v;
}

function optStr(o: Obj, key: string, path: string): string | null {
  const v = o[key];
  if (v === undefined || v === null) return null;
  if (typeof v !== "string") throw new DataError(`${path}.${key}`, `expected a string or null, got ${describe(v)}`);
  return v;
}

function num(o: Obj, key: string, path: string): number {
  const v = o[key];
  if (typeof v !== "number" || !Number.isFinite(v)) throw new DataError(`${path}.${key}`, `expected a number, got ${describe(v)}`);
  return v;
}

function optNum(o: Obj, key: string, path: string): number | null {
  const v = o[key];
  if (v === undefined || v === null) return null;
  return num(o, key, path);
}

function bool(o: Obj, key: string, path: string, fallback?: boolean): boolean {
  const v = o[key];
  if (v === undefined && fallback !== undefined) return fallback;
  if (typeof v !== "boolean") throw new DataError(`${path}.${key}`, `expected true or false, got ${describe(v)}`);
  return v;
}

function oneOf<T extends string>(o: Obj, key: string, allowed: readonly T[], path: string): T {
  const v = o[key];
  if (typeof v !== "string" || !(allowed as readonly string[]).includes(v)) {
    throw new DataError(`${path}.${key}`, `expected one of ${allowed.join(", ")}, got ${describe(v)}`);
  }
  return v as T;
}

function describe(v: unknown): string {
  if (v === undefined) return "nothing";
  if (v === null) return "null";
  if (typeof v === "string") return `"${v.length > 40 ? v.slice(0, 40) + "…" : v}"`;
  return typeof v;
}

/** ISO 8601 string, Firestore Timestamp or Date -> Date. Anything else, or an invalid date, throws. */
export function toDate(v: unknown, path: string): Date {
  let d: Date | undefined;
  if (v instanceof Date) d = v;
  else if (typeof v === "string") d = new Date(v);
  else if (typeof v === "object" && v !== null && typeof (v as { toDate?: unknown }).toDate === "function") {
    d = (v as { toDate: () => Date }).toDate();
  }
  if (!d || Number.isNaN(d.getTime())) throw new DataError(path, `expected a date, got ${describe(v)}`);
  return d;
}

function date(o: Obj, key: string, path: string): Date {
  return toDate(o[key], `${path}.${key}`);
}

function optDate(o: Obj, key: string, path: string): Date | null {
  const v = o[key];
  return v === undefined || v === null ? null : toDate(v, `${path}.${key}`);
}

/**
 * When and by which circular a withdrawn citation's instrument was withdrawn; null for one in force. The corpus
 * records both for every withdrawn instrument (backend corpus/store.py refuses to load otherwise), so a withdrawn
 * citation without them breaks the contract: parseCitation refuses it, and this throws rather than invent a gap.
 */
export function withdrawalOf(c: Citation, path = "citation"): { by: string; on: string } | null {
  const by = c.withdrawn_by ?? null;
  const on = c.withdrawn_on ?? null;
  if (c.in_force) {
    if (by !== null || on !== null) throw new DataError(path, "an instrument in force cannot have withdrawn_by or withdrawn_on");
    return null;
  }
  if (by === null || on === null) throw new DataError(path, "a withdrawn instrument must record withdrawn_by and withdrawn_on");
  return { by, on };
}

export function parseCitation(v: unknown, path: string): Citation {
  const o = obj(v, path);
  const c: Citation = {
    instrument: str(o, "instrument", path),
    instrument_ref: str(o, "instrument_ref", path),
    para: str(o, "para", path),
    url: str(o, "url", path),
    quote: str(o, "quote", path),
    snapshot_date: str(o, "snapshot_date", path),
    content_hash: str(o, "content_hash", path),
    in_force: bool(o, "in_force", path),
    withdrawn_by: optStr(o, "withdrawn_by", path),
    withdrawn_on: optStr(o, "withdrawn_on", path),
    updated_as_on: optStr(o, "updated_as_on", path),
  };
  withdrawalOf(c, path);
  return c;
}

function optCitation(o: Obj, key: string, path: string): Citation | null {
  const v = o[key];
  return v === undefined || v === null ? null : parseCitation(v, `${path}.${key}`);
}

export function parseCheck(v: unknown, path: string): Check {
  const o = obj(v, path);
  return {
    name: oneOf(o, "name", CHECK_NAME_VALUES, path),
    passed: bool(o, "passed", path),
    detail: str(o, "detail", path),
  };
}

export function parseVerdict(v: unknown, path: string): Verdict {
  const o = obj(v, path);
  const checks = o.checks;
  if (!Array.isArray(checks)) throw new DataError(`${path}.checks`, "expected a list");
  return {
    action: oneOf(o, "action", MONITOR_ACTION_VALUES, path),
    checks: checks.map((c, i) => parseCheck(c, `${path}.checks[${i}]`)),
    reason: str(o, "reason", path),
  };
}

function parseClause(v: unknown, path: string): RunClause {
  const o = obj(v, path);
  return { clause_id: str(o, "clause_id", path), title: str(o, "title", path), text: str(o, "text", path) };
}

const SUMMARY_KEYS = [
  "clauses",
  "outdated_reference",
  "gap",
  "conflict",
  "compliant",
  "insufficient_evidence",
  "monitor_revisions",
  "monitor_stops",
  "failed",
  "fallback_clauses",
] as const;

export function parseRun(data: unknown, path: string): RunDoc {
  const o = obj(data, path);
  const rawClauses = o.clauses ?? [];
  if (!Array.isArray(rawClauses)) throw new DataError(`${path}.clauses`, "expected a list");
  const s = o.summary === undefined ? {} : obj(o.summary, `${path}.summary`);
  const summary = Object.fromEntries(
    SUMMARY_KEYS.map((k) => [k, s[k] === undefined ? 0 : num(s, k, `${path}.summary`)]),
  ) as RunDoc["summary"];
  return {
    schema_version: optStr(o, "schema_version", path) ?? "v0",
    run_id: str(o, "run_id", path),
    owner_uid: str(o, "owner_uid", path),
    policy_id: str(o, "policy_id", path),
    policy_title: str(o, "policy_title", path),
    saved: bool(o, "saved", path, false),
    status: oneOf(o, "status", RUN_STATUS_VALUES, path),
    error: optStr(o, "error", path),
    started_at: date(o, "started_at", path),
    finished_at: optDate(o, "finished_at", path),
    agent_model: str(o, "agent_model", path),
    monitor_model: str(o, "monitor_model", path),
    corpus_as_on: str(o, "corpus_as_on", path),
    rbi_as_on: optStr(o, "rbi_as_on", path),
    provider: optStr(o, "provider", path),
    seeded: bool(o, "seeded", path, false),
    clauses: rawClauses.map((c, i) => parseClause(c, `${path}.clauses[${i}]`)),
    summary,
    latency_ms: optNum(o, "latency_ms", path),
    cost_usd_estimate: optNum(o, "cost_usd_estimate", path),
  };
}

export function parseStep(data: unknown, path: string): StepDoc {
  const o = obj(data, path);
  return {
    seq: num(o, "seq", path),
    clause_id: optStr(o, "clause_id", path),
    kind: oneOf(o, "kind", STEP_KIND_VALUES, path),
    summary: str(o, "summary", path),
    tool: optStr(o, "tool", path),
    verdict: o.verdict === undefined || o.verdict === null ? null : parseVerdict(o.verdict, `${path}.verdict`),
    latency_ms: optNum(o, "latency_ms", path),
    at: date(o, "at", path),
  };
}

function parseSignoff(v: unknown, path: string): SignoffDoc {
  const o = obj(v, path);
  return {
    decision: oneOf(o, "decision", SIGNOFF_DECISION_VALUES, path),
    by: str(o, "by", path),
    at: date(o, "at", path),
    note: optStr(o, "note", path) ?? "",
    edited_rationale: optStr(o, "edited_rationale", path),
  };
}

export function parseFinding(data: unknown, path: string): FindingDoc {
  const o = obj(data, path);
  return {
    clause_id: str(o, "clause_id", path),
    clause_text: str(o, "clause_text", path),
    label: oneOf(o, "label", FINDING_LABEL_VALUES, path),
    rationale: str(o, "rationale", path),
    citation: optCitation(o, "citation", path),
    replaced_citation: optCitation(o, "replaced_citation", path),
    monitor_status: oneOf(o, "monitor_status", MONITOR_STATUS_VALUES, path),
    signoff: o.signoff === undefined || o.signoff === null ? null : parseSignoff(o.signoff, `${path}.signoff`),
    error: optStr(o, "error", path),
    agent_model: optStr(o, "agent_model", path),
    fallback_reason: optStr(o, "fallback_reason", path),
  };
}

export function parseAudit(data: unknown, path: string): AuditDoc {
  const o = obj(data, path);
  return {
    seq: num(o, "seq", path),
    at: date(o, "at", path),
    actor: str(o, "actor", path),
    event: str(o, "event", path),
    detail: optStr(o, "detail", path) ?? "",
    clause_id: optStr(o, "clause_id", path),
    model: optStr(o, "model", path),
  };
}

/** Read-only converter for a Firestore reference: the app never writes (rules deny client writes). */
function converter<T>(parse: (data: unknown, path: string) => T): FirestoreDataConverter<T> {
  return {
    toFirestore(): DocumentData {
      throw new Error("Argus never writes to Firestore from the browser; every change goes through the API.");
    },
    fromFirestore(snapshot: QueryDocumentSnapshot): T {
      return parse(snapshot.data(), snapshot.ref.path);
    },
  };
}

export const runConverter = converter(parseRun);
export const stepConverter = converter(parseStep);
export const findingConverter = converter(parseFinding);
export const auditConverter = converter(parseAudit);
