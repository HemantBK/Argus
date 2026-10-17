// A clause the backup (fallback) model reviewed because the agent model was busy: the finding says which model
// reviewed it and why, and the summary bar counts such clauses. Runs and findings that predate the record of which
// model wrote a finding render as before.
import { describe, expect, it } from "vitest";
import { finding, run } from "../data/fixtures.test-util";
import type { FindingDoc, RunDoc } from "../data/types";
import { ReviewWorkspace } from "./ReviewWorkspace";
import { findings, noSignoff, ready, sampleRun, steps } from "./fixtures.test-util";
import { html, text } from "./render.test-util";
import { fallbackNote, fallbackSummary, modelName } from "./view";

const PRIMARY = "gemini-3.8-flash";
const BACKUP = "gemini-3.1-flash-lite";
const REASON = "Gemini 3.8 Flash was busy (HTTP 429) after 5 tries";

function render(r: RunDoc, fs: FindingDoc[]): string {
  return html(<ReviewWorkspace source="samples" run={ready<RunDoc | null>(r)} steps={ready(steps())} findings={ready(fs)} onSignoff={noSignoff} />);
}

/** The middle pane's markup only (between its section tags). */
function middle(out: string): string {
  return out.slice(out.indexOf('class="pane pane-finding"'), out.indexOf('class="pane pane-source"'));
}

/** The fixture findings with the first (the selected clause) written by `over`'s model. */
function withFirst(over: Partial<FindingDoc>): FindingDoc[] {
  const [first, ...rest] = findings();
  if (!first) throw new Error("the fixture has no findings");
  return [{ ...first, ...over }, ...rest];
}

describe("modelName", () => {
  it("names the pinned models in words, with or without the OpenRouter prefix", () => {
    expect(modelName(PRIMARY)).toBe("Gemini 3.8 Flash");
    expect(modelName(`google/${BACKUP}`)).toBe("Gemini 3.1 Flash-Lite");
    expect(modelName("gemini-3.5-flash-lite")).toBe("Gemini 3.5 Flash-Lite");
  });
  it("shows any other id as stored", () => {
    expect(modelName("FAKE-model")).toBe("FAKE-model");
  });
});

describe("fallbackNote", () => {
  it("says which model reviewed the clause and why, from the recorded reason", () => {
    const f = finding({ agent_model: BACKUP, fallback_reason: REASON });
    expect(fallbackNote(f, PRIMARY)).toBe(`Reviewed by Gemini 3.1 Flash-Lite because ${REASON}.`);
  });
  it("does not double a full stop the recorded reason already ends with", () => {
    const f = finding({ agent_model: BACKUP, fallback_reason: `${REASON}.` });
    expect(fallbackNote(f, PRIMARY)).toBe(`Reviewed by Gemini 3.1 Flash-Lite because ${REASON}.`);
  });
  it("says the agent model was busy when no reason was recorded", () => {
    for (const fallback_reason of [null, undefined, " "]) {
      const f = finding({ agent_model: BACKUP, fallback_reason });
      expect(fallbackNote(f, PRIMARY)).toBe("Reviewed by Gemini 3.1 Flash-Lite because Gemini 3.8 Flash was busy.");
    }
  });
  it("says nothing for a finding the run's agent model wrote, or one that predates the record", () => {
    expect(fallbackNote(finding({ agent_model: PRIMARY }), PRIMARY)).toBeNull();
    expect(fallbackNote(finding({ agent_model: null }), PRIMARY)).toBeNull();
    expect(fallbackNote(finding(), PRIMARY)).toBeNull();
  });
});

describe("fallbackSummary", () => {
  it("counts the clauses the backup model reviewed", () => {
    const at = (n: number): RunDoc => run({ summary: { ...run().summary, fallback_clauses: n } });
    expect(fallbackSummary(at(1))).toBe("1 clause reviewed by the backup model");
    expect(fallbackSummary(at(3))).toBe("3 clauses reviewed by the backup model");
    expect(fallbackSummary(at(0))).toBeNull();
  });
});

describe("Review screen: a clause the backup model reviewed", () => {
  it("the finding says which model reviewed it and why", () => {
    const out = render(sampleRun({ agent_model: PRIMARY }), withFirst({ agent_model: BACKUP, fallback_reason: REASON }));
    expect(text(middle(out))).toContain(`Reviewed by Gemini 3.1 Flash-Lite because ${REASON}.`);
  });
  it("a finding without a recorded reason still names both models", () => {
    const out = render(sampleRun({ agent_model: PRIMARY }), withFirst({ agent_model: BACKUP }));
    expect(text(middle(out))).toContain("Reviewed by Gemini 3.1 Flash-Lite because Gemini 3.8 Flash was busy.");
  });
  it("the summary bar counts the clauses the backup model reviewed", () => {
    const r = sampleRun({ agent_model: PRIMARY, summary: { ...sampleRun().summary, fallback_clauses: 2 } });
    expect(text(render(r, findings()))).toContain("2 clauses reviewed by the backup model");
  });
  it("a finding the agent model wrote, and a run with no backup clauses, render as before", () => {
    const recorded = render(sampleRun({ agent_model: PRIMARY }), withFirst({ agent_model: PRIMARY }));
    expect(text(recorded)).not.toContain("Reviewed by");
    expect(text(recorded)).not.toContain("backup model");
  });
  it("an old run, whose findings have no fallback fields, renders as one whose fields are null", () => {
    const absent = findings().map((f) => {
      const rest: FindingDoc = { ...f };
      delete rest.agent_model;
      delete rest.fallback_reason;
      return rest;
    });
    expect(absent.every((f) => !("agent_model" in f) && !("fallback_reason" in f))).toBe(true);
    const old = render(sampleRun(), absent);
    expect(text(old)).not.toContain("Reviewed by");
    expect(text(old)).not.toContain("backup model");
    const nulls = render(sampleRun(), absent.map((f) => ({ ...f, agent_model: null, fallback_reason: null })));
    expect(old).toBe(nulls);
  });
  it("a clause the breaker sent straight to the backup names each model once", () => {
    const f = finding({ agent_model: BACKUP, fallback_reason: "Gemini 3.8 Flash was busy on recent clauses" });
    expect(fallbackNote(f, PRIMARY)).toBe("Reviewed by Gemini 3.1 Flash-Lite because Gemini 3.8 Flash was busy on recent clauses.");
  });
});
