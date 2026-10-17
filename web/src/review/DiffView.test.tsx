// "What changed" panel: both quotes side by side, word differences marked with text labels, both paragraph numbers,
// the withdrawal circular and dates; and no claimed change when the quotes read the same.
import { describe, expect, it } from "vitest";
import { clauseRows, outdatedChain } from "../data/selectors";
import type { Citation, FindingDoc } from "../data/types";
import { citationLibrary, libraryLabel } from "../shell/format";
import { DiffView } from "./DiffView";
import { FindingPane } from "./FindingPane";
import { DIFF_TEXT, changeSummary, verdict, wordDiff } from "./diff";
import { CURRENT_CITATION, OLD_CITATION, OLD_REF, findings, noSignoff, sampleRun } from "./fixtures.test-util";
import { count, html, text } from "./render.test-util";

const OLD_QUOTE = "FAKE cooling off period shall be determined by the Board of the RE as laid down in their loan policy.";
const NEW_QUOTE = "FAKE cooling off period shall be determined by the NBFC in terms of their credit policy.";

// The production shape (agent/loop.py): the old side is the whole withdrawn paragraph (replaced_citation()), the
// side in force only the passage the agent quoted from the paragraph now in force.
const FEE_SENTENCE = "ii. FAKE RE may retain a reasonable one-time processing fee, if the customer exits the loan during the cooling-off period.";
const OLD_PARAGRAPH = `10. FAKE Cooling-off period i. The borrower may exit the loan during the cooling-off period. ${OLD_QUOTE} ${FEE_SENTENCE} This shall be disclosed upfront in KFS.`;
const NEW_EXCERPT = `(1) The borrower may exit the loan during the cooling-off period. ${NEW_QUOTE}`;

function chainOf(old: Partial<Citation>, now: Partial<Citation>): Parameters<typeof DiffView>[0]["chain"] {
  const base = findings().find((x) => x.clause_id === "c1");
  if (!base) throw new Error("FAKE fixture has no c1 finding");
  const f: FindingDoc = { ...base, replaced_citation: { ...OLD_CITATION, ...old }, citation: { ...CURRENT_CITATION, ...now } };
  const chain = outdatedChain(f);
  if (!chain?.current) throw new Error("FAKE outdated finding without a current citation");
  return { ...chain, current: chain.current };
}

const render = (old: Partial<Citation> = { quote: OLD_QUOTE }, now: Partial<Citation> = { quote: NEW_QUOTE }): string =>
  html(<DiffView chain={chainOf(old, now)} />);

describe("DiffView", () => {
  it("is a collapsible panel titled What changed, closed by default", () => {
    const out = render();
    expect(out).toMatch(/^<details class="diff-view"><summary class="diff-summary">What changed<\/summary>/);
    expect(out).not.toContain("<details open");
  });

  it("shows both quotes side by side, old first, with both paragraph numbers and instruments", () => {
    const t = text(render());
    const oldAt = t.indexOf(DIFF_TEXT.oldHeading);
    const newAt = t.indexOf(DIFF_TEXT.newHeading);
    expect(oldAt).toBeGreaterThanOrEqual(0);
    expect(newAt).toBeGreaterThan(oldAt);
    expect(t.slice(oldAt, newAt)).toContain(`${OLD_CITATION.instrument} para ${OLD_CITATION.para}`);
    expect(t.slice(newAt)).toContain(`${CURRENT_CITATION.instrument} para ${CURRENT_CITATION.para}`);
  });

  it("names the withdrawing circular and the dates, worded as the source pane words them", () => {
    const t = text(render());
    expect(t).toContain(OLD_REF);
    expect(t).toContain(libraryLabel(citationLibrary(OLD_CITATION)));
    expect(t).toContain(libraryLabel(citationLibrary(CURRENT_CITATION)));
  });

  it("strikes removed words and highlights added ones, each with a text label, not colour alone", () => {
    const out = render();
    const d = wordDiff(OLD_QUOTE, NEW_QUOTE);
    const removed = d.before.filter((p) => p.kind === "removed");
    const added = d.after.filter((p) => p.kind === "added");
    expect(removed.length).toBeGreaterThan(0);
    expect(added.length).toBeGreaterThan(0);
    for (const p of removed) {
      expect(out).toContain(`<del class="diff-removed"><span class="visually-hidden">${DIFF_TEXT.removedLabel}</span>${p.text}</del>`);
    }
    for (const p of added) {
      expect(out).toContain(`<ins class="diff-added"><span class="visually-hidden">${DIFF_TEXT.addedLabel}</span>${p.text}</ins>`);
    }
    expect(count(out, 'class="diff-removed"')).toBe(removed.length);
    expect(count(out, 'class="diff-added"')).toBe(added.length);
    const t = text(out);
    expect(t).toContain(changeSummary(d.removedWords, d.addedWords));
    expect(t).toContain(DIFF_TEXT.legendRemoved);
    expect(t).toContain(DIFF_TEXT.legendAdded);
    expect(t).toContain(DIFF_TEXT.scope);
  });

  it("each quote reads in full, in its own spelling, on its own side", () => {
    const out = render();
    const sides = out.split('<blockquote class="diff-quote">').slice(1).map((s) => s.slice(0, s.indexOf("</blockquote>")));
    const strip = (s: string): string => text(s.replace(/<span class="visually-hidden">[^<]*<\/span>/g, ""));
    expect(sides.map(strip)).toEqual([OLD_QUOTE, NEW_QUOTE]);
  });

  it("equivalent quotes: says only the citation is outdated, marks nothing, claims no change", () => {
    const out = render({ quote: "FAKE the Board\u2019s policy \u2013 one day." }, { quote: "FAKE the Board's  policy - one day." });
    const t = text(out);
    expect(t).toContain(DIFF_TEXT.same);
    expect(out).not.toContain("<del");
    expect(out).not.toContain("<ins");
    expect(t).not.toMatch(/differs|removed|added/i);
  });

  it("production shape: old words outside the quoted passage are greyed as not compared, never struck as removed", () => {
    const out = render({ quote: OLD_PARAGRAPH }, { quote: NEW_EXCERPT });
    const d = wordDiff(OLD_PARAGRAPH, NEW_EXCERPT);
    expect(d.outsideWords).toBeGreaterThan(0);
    const del = [...out.matchAll(/<del class="diff-removed">(.*?)<\/del>/g)].map((m) => text(m[1] ?? ""));
    const greyed = [...out.matchAll(/<span class="diff-outside">(.*?)<\/span><\/span>/g)].map((m) => text(m[1] ?? ""));
    // The fee sentence and everything after the passage are shown, labelled not compared, and never struck.
    expect(greyed.join(" ")).toContain(FEE_SENTENCE);
    for (const g of greyed) expect(g.startsWith(DIFF_TEXT.outsideLabel)).toBe(true);
    for (const w of ["processing", "KFS.", "Cooling-off", "10."]) expect(del.join(" ")).not.toContain(w);
    // The real change inside the passage is still marked, and the counts cover only the compared stretch.
    expect(del.join(" ")).toContain("Board");
    const t = text(out);
    expect(t).toContain(verdict(d));
    expect(t).toContain(changeSummary(d.removedWords, d.addedWords));
    expect(d.removedWords).toBe(wordDiff(OLD_QUOTE, NEW_QUOTE).removedWords);
    expect(t).toContain(DIFF_TEXT.scope);
    expect(t).toContain(DIFF_TEXT.legendOutside);
    // Both texts still read in full on their own sides.
    const sides = out.split('<blockquote class="diff-quote">').slice(1).map((x) => x.slice(0, x.indexOf("</blockquote>")));
    const strip = (x: string): string => text(x.replace(/<span class="visually-hidden">[^<]*<\/span>/g, ""));
    expect(sides.map(strip)).toEqual([OLD_PARAGRAPH, NEW_EXCERPT]);
  });

  it("paragraph numbers are greyed as numbering with a text label, never marked added or removed", () => {
    const sentence = "The borrower may exit the loan during the cooling-off period.";
    const out = render({ quote: `10. FAKE Cooling-off period i. ${sentence} ${FEE_SENTENCE}` }, { quote: `(1) ${sentence}` });
    expect(out).toContain(`<span class="diff-numbering"><span class="visually-hidden">${DIFF_TEXT.numberingLabel}</span>(1)</span>`);
    // Old "10." opens the heading outside the compared stretch: greyed with it as not compared.
    expect(out).toContain(`<span class="diff-outside"><span class="visually-hidden">${DIFF_TEXT.outsideLabel}</span>10. FAKE Cooling-off period i.</span>`);
    expect(out).not.toContain("<ins");
    expect(out).not.toContain("<del");
    const t = text(out);
    expect(t).toContain(DIFF_TEXT.samePassage);
    expect(t).toContain(DIFF_TEXT.legendNumbering);
    expect(t).not.toMatch(/differs/i);
  });

  it("sentence against sentence: nothing is greyed and the greyed legend is not shown", () => {
    const out = render();
    expect(out).not.toContain("diff-outside");
    expect(text(out)).not.toContain(DIFF_TEXT.legendOutside);
  });

  it("a replaced paragraph still in force: the old side is not called withdrawn", () => {
    const out = render({ quote: OLD_QUOTE, in_force: true, withdrawn_by: null, withdrawn_on: null }, { quote: NEW_QUOTE });
    const t = text(out);
    expect(t).not.toContain(DIFF_TEXT.oldHeading);
    expect(t).toContain(DIFF_TEXT.oldHeadingReplaced);
    // The fixture instrument's own name says "Withdrawn"; nothing else on the panel may.
    expect(t.replaceAll(OLD_CITATION.instrument, "")).not.toMatch(/withdrawn/i);
  });
});

describe("FindingPane mounts the panel", () => {
  const paneFor = (list: FindingDoc[], id: string): string => {
    const row = clauseRows(sampleRun(), list).find((r) => r.clauseId === id) ?? null;
    return html(
      <FindingPane
        row={row}
        badge={null}
        injection={null}
        steps={[]}
        availability={{ allowed: false, reason: "FAKE read-only" }}
        onSubmit={noSignoff}
        shown="current"
        onShowCitation={() => undefined}
      />,
    );
  };

  it("under the three-box chain of an outdated-reference finding", () => {
    const out = paneFor(findings(), "c1");
    expect(count(out, '<details class="diff-view">')).toBe(1);
    expect(out.indexOf('class="chain"')).toBeLessThan(out.indexOf('class="diff-view"'));
  });

  it("not for a finding with no replaced citation", () => {
    const list = findings();
    const other = list.find((f) => !f.replaced_citation);
    if (!other) throw new Error("FAKE fixture has no finding without a replaced citation");
    expect(paneFor(list, other.clause_id)).not.toContain("diff-view");
  });

  it("not when no current paragraph was found (nothing to compare against)", () => {
    const list = findings().map((f) => (f.clause_id === "c1" ? { ...f, citation: null } : f));
    const out = paneFor(list, "c1");
    expect(out).toContain('class="chain"');
    expect(out).not.toContain("diff-view");
  });
});
