// "Gemini is busy": which failed clauses Gemini's capacity caused, and which saved runs are offered. The prefix is
// read from the backend source, so a change of wording there fails here instead of silently hiding the banner.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { finding, run, T0 } from "../data/fixtures.test-util";
import type { SamplePolicy } from "../start/samplePolicies";
import { PROVIDER_BUSY_PREFIX, busyClauseIds, savedRunOffers } from "./busy";

const policy = (policyId: string): SamplePolicy => ({ policyId, title: `FAKE ${policyId}`, clauses: 3, synthetic: true, disclaimer: "FAKE" });
const at = (minutes: number): Date => new Date(T0.getTime() + minutes * 60_000);

describe("PROVIDER_BUSY_PREFIX", () => {
  it("is how the API's PROVIDER_BUSY sentence starts", () => {
    const source = readFileSync(resolve(process.cwd(), "../backend/argus_api/runs.py"), "utf8");
    const sentence = /^PROVIDER_BUSY = "([^"]+)"/m.exec(source)?.[1];
    expect(sentence).toBeDefined();
    expect(sentence?.startsWith(PROVIDER_BUSY_PREFIX)).toBe(true);
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
