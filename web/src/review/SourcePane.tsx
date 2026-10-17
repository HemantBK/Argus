// Right pane (UI_SPEC 3.2): the RBI source behind the selected citation. Instrument, official paragraph number
// (monospace), the verbatim quote, the library dates (libraryLabel: RBI's update date and the fetch date), a short
// content hash and a link to the RBI page. A withdrawn instrument gets a Withdrawn banner with the withdrawing
// circular, its date and the replacement.
import type { ReactNode } from "react";
import { withdrawalOf } from "../data/convert";
import type { Citation, FindingDoc } from "../data/types";
import { citationLibrary, libraryLabel } from "../shell/format";
import { HASH_EXPLAINED, NO_VERIFIED_CITATION, formatIsoDate, paraLabel, shortHash, sourceLinkLabel } from "./view";

interface Props {
  finding: FindingDoc | null;
  citation: Citation | null;
  /** The paragraph in force that replaces `citation`, when `citation` is the withdrawn one the policy cites. */
  replacement: Citation | null;
  onShowReplacement: () => void;
}

export function SourcePane({ finding, citation, replacement, onShowReplacement }: Props): ReactNode {
  return (
    <aside className="pane pane-source" aria-labelledby="source-heading">
      <h2 id="source-heading" className="pane-title">
        RBI source
      </h2>
      {citation ? (
        <SourceBody citation={citation} replacement={replacement} onShowReplacement={onShowReplacement} />
      ) : (
        <p className="text-muted pane-note">{emptyText(finding)}</p>
      )}
    </aside>
  );
}

function emptyText(finding: FindingDoc | null): string {
  if (!finding) return "The RBI text appears here once the clause has a finding.";
  if (finding.error) return "No RBI paragraph: the review of this clause did not complete.";
  return NO_VERIFIED_CITATION;
}

function SourceBody({ citation, replacement, onShowReplacement }: Omit<Props, "finding" | "citation"> & { citation: Citation }): ReactNode {
  const withdrawal = withdrawalOf(citation);
  return (
    <div className="source">
      {withdrawal && (
        <div className="withdrawn-banner" role="note">
          <p className="withdrawn-title">
            <span aria-hidden="true">⟲ </span>Withdrawn
          </p>
          <p>
            Withdrawn by <span className="mono">{withdrawal.by}</span> on {formatIsoDate(withdrawal.on)}. It is not in force.
          </p>
          {replacement && (
            <button type="button" className="link-button" onClick={onShowReplacement}>
              Replaced by {replacement.instrument}, <span className="mono">{paraLabel(replacement)}</span>
            </button>
          )}
        </div>
      )}

      <p className="source-instrument">{citation.instrument}</p>
      <p className="source-ref">
        <span className="mono">{citation.instrument_ref}</span> · <span className="mono">{paraLabel(citation)}</span>
      </p>

      <blockquote className="source-quote">
        <mark>{citation.quote}</mark>
      </blockquote>

      <dl className="source-facts">
        <dt>Dates</dt>
        <dd className="source-dates">{libraryLabel(citationLibrary(citation))}</dd>
        <dt>Content hash</dt>
        <dd>
          <span className="mono" title={`${citation.content_hash}\n${HASH_EXPLAINED}`}>
            {shortHash(citation.content_hash)}
          </span>
          <span className="visually-hidden"> {HASH_EXPLAINED}</span>
        </dd>
      </dl>

      <a className="source-link" href={citation.url} target="_blank" rel="noopener noreferrer">
        {sourceLinkLabel(citation.url)}
        <span aria-hidden="true"> ↗</span>
        <span className="visually-hidden"> (opens in a new tab)</span>
      </a>
    </div>
  );
}
