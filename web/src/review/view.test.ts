import { describe, expect, it } from "vitest";
import { finding, run, step, verdict } from "../data/fixtures.test-util";
import { summarize } from "../data/selectors";
import {
  formatIsoDate,
  highlight,
  lastSignoffAt,
  plural,
  progressText,
  shortHash,
  signoffAvailability,
  sourceLinkLabel,
  traceVerdict,
} from "./view";

describe("formatting", () => {
  it("formats ISO dates and leaves anything else as stored", () => {
    expect(formatIsoDate("2026-07-15")).toBe("15 Jul 2026");
    expect(formatIsoDate("2025-11-28T00:00:00Z")).toBe("28 Nov 2025");
    expect(formatIsoDate("FAKE not a date")).toBe("FAKE not a date");
  });

  it("shortens hashes to 8 characters", () => {
    expect(shortHash("3f9a1c2d" + "0".repeat(56))).toBe("3f9a1c2d…");
    expect(shortHash("abc")).toBe("abc");
  });

  it("labels a source link by its real host", () => {
    expect(sourceLinkLabel("https://www.rbi.org.in/Scripts/FAKE.aspx")).toBe("Open on rbi.org.in");
    expect(sourceLinkLabel("https://example.test/x")).toBe("Open on example.test");
    expect(sourceLinkLabel("not a url")).toBe("Open source page");
  });

  it("pluralises", () => {
    expect(plural(1, "citation")).toBe("1 citation");
    expect(plural(0, "citation")).toBe("0 citations");
  });

  it("marks the first occurrence of a span", () => {
    expect(highlight("FAKE a b c", "b")).toEqual([
      { text: "FAKE a ", mark: false },
      { text: "b", mark: true },
      { text: " c", mark: false },
    ]);
    expect(highlight("FAKE text", "absent")).toEqual([{ text: "FAKE text", mark: false }]);
    expect(highlight("FAKE text", null)).toEqual([{ text: "FAKE text", mark: false }]);
  });
});

describe("trace verdicts", () => {
  it("marks revise and stop lines distinctly and leaves unjudged steps plain", () => {
    expect(traceVerdict(step(1, { verdict: verdict("revise", []) }))).toMatchObject({ tone: "revise", label: "Monitor revised" });
    expect(traceVerdict(step(1, { verdict: verdict("stop", []) }))).toMatchObject({ tone: "stop", label: "Monitor stopped" });
    expect(traceVerdict(step(1, { verdict: verdict("continue", []) }))).toMatchObject({ tone: "plain" });
    expect(traceVerdict(step(1, { kind: "search" }))).toBeNull();
  });
});

describe("progress", () => {
  const clauses = [1, 2, 3].map((i) => ({ clause_id: `c${i}`, title: `FAKE ${i}`, text: `FAKE ${i}` }));

  it("counts finished clauses from the findings while running, with no time estimate", () => {
    const r = run({ status: "running", clauses });
    const counts = summarize(r, [finding({ clause_id: "c1" })], []);
    expect(progressText(r, counts)).toBe("Reviewing: 1 of 3 clauses finished");
  });

  it("is silent once the run is done", () => {
    const r = run({ status: "done", clauses });
    expect(progressText(r, summarize(r, [], []))).toBeNull();
  });

  it("says the policy is being split before clauses exist", () => {
    const r = run({ status: "running" });
    expect(progressText(r, summarize(r, [], []))).toBe("Reviewing: splitting the policy into clauses");
  });

  it("takes the latest sign-off time", () => {
    const a = new Date("2026-10-16T07:00:00Z");
    const b = new Date("2026-10-16T08:00:00Z");
    const fs = [
      finding({ clause_id: "c1", signoff: { decision: "accept", by: "FAKE", at: b, note: "" } }),
      finding({ clause_id: "c2", signoff: { decision: "reject", by: "FAKE", at: a, note: "" } }),
      finding({ clause_id: "c3" }),
    ];
    expect(lastSignoffAt(fs)).toEqual(b);
    expect(lastSignoffAt([finding()])).toBeNull();
  });
});

describe("sign-off availability", () => {
  const f = finding();

  it("refuses saved runs, by source or by the saved flag", () => {
    expect(signoffAvailability("samples", run({ saved: true }), f)).toMatchObject({ allowed: false, reason: expect.stringContaining("Saved runs are read-only") as unknown });
    expect(signoffAvailability("runs", run({ saved: true }), f).allowed).toBe(false);
  });

  it("refuses while the run is running (the API answers 409 then)", () => {
    expect(signoffAvailability("runs", run({ status: "running" }), f)).toEqual({ allowed: false, reason: "Sign off when the run has finished." });
  });

  it("refuses before the run or the finding exists", () => {
    expect(signoffAvailability("runs", null, f).allowed).toBe(false);
    expect(signoffAvailability("runs", run(), null).allowed).toBe(false);
  });

  it("allows a finished live run, a failed one included", () => {
    expect(signoffAvailability("runs", run({ status: "done" }), f)).toEqual({ allowed: true });
    expect(signoffAvailability("runs", run({ status: "failed" }), f)).toEqual({ allowed: true });
  });
});
