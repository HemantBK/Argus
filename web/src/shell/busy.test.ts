// "Gemini is busy": which failed clauses Gemini's capacity caused, which an unusable reply caused, whether a run is
// partial, and which saved runs are offered. Both prefixes are read from the backend source, so a change of wording
// there fails here instead of silently giving the wrong reason.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { finding, run, T0 } from "../data/fixtures.test-util";
import type { SamplePolicy } from "../start/samplePolicies";
import {
  MODEL_OUTPUT_PREFIX,
  PROVIDER_BUSY_PREFIX,
  busyClauseIds,
  failedClauses,
  failureCause,
  isPartialRun,
  runCounts,
  savedRunOffers,
} from "./busy";

const policy = (policyId: string): SamplePolicy => ({ policyId, title: `FAKE ${policyId}`, clauses: 3, synthetic: true, disclaimer: "FAKE" });
const at = (minutes: number): Date => new Date(T0.getTime() + minutes * 60_000);

const runsPy = (): string => readFileSync(resolve(process.cwd(), "../backend/argus_api/runs.py"), "utf8");

describe("PROVIDER_BUSY_PREFIX", () => {
  it("is how the API's PROVIDER_BUSY sentence starts", () => {
    const sentence = /^PROVIDER_BUSY = "([^"]+)"/m.exec(runsPy())?.[1];
    expect(sentence).toBeDefined();
    expect(sentence?.startsWith(PROVIDER_BUSY_PREFIX)).toBe(true);
  });
});

describe("MODEL_OUTPUT_PREFIX", () => {
  it("is how the API's MODEL_OUTPUT sentence starts", () => {
    const sentence = /^MODEL_OUTPUT = "([^"]+)"/m.exec(runsPy())?.[1];
    expect(sentence).toBeDefined();
    expect(sentence?.startsWith(MODEL_OUTPUT_PREFIX)).toBe(true);
  });

  it("never reads as busy: the two prefixes do not start one another", () => {
    expect(MODEL_OUTPUT_PREFIX.startsWith(PROVIDER_BUSY_PREFIX)).toBe(false);
    expect(PROVIDER_BUSY_PREFIX.startsWith(MODEL_OUTPUT_PREFIX)).toBe(false);
  });
});

describe("failureCause and failedClauses", () => {
  const busy = `${PROVIDER_BUSY_PREFIX}; try again shortly. FAKE: HTTP 429 on every try.`;
  const unusable = `${MODEL_OUTPUT_PREFIX}; a person must review this clause. FAKE: finish reason MALFORMED_RESPONSE.`;

  it("reads the cause from the start of the error only", () => {
    expect(failureCause(busy)).toBe("busy");
    expect(failureCause(`  ${unusable}`)).toBe("model_output");
    expect(failureCause(`FAKE: something else. ${unusable}`)).toBe("other");
    expect(failureCause("FAKE: the reply was not usable.")).toBe("other");
  });

  it("lists every failed clause with its cause, in the order of the findings, and skips reviewed ones", () => {
    const clauses = failedClauses([
      finding({ clause_id: "c1" }),
      finding({ clause_id: "c2", error: unusable }),
      finding({ clause_id: "c3", error: busy }),
      finding({ clause_id: "c4", error: "FAKE: internal." }),
      finding({ clause_id: "c5", error: "" }),
    ]);
    expect(clauses).toEqual([
      { clauseId: "c2", cause: "model_output" },
      { clauseId: "c3", cause: "busy" },
      { clauseId: "c4", cause: "other" },
    ]);
  });
});

describe("runCounts and isPartialRun", () => {
  const summary = run().summary;
  const counted = (over: Partial<typeof summary>) => run({ summary: { ...summary, clauses: 8, ...over } });

  it("counts reviewed clauses from the label counts of the summary", () => {
    const r = counted({ gap: 2, conflict: 1, compliant: 3, outdated_reference: 1, failed: 1 });
    expect(runCounts(r)).toEqual({ reviewed: 7, total: 8, failed: 1 });
    expect(isPartialRun(runCounts(r))).toBe(true);
  });

  it("is partial whatever the status says, and not partial with none reviewed or none failed", () => {
    for (const status of ["done", "failed"] as const) {
      expect(isPartialRun(runCounts(run({ status, summary: { ...summary, clauses: 8, compliant: 7, failed: 1 } })))).toBe(true);
    }
    expect(isPartialRun(runCounts(counted({ failed: 8 })))).toBe(false);
    expect(isPartialRun(runCounts(counted({ compliant: 8 })))).toBe(false);
    // A run still running (or stopped before its summary was written) has an empty summary: never partial.
    expect(isPartialRun(runCounts(run({ status: "running" })))).toBe(false);
  });
});

describe("busyClauseIds", () => {
  it("picks only clauses whose error starts with the busy wording", () => {
    const busy = `${PROVIDER_BUSY_PREFIX}; try again shortly. FAKE: HTTP 429 on every try.`;
    const ids = busyClauseIds([
      finding({ clause_id: "c1", error: busy }),
      finding({ clause_id: "c2", error: "FAKE: the reply was not usable." }),
      finding({ clause_id: "c3", error: null }),
      finding({ clause_id: "c4", error: `FAKE: something else. ${busy}` }),
      finding({ clause_id: "c5", error: `  ${busy}` }),
    ]);
    expect(ids).toEqual(["c1", "c5"]);
  });
});

describe("savedRunOffers", () => {
  const a1 = run({ run_id: "a1", policy_id: "policy_a", saved: true, started_at: at(1) });
  const a2 = run({ run_id: "a2", policy_id: "policy_a", saved: true, started_at: at(2) });
  const b1 = run({ run_id: "b1", policy_id: "policy_b", saved: true, started_at: at(3) });

  it("offers the newest saved run of each sample policy, demo policy first", () => {
    expect(savedRunOffers([b1, a1, a2], [policy("policy_b"), policy("policy_a")]).map((r) => r.run_id)).toEqual(["a2", "b1"]);
  });

  it("never offers the run already on screen, and skips a policy with no saved run", () => {
    expect(savedRunOffers([a1, a2, b1], [policy("policy_a"), policy("policy_b")], "a2").map((r) => r.run_id)).toEqual(["a1", "b1"]);
    expect(savedRunOffers([a1], [policy("policy_a"), policy("policy_b")]).map((r) => r.run_id)).toEqual(["a1"]);
    expect(savedRunOffers([], [policy("policy_a")])).toEqual([]);
  });
});
