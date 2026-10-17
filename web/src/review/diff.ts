// Word-level diff for the "What changed" panel. No React, no Firestore: the diff is tested in diff.test.ts, the
// wording in DiffView.test.tsx.
//
// The two texts are not alike in production. The old side is the whole cited paragraph, verbatim
// (agent/loop.py replaced_citation); the new side is only the passage the agent quoted from the paragraph in force
// (agent/loop.py citation), a contiguous excerpt of it. So the diff is semi-global: the new passage is aligned in
// full against the stretch of the old paragraph it lines up with, and the old words before and after that stretch
// are "outside", shown but never marked removed, because the panel cannot know whether the paragraph in force
// keeps them beyond the quoted passage.
//
// Paragraph numbering is not wording. The excerpt in force usually opens with its own number ("(1)") and the old
// paragraph with its own ("10."); the two never match, and an unmatched number would read as an added or removed word
// when RBI changed nothing. So enumerators (see `isEnumerator`) are left out of the alignment and the counts, and
// shown on their own side as "numbering", greyed and labelled not compared; an old number that opens words outside
// the aligned stretch ("ii." before the processing-fee sentence) is simply part of that outside run.
import { plural } from "./view";

// ---------------------------------------------------------------- panel wording

export const DIFF_TEXT = {
  title: "What changed",
  same: "Same wording; only the citation is outdated.",
  samePassage: "The quoted passage in force reads the same as the matching part of the cited text.",
  noOverlap: "The quoted passage in force shares no wording with the cited text.",
  tooLong: "These texts are too long to compare word by word here.",
  scope: "Compares the cited text with the passage quoted from the text in force, not with the whole paragraph in force.",
  legendRemoved: "Struck through: words of the cited text that the quoted passage in force does not have at that point.",
  legendAdded: "Underlined and highlighted: words of the quoted passage in force that the cited text does not have at that point.",
  legendOutside: "Greyed: words of the cited text outside the stretch that lines up with the quoted passage; not compared.",
  legendNumbering: "Greyed: paragraph and sub-paragraph numbers, such as (1) or 10.; not compared.",
  removedLabel: "removed: ",
  addedLabel: "added: ",
  outsideLabel: "not compared: ",
  numberingLabel: "numbering, not compared: ",
  oldHeading: "Policy cites (withdrawn)",
  oldHeadingReplaced: "Policy cites",
  newHeading: "Now in force",
} as const;

/** Summary line for a diff that is not equivalent: counts derived from the diff itself. */
export function changeSummary(removedWords: number, addedWords: number): string {
  return `Wording differs: ${plural(removedWords, "word")} removed, ${plural(addedWords, "word")} added.`;
}

/** The verdict line for a diff: the one sentence the panel may say about it, and no more than the words show. */
export function verdict(d: WordDiff): string {
  if (d.tooLong) return DIFF_TEXT.tooLong;
  if (d.equivalent) return DIFF_TEXT.same;
  if (d.sharedWords === 0) return DIFF_TEXT.noOverlap;
  if (d.removedWords === 0 && d.addedWords === 0) return DIFF_TEXT.samePassage;
  return changeSummary(d.removedWords, d.addedWords);
}

// ---------------------------------------------------------------- diff

/** "outside": old words beyond the stretch aligned with the new passage, or (past the cell cap) every word.
 * "numbering": a paragraph or sub-paragraph enumerator on either side, never compared or counted. */
export type DiffKind = "same" | "removed" | "added" | "outside" | "numbering";

/** A run of consecutive words of one kind, joined by single spaces, in their original spelling. */
export interface DiffPart {
  kind: DiffKind;
  text: string;
}

export interface WordDiff {
  /** The old text's words: "outside", "same" and "removed" runs, in order. */
  before: DiffPart[];
  /** The new text's words: "same", "added" and "numbering" runs, in order ("outside" only when `tooLong`). */
  after: DiffPart[];
  removedWords: number;
  addedWords: number;
  /** Old words not compared: before or after the stretch that lines up with the new passage. */
  outsideWords: number;
  /** Words matched between the two sides. */
  sharedWords: number;
  /** Enumerators on both sides together, left out of the comparison (see `isEnumerator`). */
  numberingWords: number;
  /** True when the two texts are identical word for word after normalisation (see `normalise`), numbering aside. */
  equivalent: boolean;
  /** True when the texts were too long to align; every word is then "outside" and nothing is claimed. */
  tooLong: boolean;
}

/** Above this many alignment cells (old words times new words), the diff would
 * block the UI thread; the longest RBI library paragraph is about 520 words, far below it. Past it, nothing is
 * claimed: both texts are shown unmarked as "outside". */
export const MAX_LCS_CELLS = 1_000_000;

// Typographic variants that the RBI pages mix freely and that never change the wording.
const FOLD: Record<string, string> = {
  "\u2018": "'",
  "\u2019": "'",
  "\u201C": '"',
  "\u201D": '"',
  "\u2013": "-",
  "\u2014": "-",
  "\u00A0": " ",
};
const FOLD_RE = /[\u2018\u2019\u201C\u201D\u2013\u2014\u00A0]/g;

/** NFC, curly quotes and en/em dashes folded to ASCII, whitespace collapsed, trimmed. Case and punctuation kept. */
export function normalise(text: string): string {
  return text
    .normalize("NFC")
    .replace(FOLD_RE, (c) => FOLD[c] ?? c)
    .replace(/\s+/g, " ")
    .trim();
}

/** Words in original spelling (NFC only), split on any whitespace. */
export function words(text: string): string[] {
  const t = text.normalize("NFC").trim();
  return t === "" ? [] : t.split(/\s+/);
}

/** True when the two texts read the same after `normalise`. */
export function sameWording(a: string, b: string): boolean {
  return normalise(a) === normalise(b);
}

type Op = { kind: DiffKind; word: string };

// One enumerator token: "(1)", "(a)", "(iv)", "10.", "11.2.", "i.", "a)", "IV.".
const ENUMERATOR_RE =
  /^(?:\((?:\d{1,3}|[ivxlc]{1,7}|[IVXLC]{1,7}|[a-zA-Z])\)|(?:\d{1,3}(?:\.\d{1,3})*|[ivxlc]{1,7}|[IVXLC]{1,7}|[a-zA-Z])[.)])$/;

/** Which words are paragraph numbering: an enumerator token at the start of the text, right after another
 * enumerator, or right after a word that closes a sentence or list item (". ; :"). Elsewhere the same token is
 * prose ("under sub-paragraph (a)", "as per para 10.") and is compared like any other word. */
export function isEnumerator(ws: readonly string[]): boolean[] {
  const out: boolean[] = [];
  for (let i = 0; i < ws.length; i++) {
    const prev = i === 0 ? undefined : ws[i - 1];
    const atBoundary = prev === undefined || out[i - 1] === true || /[.;:]$/.test(prev);
    out.push(atBoundary && ENUMERATOR_RE.test(at(ws, i)));
  }
  return out;
}

/** Index that the caller has bounds-checked: a miss is a bug in this file, not a data condition. */
function at<T>(xs: ArrayLike<T>, i: number): T {
  const v = xs[i];
  if (v === undefined) throw new RangeError(`diff: index ${String(i)} out of range`);
  return v;
}

// Alignment scores: a shared word earns MATCH, an added word costs ADDED, a removed word costs REMOVED. Old words
// before the first and after the last aligned word cost nothing (they are "outside"). Within one stretch the best
// alignment is a longest common subsequence; the scores only decide how far the stretch reaches. Reaching one more
// shared word gains MATCH - ADDED (it was an added word) and costs REMOVED per unmatched old word crossed: worth
// crossing up to five, so a replaced phrase ("Board of the RE as laid down in their loan policy" -> "NBFC in terms of
// their credit policy") stays inside the stretch, while a lone common word ("the") further off does not drag the
// stretch across the provisions in between. Powers of two keep the float sums exact for the traceback's equalities.
const MATCH = 2;
const ADDED = -1;
const REMOVED = -0.5;

/** Semi-global alignment of all of `b` against the stretch of `a` it lines up with best; old words before and after
 * that stretch are "outside". No shared prefix or suffix is trimmed first: matching a shared first word is not
 * always best when the start is free (old "The X Y Z ... The D E" against new "The D E" aligns with the second "The"). */
function alignOps(a: readonly string[], b: readonly string[], ka: readonly string[], kb: readonly string[]): Op[] {
  const n = a.length;
  const m = b.length;
  const w = m + 1;
  // s[i*w+j]: best score aligning b[0..j) against a stretch ending just before a[i].
  const s = new Float64Array((n + 1) * w);
  for (let j = 1; j <= m; j++) s[j] = ADDED * j;
  for (let i = 1; i <= n; i++) {
    s[i * w] = 0;
    for (let j = 1; j <= m; j++) {
      const diag = ka[i - 1] === kb[j - 1] ? at(s, (i - 1) * w + j - 1) + MATCH : -Infinity;
      s[i * w + j] = Math.max(diag, at(s, (i - 1) * w + j) + REMOVED, at(s, i * w + j - 1) + ADDED);
    }
  }
  // Where the stretch ends in `a`: the best score, the earliest on a tie.
  let end = 0;
  for (let i = 1; i <= n; i++) if (at(s, i * w + m) > at(s, end * w + m)) end = i;
  const rev: Op[] = [];
  for (let k = n - 1; k >= end; k--) rev.push({ kind: "outside", word: at(a, k) });
  let i = end;
  let j = m;
  while (j > 0 || i > 0) {
    if (j === 0) {
      rev.push({ kind: "outside", word: at(a, i - 1) });
      i--;
    } else if (i === 0) {
      rev.push({ kind: "added", word: at(b, j - 1) });
      j--;
    } else if (at(s, i * w + j) === at(s, i * w + j - 1) + ADDED) {
      // Walking backwards, a tie goes to a gap, so that forwards shared words match as early as they can; and to an
      // added word before a removed one, so that forwards each change reads removed, then added.
      rev.push({ kind: "added", word: at(b, j - 1) });
      j--;
    } else if (at(s, i * w + j) === at(s, (i - 1) * w + j) + REMOVED) {
      rev.push({ kind: "removed", word: at(a, i - 1) });
      i--;
    } else {
      rev.push({ kind: "same", word: at(b, j - 1) });
      i--;
      j--;
    }
  }
  return rev.reverse();
}

function merge(ops: readonly Op[]): DiffPart[] {
  const parts: DiffPart[] = [];
  for (const op of ops) {
    const last = parts.at(-1);
    if (last && last.kind === op.kind) last.text += ` ${op.word}`;
    else parts.push({ kind: op.kind, text: op.word });
  }
  return parts;
}

const tally = (ops: readonly Op[], kind: DiffKind): number => ops.filter((op) => op.kind === kind).length;

/** Word diff of `before` (the cited text: in production the whole old paragraph) against `after` (the passage quoted
 * from the text in force). Words compare equal when their normalised forms do. */
export function wordDiff(before: string, after: string): WordDiff {
  const a = words(before);
  const b = words(after);
  const ka = a.map(normalise);
  const kb = b.map(normalise);

  if (a.length * b.length > MAX_LCS_CELLS) {
    return {
      before: merge(a.map((word): Op => ({ kind: "outside", word }))),
      after: merge(b.map((word): Op => ({ kind: "outside", word }))),
      removedWords: 0,
      addedWords: 0,
      outsideWords: a.length,
      sharedWords: 0,
      numberingWords: 0,
      equivalent: false,
      tooLong: true,
    };
  }

  // Numbering is left out of the alignment; `ia`/`ib` map the compared words back to their place in each text.
  const numA = isEnumerator(a);
  const numB = isEnumerator(b);
  const ia = a.flatMap((_, i) => (numA[i] ? [] : [i]));
  const ib = b.flatMap((_, i) => (numB[i] ? [] : [i]));
  const pick = <T>(xs: readonly T[], idx: readonly number[]): T[] => idx.map((i) => at(xs, i));
  const ops = alignOps(pick(a, ia), pick(b, ib), pick(ka, ia), pick(kb, ib));

  // Each side is rebuilt in its own order and spelling: the ops of that side's compared words, with that side's
  // numbering put back where it stands.
  // A number belongs to the item it opens: when the next compared word (or, at the end, the last one) is outside the
  // aligned stretch, so is the number.
  const side = (ws: readonly string[], num: readonly boolean[], kinds: readonly DiffKind[]): Op[] => {
    const out: Op[] = [];
    let k = 0;
    ws.forEach((word, i) => {
      if (!num[i]) {
        out.push({ kind: at(kinds, k++), word });
        return;
      }
      const owner = k < kinds.length ? kinds[k] : kinds[k - 1];
      out.push({ kind: owner === "outside" ? "outside" : "numbering", word });
    });
    return out;
  };
  const beforeOps = side(a, numA, ops.filter((op) => op.kind !== "added").map((op) => op.kind));
  const afterOps = side(b, numB, ops.filter((op) => op.kind === "same" || op.kind === "added").map((op) => op.kind));

  const removedWords = tally(ops, "removed");
  const addedWords = tally(ops, "added");
  // Counted on the old side, so that an old number greyed with the words it opens counts as outside too.
  const outsideWords = tally(beforeOps, "outside");
  return {
    before: merge(beforeOps),
    after: merge(afterOps),
    removedWords,
    addedWords,
    outsideWords,
    sharedWords: tally(ops, "same"),
    numberingWords: tally(beforeOps, "numbering") + tally(afterOps, "numbering"),
    equivalent: removedWords === 0 && addedWords === 0 && outsideWords === 0,
    tooLong: false,
  };
}
