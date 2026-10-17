import { Timestamp } from "firebase/firestore";
import { describe, expect, it } from "vitest";
import { DataError, parseAudit, parseFinding, parseRun, parseStep, toDate } from "./convert";

// Documents as the API stores them: model_dump(mode="json"). Every string is FAKE.
const iso = "2026-10-16T06:30:00Z";
const rawCitation = {
  instrument: "FAKE instrument",
  instrument_ref: "FAKE/REF",
  para: "9",
  url: "https://www.rbi.org.in/",
  quote: "FAKE quote",
  snapshot_date: "2026-10-16",
  content_hash: "a".repeat(64),
  in_force: true,
};

describe("toDate", () => {
  it("accepts ISO strings, Timestamps and Dates", () => {
    const expected = new Date(iso).getTime();
    expect(toDate(iso, "x").getTime()).toBe(expected);
    expect(toDate(Timestamp.fromDate(new Date(iso)), "x").getTime()).toBe(expected);
    expect(toDate(new Date(iso), "x").getTime()).toBe(expected);
  });
  it("throws a DataError naming the field for anything else", () => {
    expect(() => toDate("not a date", "runs/r.started_at")).toThrow(DataError);
    expect(() => toDate(42, "runs/r.started_at")).toThrow(/runs\/r.started_at/);
  });
});

describe("parseRun", () => {
  const v0 = {
    schema_version: "v0",
    run_id: "r",
    owner_uid: "u",
    policy_id: "policy_a",
    policy_title: "FAKE policy",
    status: "running",
    started_at: iso,
    agent_model: "fake-agent",
    monitor_model: "fake-monitor",
    corpus_as_on: "2026-10-16",
    summary: { clauses: 2, gap: 1 },
  };
  it("reads a v0 run (no clauses, partial summary) with defaults filled in", () => {
    const r = parseRun(v0, "runs/r");
    expect(r.started_at).toBeInstanceOf(Date);
    expect(r.finished_at).toBeNull();
    expect(r.clauses).toEqual([]);
    expect(r.saved).toBe(false);
    expect(r.seeded).toBe(false);
    expect(r.provider).toBeNull();
    expect(r.rbi_as_on).toBeNull();
    expect(r.summary.gap).toBe(1);
    expect(r.summary.failed).toBe(0);
  });
  it("reads v0.1 clauses", () => {
    const clauses = [{ clause_id: "c1", title: "FAKE", text: "FAKE text" }];
    expect(parseRun({ ...v0, clauses, finished_at: iso }, "runs/r").clauses).toEqual(clauses);
  });
  it("reads the provenance and library date fields", () => {
    const r = parseRun({ ...v0, provider: "vertex", seeded: true, rbi_as_on: "2026-07-15" }, "runs/r");
    expect([r.provider, r.seeded, r.rbi_as_on]).toEqual(["vertex", true, "2026-07-15"]);
    expect(() => parseRun({ ...v0, seeded: "yes" }, "runs/r")).toThrow(/runs\/r.seeded/);
  });
  it("rejects an unknown status loudly", () => {
    expect(() => parseRun({ ...v0, status: "paused" }, "runs/r")).toThrow(/runs\/r.status/);
  });
});

describe("parseStep", () => {
  it("converts the time and parses the verdict and its checks", () => {
    const s = parseStep(
      {
        seq: 3,
        clause_id: "c1",
        kind: "monitor",
        summary: "FAKE",
        at: iso,
        verdict: { action: "revise", reason: "FAKE", checks: [{ name: "paragraph_exists", passed: false, detail: "FAKE" }] },
      },
      "runs/r/steps/0003",
    );
    expect(s.at.getTime()).toBe(new Date(iso).getTime());
    expect(s.verdict?.checks[0]?.name).toBe("paragraph_exists");
    expect(s.tool).toBeNull();
  });
  it("rejects an unknown check name", () => {
    expect(() =>
      parseStep(
        { seq: 1, clause_id: null, kind: "monitor", summary: "F", at: iso, verdict: { action: "stop", reason: "F", checks: [{ name: "vibes", passed: true, detail: "" }] } },
        "p",
      ),
    ).toThrow(/checks\[0\]\.name/);
  });
});

describe("parseFinding", () => {
  const base = {
    clause_id: "c1",
    clause_text: "FAKE",
    label: "outdated_reference",
    rationale: "FAKE",
    citation: { ...rawCitation, updated_as_on: "2026-07-15" },
    replaced_citation: { ...rawCitation, in_force: false, withdrawn_by: "FAKE/W", withdrawn_on: "2025-11-28" },
    monitor_status: "passed",
  };
  it("reads citations, v0.1 withdrawal fields and a sign-off time", () => {
    const f = parseFinding({ ...base, signoff: { decision: "edit", by: "officer:u", at: iso, edited_rationale: "FAKE edit" } }, "p");
    expect(f.replaced_citation?.withdrawn_by).toBe("FAKE/W");
    expect(f.replaced_citation?.withdrawn_on).toBe("2025-11-28");
    expect(f.replaced_citation?.updated_as_on).toBeNull();
    expect(f.citation?.updated_as_on).toBe(base.citation.updated_as_on);
    expect(f.signoff?.at).toBeInstanceOf(Date);
    expect(f.signoff?.note).toBe("");
    expect(f.error).toBeNull();
  });
  it("defaults v0 fields that are absent", () => {
    const v0: Record<string, unknown> = { ...base, citation: rawCitation };
    delete v0.replaced_citation;
    const f = parseFinding(v0, "p");
    expect(f.replaced_citation).toBeNull();
    expect(f.citation?.withdrawn_by).toBeNull();
    expect(f.citation?.updated_as_on).toBeNull();
    expect(f.signoff).toBeNull();
  });
  it("refuses a withdrawn citation that does not record when and by what it was withdrawn", () => {
    const withdrawn = base.replaced_citation;
    for (const replaced_citation of [{ ...withdrawn, withdrawn_by: null }, { ...withdrawn, withdrawn_on: undefined }, { ...rawCitation, in_force: false }]) {
      expect(() => parseFinding({ ...base, replaced_citation }, "runs/r/findings/c1")).toThrow(
        /runs\/r\/findings\/c1.replaced_citation: a withdrawn instrument must record withdrawn_by and withdrawn_on/,
      );
    }
  });
  it("refuses a citation in force that carries a withdrawal", () => {
    for (const extra of [{ withdrawn_by: "FAKE/W" }, { withdrawn_on: "2025-11-28" }]) {
      expect(() => parseFinding({ ...base, citation: { ...rawCitation, ...extra } }, "p")).toThrow(
        /p.citation: an instrument in force cannot have withdrawn_by or withdrawn_on/,
      );
    }
  });
});

describe("parseAudit", () => {
  it("reads optional v0.1 fields as null when absent", () => {
    const a = parseAudit({ seq: 1, at: iso, actor: "agent", event: "FAKE" }, "p");
    expect(a.clause_id).toBeNull();
    expect(a.model).toBeNull();
    expect(a.detail).toBe("");
  });
});
