// "What changed" panel under the outdated-reference chain: the cited (withdrawn) text next to the passage quoted from
// the text in force now, with word-level differences marked. Built only from the two recorded citations. In
// production the cited side is the whole old paragraph and the side in force is only a quoted passage, so the old
// words outside the stretch that lines up with that passage are greyed as not compared, never struck as removed;
// paragraph numbers on either side are greyed as numbering, never counted as a change;
// the panel never says more than the words show (see diff.ts).
import type { ReactNode } from "react";
import type { OutdatedChain } from "../data/selectors";
import type { Citation } from "../data/types";
import { citationLibrary, libraryLabel } from "../shell/format";
import { DIFF_TEXT, verdict, wordDiff, type DiffPart } from "./diff";
import { paraLabel } from "./view";
import "./DiffView.css";

interface Props {
  /** The chain of an outdated-reference finding; the panel needs the current paragraph to compare against. */
  chain: OutdatedChain & { current: Citation };
}

export function DiffView({ chain }: Props): ReactNode {
  const { cited, withdrawal, current } = chain;
  const diff = wordDiff(cited.quote, current.quote);
  return (
    <details className="diff-view">
      <summary className="diff-summary">{DIFF_TEXT.title}</summary>
      <div className="diff-body">
        <p className="diff-verdict">
          {verdict(diff)}
          {!diff.equivalent && (
            <>
              {" "}
              <span className="text-muted">{DIFF_TEXT.scope}</span>
            </>
          )}
        </p>
        <div className="diff-columns">
          <section className="diff-side diff-old" aria-label={`${withdrawal ? DIFF_TEXT.oldHeading : DIFF_TEXT.oldHeadingReplaced}, ${paraLabel(cited)}`}>
            <h4 className="diff-side-head">{withdrawal ? DIFF_TEXT.oldHeading : DIFF_TEXT.oldHeadingReplaced}</h4>
            <p className="diff-source">
              {cited.instrument} <span className="mono">{paraLabel(cited)}</span>
            </p>
            <Dates citation={cited} />
            <blockquote className="diff-quote">
              <Parts parts={diff.before} />
            </blockquote>
          </section>
          <section className="diff-side diff-new" aria-label={`${DIFF_TEXT.newHeading}, ${paraLabel(current)}`}>
            <h4 className="diff-side-head">{DIFF_TEXT.newHeading}</h4>
            <p className="diff-source">
              {current.instrument} <span className="mono">{paraLabel(current)}</span>
            </p>
            <Dates citation={current} />
            <blockquote className="diff-quote">
              <Parts parts={diff.after} />
            </blockquote>
          </section>
        </div>
        {(diff.removedWords > 0 || diff.addedWords > 0 || diff.outsideWords > 0 || diff.numberingWords > 0) && (
          <ul className="diff-legend">
            {diff.removedWords > 0 && (
              <li>
                <del>struck</del> {DIFF_TEXT.legendRemoved}
              </li>
            )}
            {diff.addedWords > 0 && (
              <li>
                <ins>highlighted</ins> {DIFF_TEXT.legendAdded}
              </li>
            )}
            {diff.outsideWords > 0 && !diff.tooLong && (
              <li>
                <span className="diff-outside">greyed</span> {DIFF_TEXT.legendOutside}
              </li>
            )}
            {diff.numberingWords > 0 && (
              <li>
                <span className="diff-numbering">greyed</span> {DIFF_TEXT.legendNumbering}
              </li>
            )}
          </ul>
        )}
      </div>
    </details>
  );
}

/** The library dates of a citation, worded as the source pane words them: for the withdrawn side, when and by which
 * circular it was withdrawn; for the side in force, RBI's update date; and the date Argus fetched the text. */
function Dates({ citation }: { citation: Citation }): ReactNode {
  return <p className="diff-dates text-muted">{libraryLabel(citationLibrary(citation))}</p>;
}

function Parts({ parts }: { parts: readonly DiffPart[] }): ReactNode {
  return parts.map((p, i) => {
    const sep = i < parts.length - 1 ? " " : "";
    if (p.kind === "removed") {
      return (
        <span key={i}>
          <del className="diff-removed">
            <span className="visually-hidden">{DIFF_TEXT.removedLabel}</span>
            {p.text}
          </del>
          {sep}
        </span>
      );
    }
    if (p.kind === "outside") {
      return (
        <span key={i}>
          <span className="diff-outside">
            <span className="visually-hidden">{DIFF_TEXT.outsideLabel}</span>
            {p.text}
          </span>
          {sep}
        </span>
      );
    }
    if (p.kind === "numbering") {
      return (
        <span key={i}>
          <span className="diff-numbering">
            <span className="visually-hidden">{DIFF_TEXT.numberingLabel}</span>
            {p.text}
          </span>
          {sep}
        </span>
      );
    }
    if (p.kind === "added") {
      return (
        <span key={i}>
          <ins className="diff-added">
            <span className="visually-hidden">{DIFF_TEXT.addedLabel}</span>
            {p.text}
          </ins>
          {sep}
        </span>
      );
    }
    return (
      <span key={i}>
        {p.text}
        {sep}
      </span>
    );
  });
}
