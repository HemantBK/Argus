// The evidence report: every field the officer takes away comes from the recorded run, findings, trace and audit
// log, in the screen's own wording. Expected values are built from the fixtures and the production formatters.
import { describe, expect, it } from "vitest";
import { finding, run } from "../data/fixtures.test-util";
import { BADGE_TEXT, CHECK_TEXT, FAILED_CHIP, LABEL_CHIPS, formatIst } from "../data/selectors";
import type { FindingDoc } from "../data/types";
import { citationLibrary, libraryLabel, runLibrary } from "../shell/format";
import { auditEvent } from "../shell/screens.test-util";
import { CLAUSES, CURRENT_CITATION, OLD_CITATION, OLD_REF, WITHDRAWN_ON, findings, sampleRun, steps } from "./fixtures.test-util";
import { REPORT_TITLE, REPORT_TITLE_SYNTHETIC, evidenceReport, type ReportClause } from "./report";
import { DECISION_TEXT, formatIsoDate, modelName, paraLabel } from "./view";

const PRINTED = new Date("2026-10-18T04:00:00Z");
const AGENT = "gemini-3.8-flash";
const BACKUP = "gemini-3.1-flash-lite";

function build(fs: FindingDoc[] = findings(), over: { synthetic?: boolean; saved?: boolean } = {}) {
  return evidenceReport({
    run: sampleRun({ agent_model: AGENT, saved: over.saved ?? true }),
    findings: fs,
    steps: steps(),
    audit: [auditEvent(1, { event: "run_started" }), auditEvent(2), auditEvent(3, { actor: "officer:FAKE-uid", event: "signoff_accept", clause_id: "c2" })],
    synthetic: over.synthetic ?? true,
    printedAt: PRINTED,
  });
}

function clause(id: string, fs?: FindingDoc[]): ReportClause {
  const c = build(fs).clauses.find((x) => x.clauseId === id);
  if (!c) throw new Error(`no clause ${id}`);
  return c;
}

describe("evidenceReport: header", () => {
  it("says not legal advice, and synthetic policy only when the sample file says so", () => {
    expect(build().title).toBe(REPORT_TITLE_SYNTHETIC);
    expect(build(findings(), { synthetic: false }).title).toBe(REPORT_TITLE);
    expect(REPORT_TITLE_SYNTHETIC.startsWith(REPORT_TITLE)).toBe(true);
  });

  it("names the policy, the kind of run, its id, library dates, models and when it was printed", () => {
    const r = build();
    const s = sampleRun({ agent_model: AGENT });
    const library = runLibrary(s);
    if (!library) throw new Error("the fixture run records its library dates");
    expect(r.policyTitle).toBe(s.policy_title);
    expect(r.runKind).toBe(`Saved run (recorded ${formatIst(s.started_at, true)})`);
    expect(build(findings(), { saved: false }).runKind).toBe(`Live run, started ${formatIst(s.started_at, true)}`);
    expect(r.runId).toBe(s.run_id);
    expect(r.library).toBe(libraryLabel(library));
    expect(r.models).toBe(`Agent ${modelName(AGENT)} · Monitor ${modelName(s.monitor_model)}`);
    expect(r.printedAt).toBe(`Printed ${formatIst(PRINTED, true)}`);
  });

  it("lists every clause of the run in policy order", () => {
    expect(build().clauses.map((c) => c.clauseId)).toEqual(CLAUSES.map((c) => c.clause_id));
  });
});

describe("evidenceReport: each clause", () => {
  it("prints the clause text, label, rationale and the full citation with both library dates and the URL", () => {
    const c = clause("c1");
    expect(c.text).toBe(CLAUSES[0]?.text);
    expect(c.label).toBe(LABEL_CHIPS.outdated_reference.label);
    expect(c.rationale).toBe("FAKE rationale: cites a withdrawn paragraph.");
    expect(c.citation).toEqual({
      instrument: CURRENT_CITATION.instrument,
      instrumentRef: CURRENT_CITATION.instrument_ref,
      para: paraLabel(CURRENT_CITATION),
      quote: CURRENT_CITATION.quote,
      dates: libraryLabel(citationLibrary(CURRENT_CITATION)),
      url: CURRENT_CITATION.url,
    });
    expect(c.citation?.dates).toMatch(/^RBI text as updated on .+ · fetched .+$/);
  });

  it("prints the withdrawn chain: what the policy cites, when and by what it was withdrawn, and what is in force now", () => {
    const chain = clause("c1").chain;
    expect(chain?.cited.para).toBe(paraLabel(OLD_CITATION));
    expect(chain?.cited.dates).toBe(libraryLabel(citationLibrary(OLD_CITATION)));
    expect(chain?.withdrawal).toBe(`Withdrawn by ${OLD_REF} on ${formatIsoDate(WITHDRAWN_ON)}`);
    expect(chain?.current?.para).toBe(paraLabel(CURRENT_CITATION));
    expect(clause("c2").chain).toBeNull();
  });

  it("prints every recorded monitor verdict of the review, with the checks that passed and failed", () => {
    const m = clause("c2").monitor;
    expect(m?.badge).toBe(BADGE_TEXT.revised.label);
    expect(m?.verdicts.map((v) => [v.seq, v.action])).toEqual([
      [4, "Monitor revised"],
      [6, "Monitor: continue"],
    ]);
    expect(m?.verdicts[0]?.failed).toEqual([`${CHECK_TEXT.paragraph_exists}: FAKE: para III.47(3) is not in the RBI library.`]);
    expect(m?.verdicts[1]?.passed).toContain(CHECK_TEXT.quote_matches);
    expect(clause("c3").monitor?.badge).toBe(BADGE_TEXT.stopped.label);
  });

  it("prints a review that did not complete as such, with its recorded error and no rationale", () => {
    const c = clause("c5");
    expect(c.label).toBe(FAILED_CHIP.label);
    expect(c.error).toBe("FAKE: the model call timed out.");
    expect(c.rationale).toBeNull();
    expect(c.citation).toBeNull();
  });

  it("names the model that wrote each finding, and labels the backup model with its recorded reason", () => {
    const fs = findings().map((f) =>
      f.clause_id === "c1"
        ? { ...f, agent_model: AGENT }
        : f.clause_id === "c2"
          ? { ...f, agent_model: BACKUP, fallback_reason: "FAKE: Gemini 3.8 Flash was busy (HTTP 429)." }
          : f.clause_id === "c4"
            ? { ...f, agent_model: BACKUP, fallback_reason: null }
            : f,
    );
    expect(clause("c1", fs)).toMatchObject({ writtenBy: `${modelName(AGENT)} (the run's agent model)`, backup: false });
    expect(clause("c2", fs)).toMatchObject({ writtenBy: `${modelName(BACKUP)} (backup model), because FAKE: Gemini 3.8 Flash was busy (HTTP 429)`, backup: true });
    expect(clause("c4", fs)).toMatchObject({ writtenBy: `${modelName(BACKUP)} (backup model), because ${modelName(AGENT)} was busy`, backup: true });
    expect(clause("c3", fs)).toMatchObject({ writtenBy: `Not recorded on this finding (the run's agent model is ${modelName(AGENT)})`, backup: false });
  });

  it("prints the officer's decision, with Argus's rationale kept beside an officer's edit", () => {
    const at = new Date("2026-10-16T07:00:00Z");
    const fs = findings().map((f) =>
      f.clause_id === "c1"
        ? { ...f, signoff: { decision: "edit" as const, by: "FAKE-uid", at, note: "FAKE note", edited_rationale: "FAKE officer rationale." } }
        : f,
    );
    const c = clause("c1", fs);
    expect(c.decision).toBe(`${DECISION_TEXT.edit} by the officer at ${formatIst(at, true)} · Note: FAKE note`);
    expect(c.officerRationale).toBe("FAKE officer rationale.");
    expect(c.rationale).toBe("FAKE rationale: cites a withdrawn paragraph.");
    expect(clause("c2", fs).decision).toBe("No officer decision recorded (saved runs are read-only)");
  });

  it("a clause with no saved finding says so, never a guessed label", () => {
    const c = clause("c4", findings().filter((f) => f.clause_id !== "c4"));
    expect(c.label).toBe(FAILED_CHIP.label); // the run has ended, so the clause is incomplete
    expect(c).toMatchObject({ rationale: null, citation: null, writtenBy: null, monitor: null });
  });
});

describe("evidenceReport: audit summary", () => {
  it("counts findings, monitor actions, the backup model, decisions and the audit log from the records", () => {
    const at = new Date("2026-10-16T07:00:00Z");
    const fs = findings().map((f) => (f.clause_id === "c2" ? { ...f, signoff: { decision: "accept" as const, by: "FAKE-uid", at, note: "" } } : f));
    const r = evidenceReport({
      run: sampleRun({ summary: { ...sampleRun().summary, fallback_clauses: 2 } }),
      findings: fs,
      steps: steps(),
      audit: [auditEvent(2), auditEvent(1), auditEvent(3, { actor: "officer:FAKE-uid" })],
      synthetic: true,
      printedAt: PRINTED,
    });
    expect(r.summary.lines).toEqual([
      `${CLAUSES.length} clauses: 1 Outdated reference · 0 Gap · 1 Conflict · 0 Compliant · 2 Needs review · 1 ${FAILED_CHIP.label}`,
      "Monitor: 1 citation revised · 1 stopped for human review",
      "2 clauses reviewed by the backup model",
      `Officer decisions: 1 accepted · 0 rejected · 0 edited · ${CLAUSES.length - 1} without a decision`,
      `Audit log: 3 recorded events (1 by the officer), from ${formatIst(auditEvent(1).at, true)} to ${formatIst(auditEvent(3).at, true)}`,
    ]);
  });

  it("counts backup clauses from the findings when they record their model, so it matches each clause's label", () => {
    const fs = findings().map((f) => ({ ...f, agent_model: f.clause_id === "c2" ? BACKUP : AGENT }));
    const r = evidenceReport({ run: sampleRun({ agent_model: AGENT }), findings: fs, steps: steps(), audit: [], synthetic: true, printedAt: PRINTED });
    expect(r.summary.lines).toContain("1 clause reviewed by the backup model");
    expect(r.clauses.filter((c) => c.backup)).toHaveLength(1);
  });

  it("says so when no clause went to the backup model and no audit event was recorded", () => {
    const r = evidenceReport({ run: run({ clauses: [] }), findings: [finding()], steps: [], audit: [], synthetic: false, printedAt: PRINTED });
    expect(r.summary.lines).toContain("No clause was reviewed by the backup model");
    expect(r.summary.lines).toContain("Audit log: no event recorded");
  });
});
