// Every monitor badge state (UI_SPEC 3.2.1, 16.2): icon + label, and the detail behind it, from recorded verdicts.
import { describe, expect, it } from "vitest";
import { BADGE_TEXT, CHECK_TEXT, badgeFor, injectionFor } from "../data/selectors";
import type { FindingDoc } from "../data/types";
import { MonitorBadge } from "./MonitorBadge";
import { INJECTED, findings, steps } from "./fixtures.test-util";
import { html, text } from "./render.test-util";

function byId(id: string): FindingDoc {
  const f = findings().find((x) => x.clause_id === id);
  if (!f) throw new Error(`no FAKE finding ${id}`);
  return f;
}

function renderBadge(id: string, open?: boolean): string {
  const f = byId(id);
  const s = steps();
  return html(<MonitorBadge badge={badgeFor(f, s)} finding={f} steps={s} injection={injectionFor(id, s)} defaultOpen={open} />);
}

describe("MonitorBadge", () => {
  it("Verified: icon + label, and lists only the checks actually recorded", () => {
    const out = renderBadge("c1", true);
    const t = text(out);
    expect(t).toContain(`${BADGE_TEXT.verified.icon} ${BADGE_TEXT.verified.label}`);
    for (const name of ["paragraph_exists", "quote_matches", "in_force", "supported"] as const) expect(t).toContain(CHECK_TEXT[name]);
    // Not recorded in the c1 verdict, so never implied.
    expect(t).not.toContain(CHECK_TEXT.no_injection);
    expect(t).not.toContain(CHECK_TEXT.on_task);
    expect(out).toContain('class="badge badge-ok"');
  });

  it("Verified starts collapsed; its reason is on hover (title) and the detail opens on click", () => {
    const out = renderBadge("c1");
    expect(out).toContain('aria-expanded="false"');
    expect(out).toMatch(/class="badge-detail" hidden=""/);
    expect(out).toContain('title="The monitor recorded these checks as passed."');
  });

  it("Revised: shows before -> after and which check failed", () => {
    const t = text(renderBadge("c2", true));
    expect(t).toContain(`${BADGE_TEXT.revised.icon} ${BADGE_TEXT.revised.label}`);
    expect(t).toContain(`Before ${CHECK_TEXT.paragraph_exists}: failed. FAKE: para III.47(3) is not in the RBI library.`);
    expect(t).toContain("After Cites FAKE Credit Facilities Directions, para III.10(2)");
    expect(t).toContain("FAKE: cite an existing paragraph.");
  });

  it("Stopped: open by default, with the reason, the quoted offending text, and what Argus did not do", () => {
    const out = renderBadge("c3");
    const t = text(out);
    expect(out).toContain('aria-expanded="true"');
    expect(t).toContain(`${BADGE_TEXT.stopped.icon} ${BADGE_TEXT.stopped.label}`);
    expect(t).toContain("FAKE: the policy contains an instruction aimed at the reviewer.");
    expect(t).toContain(`Text found in the sample policy: “${INJECTED}”`);
    expect(t).toContain("did not follow that instruction and did not mark the clause compliant");
    expect(out).toContain('class="badge badge-gap"');
  });

  it("Needs human review: insufficient evidence without a stop never shows Stopped", () => {
    const t = text(renderBadge("c4", true));
    expect(t).toContain(`${BADGE_TEXT.needs_review.icon} ${BADGE_TEXT.needs_review.label}`);
    expect(t).not.toContain(BADGE_TEXT.stopped.label);
    expect(t).toContain("Argus does not guess");
    expect(t).toContain(`${CHECK_TEXT.supported}: not met. FAKE: no paragraph addresses this.`);
  });

  it("Couldn't complete: a failed clause shows the recorded error", () => {
    const t = text(renderBadge("c5", true));
    expect(t).toContain(`${BADGE_TEXT.failed.icon} ${BADGE_TEXT.failed.label}`);
    expect(t).toContain("Recorded error: FAKE: the model call timed out.");
  });
});
