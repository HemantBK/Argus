// The Review screen in each UI_SPEC section 6 state it renders, plus the hero moments of section 10.
// Run-level banners (start problems, unavailable, run failed) are the shell's and are tested there.
import { describe, expect, it } from "vitest";
import { citation, finding, run, step, verdict } from "../data/fixtures.test-util";
import { BADGE_TEXT, FAILED_CHIP, LABEL_CHIPS } from "../data/selectors";
import type { FindingDoc, RunDoc, StepDoc } from "../data/types";
import { citationLibrary, libraryLabel, runLibrary } from "../shell/format";
import { ReviewWorkspace, type ReviewWorkspaceProps } from "./ReviewWorkspace";
import { SourcePane } from "./SourcePane";
import { CLAUSES, CURRENT_CITATION, HASH, INJECTED, LOADING, OLD_CITATION, OLD_REF, WITHDRAWN_ON, findings, noSignoff, ready, sampleRun, steps } from "./fixtures.test-util";
import { formatIsoDate } from "./view";
import { count, html, text } from "./render.test-util";

function render(over: Partial<ReviewWorkspaceProps> = {}): string {
  return html(
    <ReviewWorkspace
      source="samples"
      run={ready<RunDoc | null>(sampleRun())}
      steps={ready(steps())}
      findings={ready(findings())}
      onSignoff={noSignoff}
      {...over}
    />,
  );
}

/** The middle pane's markup only (between its section tags), so assertions do not match the clause list. */
function middle(out: string): string {
  const start = out.indexOf('class="pane pane-finding"');
  const end = out.indexOf('class="pane pane-source"');
  return out.slice(start, end);
}

function liveRunning(): { run: RunDoc; findings: FindingDoc[]; steps: StepDoc[] } {
  const r = run({ status: "running", saved: false, clauses: CLAUSES });
  const fs = findings().slice(0, 2);
  // c3 is in progress (the trace has its steps); c4 and c5 have not started.
  const ss = steps().filter((s) => s.clause_id === null || s.clause_id === "c1" || s.clause_id === "c2" || s.clause_id === "c3");
  return { run: r, findings: fs, steps: ss };
}

describe("ReviewWorkspace: summary bar (the 5-second answer)", () => {
  it("shows counts with icon + label + number, gap and conflict separately, and failed clauses on their own", () => {
    const t = text(render());
    expect(t).toContain(`${CLAUSES.length} clauses`);
    expect(t).toContain(`${LABEL_CHIPS.outdated_reference.icon} 1 ${LABEL_CHIPS.outdated_reference.label}`);
    expect(t).toContain(`${LABEL_CHIPS.gap.icon} 0 ${LABEL_CHIPS.gap.label}`);
    expect(t).toContain(`${LABEL_CHIPS.conflict.icon} 1 ${LABEL_CHIPS.conflict.label}`);
    expect(t).toContain(`${LABEL_CHIPS.insufficient_evidence.icon} 2 ${LABEL_CHIPS.insufficient_evidence.label}`);
    expect(t).toContain(`${FAILED_CHIP.icon} 1 ${FAILED_CHIP.label}`);
  });

  it("shows the monitor summary and sign-off progress from the data", () => {
    const t = text(render());
    expect(t).toContain("Monitor: 1 citation revised · 1 stopped for human review");
    expect(t).toContain(`Signed off 0 / ${CLAUSES.length}`);
  });

  it("labels a saved run as saved with its recording time, and the library dates of the run", () => {
    const t = text(render());
    expect(t).toMatch(/Saved run \(recorded .+ IST\)/);
    const library = runLibrary(sampleRun());
    if (!library) throw new Error("the fixture run records its library dates");
    expect(t).toContain(libraryLabel(library));
    expect(t).toContain("FAKE FinFast NBFC (Synthetic) policy");
  });

  it("does not show monitor figures until the trace has loaded", () => {
    const t = text(render({ steps: LOADING }));
    expect(t).toContain("Monitor: loading recorded verdicts…");
    expect(t).not.toContain("citation revised");
  });
});

describe("ReviewWorkspace: section 6 states", () => {
  it("Slow network: skeleton rows, never blank space", () => {
    const out = render({ run: LOADING, steps: LOADING, findings: LOADING });
    expect(count(out, 'class="clause-skeleton"')).toBeGreaterThan(0);
    expect(out).toContain('aria-busy="true"');
    expect(text(out)).toContain("Loading the run…");
  });

  it("Running: the clause in progress spins, unstarted ones wait, progress is announced, the trace is live", () => {
    const live = liveRunning();
    const out = render({ source: "runs", run: ready<RunDoc | null>(live.run), findings: ready(live.findings), steps: ready(live.steps) });
    expect(count(out, 'class="spinner"')).toBe(1);
    expect(count(out, "Clause c3 is being reviewed")).toBe(1);
    expect(count(out, "Waiting to be reviewed")).toBe(CLAUSES.length - live.findings.length - 1);
    expect(text(out)).toContain(`Reviewing: ${live.findings.length} of ${CLAUSES.length} clauses finished`);
    expect(out).toMatch(/<ol id="trace-lines" class="trace-lines" aria-live="polite"/);
    expect(out).not.toMatch(/<ol id="trace-lines"[^>]*hidden/);
    expect(text(out)).toContain("Live trace");
  });

  it("Running: sign-off waits for the run to finish, with the reason beside the buttons", () => {
    const live = liveRunning();
    const out = middle(render({ source: "runs", run: ready<RunDoc | null>(live.run), findings: ready(live.findings), steps: ready(live.steps) }));
    expect(text(out)).toContain("Sign off when the run has finished.");
    expect(count(out, "disabled")).toBeGreaterThanOrEqual(3);
  });

  it("Running: a clause without its finding yet says in the middle pane whether it is in progress or not started", () => {
    const live = liveRunning();
    const at = (id: string): string =>
      text(middle(render({ source: "runs", run: ready<RunDoc | null>(live.run), findings: ready(live.findings), steps: ready(live.steps), initialClauseId: id })));
    expect(at("c3")).toContain("Argus is reviewing this clause.");
    expect(at("c4")).toContain("Argus has not started this clause yet.");
    expect(at("c4")).toContain(CLAUSES[3]?.text ?? "missing");
  });

  it("Run failed: clauses left without a finding say Couldn't complete, never Reviewing", () => {
    const live = liveRunning();
    const failedRun = { ...live.run, status: "failed" as const };
    const out = render({ source: "runs", run: ready<RunDoc | null>(failedRun), findings: ready(live.findings), steps: ready(live.steps), initialClauseId: "c4" });
    expect(count(out, 'class="spinner"')).toBe(0);
    expect(text(out)).not.toContain("Reviewing");
    expect(text(middle(out))).toContain("The run ended before Argus saved a finding for this clause");
    expect(text(out)).toContain(`${FAILED_CHIP.icon} ${CLAUSES.length - live.findings.length} ${FAILED_CHIP.label}`);
  });

  it("Slow network: while the findings load, no count is shown as 0 and no clause claims to be under review", () => {
    const out = render({ findings: LOADING });
    const t = text(out);
    expect(count(out, 'class="spinner"')).toBe(0);
    expect(t).not.toContain("Reviewing");
    expect(t).not.toMatch(/\b0 (Outdated reference|Gap|Conflict|Compliant|Needs review)\b/);
    expect(t).not.toContain("Signed off 0");
    expect(count(out, 'class="count-loading"')).toBeGreaterThanOrEqual(5);
    expect(t).toContain(`${CLAUSES.length} clauses`);
  });

  it("Partial failure: the failed clause is marked Couldn't complete with its error, nothing dropped", () => {
    const out = render({ initialClauseId: "c5" });
    expect(count(out, 'data-clause-id=')).toBe(CLAUSES.length);
    const mid = text(middle(out));
    expect(mid).toContain(`${FAILED_CHIP.icon} ${FAILED_CHIP.label}`);
    expect(mid).toContain("Recorded error: FAKE: the model call timed out.");
  });

  it("Injection detected: Stopped badge, the offending text quoted and highlighted in the clause", () => {
    const out = middle(render({ initialClauseId: "c3" }));
    const t = text(out);
    expect(t).toContain(BADGE_TEXT.stopped.label);
    expect(t).toContain(`“${INJECTED}”`);
    expect(out).toContain(`<mark class="mark-injection">${INJECTED}</mark>`);
  });

  it("Review complete: every clause decided shows the complete state with its time", () => {
    const at = new Date("2026-10-16T09:15:00Z");
    const fs = findings().map((f) => ({ ...f, signoff: { decision: "accept" as const, by: "officer:FAKE", at, note: "" } }));
    const t = text(render({ source: "runs", run: ready<RunDoc | null>(sampleRun({ saved: false })), findings: ready(fs) }));
    expect(t).toContain(`Signed off ${CLAUSES.length} / ${CLAUSES.length} · Review complete`);
    expect(t).toContain("14:45:00 IST"); // 09:15 UTC
  });

  it("Saved runs are read-only: Accept/Reject/Edit disabled with the explanation", () => {
    const out = middle(render());
    expect(text(out)).toContain("Saved runs are read-only.");
    expect(out).toMatch(/<button type="button" class="primary" disabled=""[^>]*>Accept<\/button>/);
    expect(out).toMatch(/disabled=""[^>]*>Reject<\/button>/);
    expect(out).toMatch(/disabled=""[^>]*>Edit<\/button>/);
    // Edit comes last.
    expect(out.indexOf(">Accept<")).toBeLessThan(out.indexOf(">Reject<"));
    expect(out.indexOf(">Reject<")).toBeLessThan(out.indexOf(">Edit<"));
  });

  it("A finished live run enables the decision buttons", () => {
    const out = middle(render({ source: "runs", run: ready<RunDoc | null>(sampleRun({ saved: false })) }));
    expect(out).toMatch(/<button type="button" class="primary">Accept<\/button>/);
    expect(text(out)).not.toContain("read-only");
  });

  it("An edited finding keeps Argus's original rationale visible beside the officer's", () => {
    const fs = findings().map((f) =>
      f.clause_id === "c1"
        ? { ...f, signoff: { decision: "edit" as const, by: "officer:FAKE", at: new Date(), note: "", edited_rationale: "FAKE officer rationale." } }
        : f,
    );
    const t = text(middle(render({ findings: ready(fs) })));
    expect(t).toContain("Officer's rationale FAKE officer rationale.");
    expect(t).toContain("Argus's original rationale FAKE rationale: cites a withdrawn paragraph.");
  });

  it("A saved run that does not exist says so", () => {
    expect(text(render({ run: ready<RunDoc | null>(null) }))).toContain("Saved run not found");
  });

  it("A live run not created yet waits with skeleton rows (listeners attach before the POST)", () => {
    const out = render({ source: "runs", run: ready<RunDoc | null>(null), findings: ready([]), steps: ready([]) });
    expect(text(out)).toContain("Waiting for the run to start");
    expect(count(out, 'class="clause-skeleton"')).toBeGreaterThan(0);
  });

  it("A listener error is shown, never swallowed", () => {
    const error = new Error("FAKE permission-denied");
    const out = render({ steps: { status: "error", error, attached: false } });
    expect(out).toContain('role="alert"');
    expect(text(out)).toContain("Could not read the trace.");
    expect(text(out)).toContain("FAKE permission-denied");
  });
});

describe("ReviewWorkspace: clause list", () => {
  it("one row per clause in policy order, the first selected, with aria-current", () => {
    const out = render();
    const ids = [...out.matchAll(/data-clause-id="([^"]+)"/g)].map((m) => m[1]);
    expect(ids).toEqual(CLAUSES.map((c) => c.clause_id));
    expect(count(out, 'aria-current="true"')).toBe(1);
    expect(out).toMatch(/aria-current="true" data-clause-id="c1"/);
  });

  it("tags stopped and revised rows", () => {
    const t = text(render());
    expect(t).toContain("■ Stopped");
    expect(t).toContain("⟲ Revised");
  });
});

describe("ReviewWorkspace: outdated reference chain and RBI source", () => {
  it("shows the chain as three boxes left to right: policy cites -> withdrawn -> now in force", () => {
    const out = middle(render());
    const t = text(out);
    expect(count(out, 'class="chain-box')).toBe(3);
    const cited = t.indexOf("Policy cites");
    const withdrawn = t.indexOf(`Withdrawn by ${OLD_REF}`);
    const now = t.indexOf("Now in force");
    expect(cited).toBeGreaterThanOrEqual(0);
    expect(withdrawn).toBeGreaterThan(cited);
    expect(now).toBeGreaterThan(withdrawn);
    expect(t).toContain(`on ${formatIsoDate(WITHDRAWN_ON)}`);
    expect(t).toContain(`para ${CURRENT_CITATION.para}`);
  });

  it("the chain's middle box names the withdrawing circular and the date, from the replaced citation", () => {
    const out = middle(render());
    const box = out.slice(out.indexOf('class="chain-box chain-withdrawn">') + 'class="chain-box chain-withdrawn">'.length);
    const t = text(box.slice(0, box.indexOf("</button>")));
    expect(t).toBe(`⟲ Withdrawn by ${OLD_CITATION.withdrawn_by ?? ""} on ${formatIsoDate(OLD_CITATION.withdrawn_on ?? "")}`);
    expect(text(out)).not.toMatch(/not recorded/i);
  });

  it("a replaced citation still in force: the middle box says so instead of a withdrawal", () => {
    const stillInForce = { ...OLD_CITATION, in_force: true, withdrawn_by: null, withdrawn_on: null, updated_as_on: "2026-07-15" };
    const list = findings().map((f) => (f.clause_id === "c1" ? { ...f, replaced_citation: stillInForce } : f));
    const t = text(middle(render({ findings: ready(list) })));
    expect(t).toContain("⟲ Replaced The cited instrument is still in force; another paragraph applies");
    expect(t).not.toContain("Withdrawn by");
  });

  it("no visible text says snapshot", () => {
    expect(text(render())).not.toMatch(/snapshot/i);
    expect(text(html(<SourcePane finding={finding()} citation={OLD_CITATION} replacement={CURRENT_CITATION} onShowReplacement={() => undefined} />))).not.toMatch(/snapshot/i);
  });

  it("the source pane shows instrument, para, quote, library dates, short hash and the RBI link in a new tab", () => {
    const out = render();
    const src = out.slice(out.indexOf('class="pane pane-source"'));
    const t = text(src);
    expect(t).toContain(CURRENT_CITATION.instrument);
    expect(src).toContain(`<span class="mono">para ${CURRENT_CITATION.para}</span>`);
    expect(t).toContain(CURRENT_CITATION.quote);
    expect(t).toContain(`Dates ${libraryLabel(citationLibrary(CURRENT_CITATION))}`);
    expect(t).toContain(HASH.slice(0, 8));
    expect(t).not.toContain(HASH); // full hash only in the tooltip
    expect(src).toMatch(/href="https:\/\/www\.rbi\.org\.in\/Scripts\/FAKE\.aspx" target="_blank" rel="noopener noreferrer"/);
    expect(t).toContain("Open on rbi.org.in");
    expect(t).toContain("(opens in a new tab)");
  });

  it("a withdrawn instrument gets the Withdrawn banner with circular, date and replacement", () => {
    const t = text(
      html(<SourcePane finding={finding()} citation={OLD_CITATION} replacement={CURRENT_CITATION} onShowReplacement={() => undefined} />),
    );
    expect(t).toContain("Withdrawn");
    expect(t).toContain(`Withdrawn by ${OLD_REF} on ${formatIsoDate(WITHDRAWN_ON)}. It is not in force.`);
    expect(t).toContain(`Dates ${libraryLabel(citationLibrary(OLD_CITATION))}`);
    expect(t).toContain(`Withdrawn 28 Nov 2025 by ${OLD_REF} · fetched`);
    expect(t).toContain(`Replaced by ${CURRENT_CITATION.instrument}, para ${CURRENT_CITATION.para}`);
  });

  it("no citation: says Argus does not guess instead of showing an empty pane", () => {
    const t = text(html(<SourcePane finding={finding({ citation: null })} citation={null} replacement={null} onShowReplacement={() => undefined} />));
    expect(t).toContain("Argus does not guess");
  });

  it("the citation chip names the paragraph and is a button that loads the source pane", () => {
    const out = middle(render({ initialClauseId: "c2" }));
    expect(out).toMatch(/<button type="button" class="citation-chip is-active" aria-pressed="true"><span class="mono">para III\.10\(2\)<\/span>/);
  });
});

describe("ReviewWorkspace: trace drawer", () => {
  it("collapses to one line after the run finishes", () => {
    const out = render();
    expect(out).toMatch(/<ol id="trace-lines"[^>]*hidden=""/);
    expect(text(out)).toContain(`${steps().length} steps recorded · last:`);
  });

  it("marks REVISE and STOP lines with icon + label + tint, and lines with a clause are buttons", () => {
    const out = render();
    expect(out).toContain('class="trace-line trace-revise"');
    expect(out).toContain('class="trace-line trace-stop"');
    const t = text(out);
    expect(t).toContain("⟲ Monitor revised");
    expect(t).toContain("■ Monitor stopped");
    // the run-level plan step (no clause) is not clickable; the clause steps are
    expect(t).toContain("(select clause c2)");
  });

  it("one line per recorded step", () => {
    const extra = [...steps(), step(99, { clause_id: "c1", summary: "FAKE extra", verdict: verdict("continue", []) })];
    const out = render({ steps: ready(extra) });
    expect(count(out, '<li class="trace-line')).toBe(extra.length);
  });
});

describe("ReviewWorkspace: findings without run.clauses", () => {
  it("falls back to the findings in clause-id order", () => {
    const fs = [finding({ clause_id: "c10", citation: citation() }), finding({ clause_id: "c2" })];
    const out = render({ run: ready<RunDoc | null>(sampleRun({ clauses: [] })), findings: ready(fs), steps: ready([]) });
    const ids = [...out.matchAll(/data-clause-id="([^"]+)"/g)].map((m) => m[1]);
    expect(ids).toEqual(["c2", "c10"]);
  });
});
