// Word-level diff behind the "What changed" panel: the cited text against the passage quoted from the text in force.
import { describe, expect, it } from "vitest";
import { DIFF_TEXT, MAX_LCS_CELLS, changeSummary, isEnumerator, normalise, sameWording, verdict, wordDiff, words, type DiffPart } from "./diff";

const join = (parts: readonly DiffPart[]): string => parts.map((p) => p.text).join(" ");
const only = (parts: readonly DiffPart[], kind: DiffPart["kind"]): string[] => parts.filter((p) => p.kind === kind).map((p) => p.text);

// The shape of the hero clause: one paragraph carried into the new directions with the actor and policy renamed.
const OLD = "FAKE cooling off period shall be determined by the Board of the RE as laid down in their loan policy, subject to one day.";
const NEW = "FAKE cooling off period shall be determined by the NBFC in terms of their credit policy, subject to one day.";

// The production shape (agent/loop.py): replaced_citation.quote is the WHOLE old paragraph, verbatim
// (replaced_citation()), while citation.quote is only the passage the agent quoted from the paragraph in force, a
// contiguous excerpt of it. Modelled on the hero finding: old para 10 against an excerpt of new para 11(1).
const OLD_PARA =
  "10. FAKE Cooling-off period i. The borrower shall be given an explicit option to exit a digital loan during an initial cooling-off period. " +
  "The cooling off period shall be determined by the Board of the RE as laid down in their loan policy, subject to one day. " +
  "For borrower continuing with the loan, pre-payment shall continue to be allowed as per applicable RBI guidelines. " +
  "ii. RE may retain a reasonable one-time processing fee, if the customer exits the loan during the cooling-off period. " +
  "This, if applicable, shall be disclosed to the customer upfront in KFS.";
const PROCESSING_FEE = "ii. RE may retain a reasonable one-time processing fee, if the customer exits the loan during the cooling-off period.";
const EXCERPT_FIRST = "(1) The borrower shall be given an explicit option to exit a digital loan during an initial cooling-off period.";
const EXCERPT_TWO =
  `${EXCERPT_FIRST} The cooling off period shall be determined by the NBFC in terms of their credit policy, subject to one day.`;
const EXCERPT_THREE =
  `${EXCERPT_TWO} For borrower continuing with the loan, pre-payment shall continue to be allowed as per FAKE Responsible Business Conduct Directions, 2025.`;

describe("normalise / sameWording", () => {
  it("folds NFC, curly quotes, dashes and whitespace, but keeps case and punctuation", () => {
    expect(normalise("  it\u2019s  \u201Cfine\u201D \u2014 ok\u00A0\n")).toBe(`it's "fine" - ok`);
    expect(normalise("e\u0301")).toBe("\u00E9");
    expect(sameWording("FAKE Rule.", "FAKE  rule.")).toBe(false);
    expect(sameWording("FAKE rule.", "FAKE rule")).toBe(false);
  });

  it("splits words on any whitespace and drops empty input", () => {
    expect(words("  a \n b\tc ")).toEqual(["a", "b", "c"]);
    expect(words("   ")).toEqual([]);
  });
});

describe("isEnumerator", () => {
  it("takes paragraph numbers at the start, after another number, or after a closing . ; :", () => {
    expect(isEnumerator(words("10. Cooling-off period i. The borrower"))).toEqual([true, false, false, false, false, false]);
    expect(isEnumerator(words("11. (1) The rule. (2) Next; (a) one: ii. two"))).toEqual([
      true, true, false, false, true, false, true, false, true, false,
    ]);
    for (const w of ["(1)", "(12)", "(a)", "(iv)", "(IV)", "10.", "11.2.", "i.", "a)", "IV."]) expect(isEnumerator([w])).toEqual([true]);
  });

  it("leaves prose alone: a reference mid-sentence, or a word that only looks short", () => {
    expect(isEnumerator(words("under sub-paragraph (a) of para 10."))).toEqual([false, false, false, false, false, false]);
    for (const w of ["The", "(FAKE)", "RE", "1,000", "(1000)", "10"]) expect(isEnumerator([w])).toEqual([false]);
  });
});

describe("wordDiff", () => {
  it("identical texts: equivalent, no removed or added words, one same run per side", () => {
    const d = wordDiff(OLD, OLD);
    expect(d.equivalent).toBe(true);
    expect(d.removedWords).toBe(0);
    expect(d.addedWords).toBe(0);
    expect(d.before).toEqual([{ kind: "same", text: OLD }]);
    expect(d.after).toEqual([{ kind: "same", text: OLD }]);
  });

  it("texts that differ only in typography or spacing are equivalent and show no change", () => {
    const d = wordDiff("FAKE the Board\u2019s  policy \u2013 one day", "FAKE the Board's policy - one\nday");
    expect(d.equivalent).toBe(true);
    expect(d.removedWords + d.addedWords).toBe(0);
    // Each side keeps its own spelling.
    expect(join(d.before)).toBe("FAKE the Board\u2019s policy \u2013 one day");
    expect(join(d.after)).toBe("FAKE the Board's policy - one day");
  });

  it("marks exactly the replaced words and keeps the shared ones", () => {
    const d = wordDiff(OLD, NEW);
    expect(d.equivalent).toBe(false);
    // The differing middles are "Board of the RE as laid down in their loan" (10 words) and "NBFC in terms of their
    // credit" (6 words); their longest common subsequence is 2 words ("in their" or, equally long, "of their").
    expect(d.removedWords).toBe(10 - 2);
    expect(d.addedWords).toBe(6 - 2);
    const removed = only(d.before, "removed").join(" ");
    const added = only(d.after, "added").join(" ");
    for (const w of ["Board", "RE", "laid", "loan"]) expect(removed).toContain(w);
    for (const w of ["NBFC", "terms", "credit"]) expect(added).toContain(w);
    // The shared prefix and suffix are never marked.
    expect(d.before[0]).toEqual({ kind: "same", text: "FAKE cooling off period shall be determined by the" });
    expect(d.after.at(-1)).toEqual({ kind: "same", text: "policy, subject to one day." });
    // Each side reconstructs its own text word for word.
    expect(join(d.before)).toBe(words(OLD).join(" "));
    expect(join(d.after)).toBe(words(NEW).join(" "));
    // Neither side carries the other side's kind.
    expect(d.before.some((p) => p.kind === "added")).toBe(false);
    expect(d.after.some((p) => p.kind === "removed")).toBe(false);
  });

  it("common words within the aligned stretch are a longest common subsequence; old words past it are outside", () => {
    const d = wordDiff("a b c d e", "b x d e a");
    const common = only(d.after, "same").join(" ").split(" ").length;
    expect(common).toBe(3); // b d e
    expect(d.sharedWords).toBe(common);
    // Old "a" lies before the stretch the new text lines up with: not compared, never called removed.
    expect(d.before[0]).toEqual({ kind: "outside", text: "a" });
    expect(d.removedWords + d.outsideWords).toBe(5 - common);
    expect(d.addedWords).toBe(5 - common);
  });

  it("handles empty sides: an empty passage in force compares nothing and removes nothing", () => {
    expect(wordDiff("", "FAKE new")).toMatchObject({ before: [], after: [{ kind: "added", text: "FAKE new" }], addedWords: 2, removedWords: 0 });
    expect(wordDiff("FAKE old", "")).toMatchObject({ before: [{ kind: "outside", text: "FAKE old" }], after: [], removedWords: 0, addedWords: 0, outsideWords: 2 });
    expect(wordDiff("", "").equivalent).toBe(true);
  });

  it("past the cell cap, claims nothing instead of blocking: every word unmarked, no counts", () => {
    const side = Math.ceil(Math.sqrt(MAX_LCS_CELLS)) + 1;
    const a = Array.from({ length: side }, (_, i) => `a${i}`).join(" ");
    const b = Array.from({ length: side }, (_, i) => `b${i}`).join(" ");
    const d = wordDiff(`FAKE ${a} end`, `FAKE ${b} end`);
    expect(d.tooLong).toBe(true);
    expect(d.removedWords + d.addedWords).toBe(0);
    expect(d.before).toEqual([{ kind: "outside", text: `FAKE ${a} end` }]);
    expect(d.after).toEqual([{ kind: "outside", text: `FAKE ${b} end` }]);
    expect(verdict(d)).toBe(DIFF_TEXT.tooLong);
  });
});

describe("wordDiff on the production shape: whole old paragraph against a quoted passage in force", () => {
  const removedWords = (parts: readonly DiffPart[]): string[] => only(parts, "removed").join(" ").split(" ").filter(Boolean);

  for (const [name, excerpt] of [
    ["first sentence", EXCERPT_FIRST],
    ["two sentences", EXCERPT_TWO],
    ["three sentences", EXCERPT_THREE],
  ] as const) {
    it(`${name}: no old word outside the passage is struck, and the processing-fee sentence is never removed`, () => {
      const d = wordDiff(OLD_PARA, excerpt);
      expect(d.tooLong).toBe(false);
      // Every word of both texts is shown, in order, on its own side.
      expect(join(d.before)).toBe(words(OLD_PARA).join(" "));
      expect(join(d.after)).toBe(words(excerpt).join(" "));
      // The processing-fee provision is still in force in the new directions; the panel must not say RBI removed it.
      expect(only(d.before, "outside").join(" ")).toContain(PROCESSING_FEE);
      for (const w of ["processing", "one-time", "KFS."]) expect(removedWords(d.before)).not.toContain(w);
      // Old words outside the aligned stretch are at its two ends only: the aligned part is one contiguous run.
      const kinds = d.before.map((p) => (p.kind === "outside" ? "o" : "i")).join("");
      expect(kinds).toMatch(/^o?i+o?$/);
      const numbering = (parts: readonly DiffPart[]): number => words(only(parts, "numbering").join(" ")).length;
      expect(d.removedWords + d.outsideWords + d.sharedWords + numbering(d.before)).toBe(words(OLD_PARA).length);
      expect(d.addedWords + d.sharedWords + numbering(d.after)).toBe(words(excerpt).length);
      expect(numbering(d.before) + numbering(d.after)).toBe(d.numberingWords);
      // "(1)" opens the excerpt: numbering, never counted as added.
      expect(only(d.after, "added").join(" ")).not.toContain("(1)");
    });
  }

  it("a word-for-word excerpt under new numbering reads as the same passage: the numbers are not compared", () => {
    const d = wordDiff(OLD_PARA, EXCERPT_FIRST);
    expect(d.removedWords + d.addedWords).toBe(0);
    // "(1)" opens the excerpt, "10." the old paragraph: each is numbering on its own side, never added or removed.
    // "(1)" opens the excerpt: numbering, never an added word. Old "10." opens the heading outside the stretch, so it
    // is greyed with it.
    expect(d.after[0]).toEqual({ kind: "numbering", text: "(1)" });
    expect(d.numberingWords).toBe(1);
    expect(d.before[0]).toEqual({ kind: "outside", text: "10. FAKE Cooling-off period i." });
    expect(d.addedWords + d.sharedWords + d.numberingWords).toBe(words(EXCERPT_FIRST).length);
    expect(join(d.after)).toBe(words(EXCERPT_FIRST).join(" "));
    expect(join(d.before)).toBe(words(OLD_PARA).join(" "));
    expect(verdict(d)).toBe(DIFF_TEXT.samePassage);
  });

  it("the live hero finding (d38710fb c2): old para 10 against excerpt of para 11(1) reads as the same passage", () => {
    const sentence =
      "The borrower shall be given an explicit option to exit a digital loan by paying the principal and the proportionate APR without any penalty during an initial \u201Ccooling-off period\u201D.";
    const old = `10. Cooling-off period i. ${sentence} The cooling off period shall be determined by the Board of the RE as laid down in their loan policy, subject to one day.`;
    const d = wordDiff(old, `(1) ${sentence}`);
    expect(d.addedWords).toBe(0);
    expect(d.removedWords).toBe(0);
    expect(only(d.after, "added")).toEqual([]);
    expect(verdict(d)).toBe(DIFF_TEXT.samePassage);
  });

  it("a number that opens the compared stretch on either side is numbering, on both sides", () => {
    const d = wordDiff(`10. ${OLD}`, `(1) ${NEW}`);
    expect(d.before[0]).toEqual({ kind: "numbering", text: "10." });
    expect(d.after[0]).toEqual({ kind: "numbering", text: "(1)" });
    expect(d.numberingWords).toBe(2);
    expect(d.removedWords).toBe(wordDiff(OLD, NEW).removedWords);
    expect(d.addedWords).toBe(wordDiff(OLD, NEW).addedWords);
  });

  it("numbering on both sides of identical texts: equivalent, nothing counted", () => {
    const d = wordDiff(`(1) ${OLD}`, `(a) ${OLD}`);
    expect(d.equivalent).toBe(true);
    expect(d.removedWords + d.addedWords).toBe(0);
    expect(verdict(d)).toBe(DIFF_TEXT.same);
  });

  it("an excerpt without its numbering, found verbatim in the old paragraph, reads as the same passage", () => {
    const d = wordDiff(OLD_PARA, EXCERPT_FIRST.replace("(1) ", ""));
    expect(d.removedWords + d.addedWords).toBe(0);
    expect(d.outsideWords).toBeGreaterThan(0);
    expect(d.equivalent).toBe(false);
    expect(verdict(d)).toBe(DIFF_TEXT.samePassage);
  });

  it("inside the aligned stretch, the replaced actor and policy are marked as before", () => {
    const d = wordDiff(OLD_PARA, EXCERPT_TWO);
    const removed = only(d.before, "removed").join(" ");
    const added = only(d.after, "added").join(" ");
    for (const w of ["Board", "RE", "laid", "loan"]) expect(removed).toContain(w);
    for (const w of ["NBFC", "terms", "credit"]) expect(added).toContain(w);
    expect(verdict(d)).toBe(changeSummary(d.removedWords, d.addedWords));
  });

  it("a far-off common word does not drag the stretch across the rest of the paragraph", () => {
    // "the" ends the excerpt and recurs late in the old paragraph; reaching it would strike the provisions between.
    const d = wordDiff(OLD_PARA, `${EXCERPT_FIRST} FAKE the`);
    for (const w of ["processing", "Board", "pre-payment"]) expect(removedWords(d.before)).not.toContain(w);
  });

  it("aligns with the later of two candidate starts when the earlier one would strike words between", () => {
    const d = wordDiff("The FAKE x y z w v The D E", "The D E");
    expect(d.removedWords).toBe(0);
    expect(d.before.at(-1)).toEqual({ kind: "same", text: "The D E" });
  });

  it("no shared wording: says so, and strikes nothing", () => {
    const d = wordDiff(OLD_PARA, "Unrelated words entirely (FAKE-free).");
    expect(d.removedWords).toBe(0);
    expect(verdict(d)).toBe(DIFF_TEXT.noOverlap);
  });
});
