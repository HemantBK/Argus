// Builders for FAKE documents used by the unit tests. Every string says FAKE.
import type { Check, Citation, FindingDoc, RunDoc, StepDoc, Verdict } from "./types";

export const T0 = new Date("2026-10-16T06:30:00Z");

export function citation(over: Partial<Citation> = {}): Citation {
  return {
    instrument: "FAKE Credit Facilities Directions",
    instrument_ref: "FAKE/DOR/0001",
    para: "10(2)",
    url: "https://www.rbi.org.in/",
    quote: "FAKE quoted RBI text.",
    snapshot_date: "2026-10-16",
    content_hash: "f".repeat(64),
    in_force: true,
    withdrawn_by: null,
    withdrawn_on: null,
    updated_as_on: "2026-07-15",
    ...over,
  };
}

export function finding(over: Partial<FindingDoc> = {}): FindingDoc {
  return {
    clause_id: "c1",
    clause_text: "FAKE clause text.",
    label: "compliant",
    rationale: "FAKE rationale.",
    citation: citation(),
    replaced_citation: null,
    monitor_status: "passed",
    signoff: null,
    error: null,
    ...over,
  };
}

export function check(name: Check["name"], passed: boolean, detail = "FAKE detail"): Check {
  return { name, passed, detail };
}

export function verdict(action: Verdict["action"], checks: Check[], reason = "FAKE reason"): Verdict {
  return { action, checks, reason };
}

export function step(seq: number, over: Partial<StepDoc> = {}): StepDoc {
  return {
    seq,
    clause_id: "c1",
    kind: "monitor",
    summary: `FAKE step ${seq}`,
    tool: null,
    verdict: null,
    latency_ms: null,
    at: new Date(T0.getTime() + seq * 1000),
    ...over,
  };
}

export function run(over: Partial<RunDoc> = {}): RunDoc {
  return {
    schema_version: "v0.1",
    run_id: "fakerun",
    owner_uid: "fake-owner",
    policy_id: "policy_a",
    policy_title: "FAKE policy",
    saved: false,
    status: "done",
    error: null,
    started_at: T0,
    finished_at: null,
    agent_model: "fake-agent",
    monitor_model: "fake-monitor",
    corpus_as_on: "2026-10-16",
    rbi_as_on: "2026-07-15",
    provider: null,
    seeded: false,
    clauses: [],
    summary: {
      clauses: 0,
      outdated_reference: 0,
      gap: 0,
      conflict: 0,
      compliant: 0,
      insufficient_evidence: 0,
      monitor_revisions: 0,
      monitor_stops: 0,
      failed: 0,
      fallback_clauses: 0,
    },
    latency_ms: null,
    cost_usd_estimate: null,
    ...over,
  };
}
