import { describe, expect, it } from "vitest";
import { SAMPLE_POLICIES, loadSamplePolicies, parseSamplePolicy } from "./samplePolicies";

const good = {
  policy_id: "policy_x",
  title: "FAKE Sample NBFC X",
  synthetic: true,
  disclaimer: "SYNTHETIC. FAKE.",
  clauses: [{ clause_id: "c1" }, { clause_id: "c2" }],
};

describe("sample policy files", () => {
  it("bundles every policy file the API reviews, each synthetic and with clauses", () => {
    expect(SAMPLE_POLICIES.invalid).toEqual([]);
    expect(SAMPLE_POLICIES.policies.map((p) => p.policyId)).toEqual(expect.arrayContaining(["policy_a", "policy_b"]));
    for (const p of SAMPLE_POLICIES.policies) {
      expect(p.synthetic).toBe(true);
      expect(p.clauses).toBeGreaterThan(0);
    }
  });

  it("counts clauses from the file and keeps the title as written", () => {
    expect(parseSamplePolicy(good, "samples/policy_x.json")).toEqual({
      policyId: "policy_x",
      title: good.title,
      clauses: good.clauses.length,
      synthetic: true,
      disclaimer: good.disclaimer,
    });
  });

  it("reports a malformed file by name and field instead of dropping it", () => {
    const out = loadSamplePolicies({ "../x/policy_y.json": { ...good, title: "" }, "../x/policy_x.json": good });
    expect(out.policies.map((p) => p.policyId)).toEqual(["policy_x"]);
    expect(out.invalid.map((e) => e.message)).toEqual(["samples/policy_y.json: title: expected a non-empty string"]);
    expect(() => parseSamplePolicy({ ...good, clauses: [] }, "f")).toThrow("f: clauses: expected a non-empty list");
    expect(() => parseSamplePolicy({ ...good, synthetic: "yes" }, "f")).toThrow("f: synthetic: expected true or false");
  });
});
