// Which evidence-report title a run gets (ReviewScreen -> isSyntheticSample): "synthetic policy" only for a sample
// policy whose sample file says so, never for a pasted clause, an unknown policy or a run not loaded yet.
import { describe, expect, it } from "vitest";
import { parseRun } from "../data/convert";
import { run } from "../data/fixtures.test-util";
import { SAMPLE_POLICIES, type SamplePolicy } from "../start/samplePolicies";
import { isSyntheticSample } from "./view";

const policy = (policyId: string, synthetic: boolean): SamplePolicy => ({ policyId, title: `FAKE ${policyId}`, clauses: 1, synthetic, disclaimer: "FAKE" });
const POLICIES = [policy("fake_synthetic", true), policy("fake_real", false)];

describe("isSyntheticSample", () => {
  it("is true for a sample policy whose file says synthetic", () => {
    expect(isSyntheticSample(run({ policy_id: "fake_synthetic" }), POLICIES)).toBe(true);
  });
  it("is false for a sample policy whose file does not say synthetic", () => {
    expect(isSyntheticSample(run({ policy_id: "fake_real" }), POLICIES)).toBe(false);
  });
  it("is false for a policy no sample file lists, and before the run has loaded", () => {
    expect(isSyntheticSample(run({ policy_id: "fake_unknown" }), POLICIES)).toBe(false);
    expect(isSyntheticSample(null, POLICIES)).toBe(false);
  });
  it("is false for a pasted clause, even one whose policy id matches a synthetic sample", () => {
    expect(isSyntheticSample(run({ policy_id: "fake_synthetic", source: "custom" }), POLICIES)).toBe(false);
  });
  it("reads the source of a pasted-clause run from its stored document, so the guard sees it", () => {
    const stored = { ...run({ policy_id: "fake_synthetic" }), started_at: new Date().toISOString(), finished_at: null, source: "custom" };
    expect(isSyntheticSample(parseRun(stored, "runs/r"), POLICIES)).toBe(false);
  });
  it("is true for both bundled sample policies, which are synthetic", () => {
    for (const p of SAMPLE_POLICIES.policies) expect(isSyntheticSample(run({ policy_id: p.policyId }), SAMPLE_POLICIES.policies)).toBe(true);
  });
});
