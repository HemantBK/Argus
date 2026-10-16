import { describe, expect, it } from "vitest";
import { run, T0 } from "../data/fixtures.test-util";
import { DEMO_POLICY_ID, clauseCount, latestCorpusAsOn, sampleCards, savedRunFor } from "./catalog";

const clause = (i: number) => ({ clause_id: `c${i}`, title: `FAKE title ${i}`, text: `FAKE clause ${i}` });
const at = (minutes: number) => new Date(T0.getTime() + minutes * 60_000);

describe("sampleCards", () => {
  const older = run({ run_id: "a-old", policy_id: DEMO_POLICY_ID, policy_title: "FAKE A old title", started_at: at(0), clauses: [clause(1)] });
  const newer = run({ run_id: "a-new", policy_id: DEMO_POLICY_ID, policy_title: "FAKE A (Synthetic)", started_at: at(5), clauses: [1, 2, 3].map(clause) });
  const b = run({ run_id: "b-1", policy_id: "policy_b", policy_title: "FAKE B (Synthetic)", started_at: at(10) });
  b.summary.clauses = 6;

  it("makes one card per policy, named and counted from its newest saved run", () => {
    const cards = sampleCards([b, older, newer]);
    expect(cards.map((c) => c.policyId)).toEqual([DEMO_POLICY_ID, "policy_b"]);
    expect(cards[0]?.title).toBe(newer.policy_title);
    expect(cards[0]?.clauses).toBe(newer.clauses.length);
    expect(cards[0]?.savedRuns.map((r) => r.run_id)).toEqual(["a-new", "a-old"]);
    expect(cards[1]?.clauses).toBe(b.summary.clauses);
  });
  it("has no cards when nothing is saved and no sample file is bundled", () => {
    expect(sampleCards([])).toEqual([]);
  });
  it("makes a card for every sample file, named and counted from the file, with that policy's saved runs", () => {
    const fileB = { policyId: "policy_b", title: "FAKE B file title", clauses: 7, synthetic: true, disclaimer: "SYNTHETIC." };
    const fileC = { policyId: "policy_c", title: "FAKE C file title", clauses: 2, synthetic: true, disclaimer: "SYNTHETIC." };
    const cards = sampleCards([b, newer], [fileC, fileB]);
    expect(cards.map((c) => c.policyId)).toEqual([DEMO_POLICY_ID, "policy_b", "policy_c"]);
    expect(cards[0]?.synthetic).toBeNull();
    expect(cards[1]).toEqual({ policyId: "policy_b", title: fileB.title, clauses: 7, synthetic: true, savedRuns: [b] });
    expect(cards[2]?.savedRuns).toEqual([]);
  });
});

describe("clauseCount", () => {
  it("prefers the clause list, then the summary, and says null when neither recorded one", () => {
    expect(clauseCount(run({ clauses: [clause(1), clause(2)] }))).toBe(2);
    const r = run();
    expect(clauseCount(r)).toBeNull();
    r.summary.clauses = 4;
    expect(clauseCount(r)).toBe(4);
  });
});

describe("savedRunFor and latestCorpusAsOn", () => {
  const a1 = run({ run_id: "a1", policy_id: "policy_a", started_at: at(0), corpus_as_on: "2026-07-01" });
  const a2 = run({ run_id: "a2", policy_id: "policy_a", started_at: at(1), corpus_as_on: "2026-07-15" });
  const b1 = run({ run_id: "b1", policy_id: "policy_b", started_at: at(2), corpus_as_on: "2026-07-20" });

  it("offers the newest saved run of the same policy, never the run on screen", () => {
    expect(savedRunFor("policy_a", [a1, a2, b1])?.run_id).toBe("a2");
    expect(savedRunFor("policy_a", [a1, a2, b1], "a2")?.run_id).toBe("a1");
    expect(savedRunFor("policy_x", [a1, a2, b1])).toBeNull();
  });
  it("takes the snapshot date from the newest saved run", () => {
    expect(latestCorpusAsOn([a1, b1, a2])).toBe(b1.corpus_as_on);
    expect(latestCorpusAsOn([])).toBeNull();
    expect(latestCorpusAsOn([run({ corpus_as_on: " " })])).toBeNull();
  });
});
