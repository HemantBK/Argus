// FAKE documents for the shell, Start, Audit and Evaluation tests. Every string says FAKE.
import { T0 } from "../data/fixtures.test-util";
import type { AuditDoc } from "../data/types";

export function auditEvent(seq: number, over: Partial<AuditDoc> = {}): AuditDoc {
  return { seq, at: new Date(T0.getTime() + seq * 1000), actor: "agent", event: "finding_recorded", detail: `FAKE detail ${seq}`, clause_id: null, model: null, ...over };
}

/** A FAKE stored evaluation: every value is invented for the test. */
export function fakeEval(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_date: "2026-10-18",
    conditions: [
      { id: "plain_gemini", label: "Plain Gemini" },
      { id: "full_argus", label: "Full Argus" },
    ],
    metrics: [
      { id: "citation_accuracy", label: "Citation accuracy", unit: "ratio" },
      { id: "latency", label: "Latency per review", unit: "seconds" },
    ],
    results: [
      { metric: "citation_accuracy", condition: "full_argus", value: 0.5, n_label: "FAKE 2 clause cases" },
      { metric: "citation_accuracy", condition: "plain_gemini", value: null, n_label: null },
    ],
    corpus: {
      documents: [{ instrument: "FAKE Directions", instrument_ref: "FAKE/0001" }],
      as_on: "2026-07-15",
      paragraphs: 3,
      supersession_rows: 2,
    },
    failure_cases: ["FAKE failure case one."],
    ...over,
  };
}

/** A Live read in each state, as the hooks return it. */
export const live = {
  loading: { status: "loading", attached: false } as const,
  ready<T>(data: T) {
    return { status: "ready" as const, data, attached: true };
  },
  error(message: string) {
    return { status: "error" as const, error: new Error(message), attached: false };
  },
};
