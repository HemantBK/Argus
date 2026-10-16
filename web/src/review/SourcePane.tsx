// Right pane (UI_SPEC 3.2): the RBI source behind the selected citation. Instrument, official paragraph number
// (monospace), the verbatim quote, the snapshot date, a short content hash and a link to the RBI page. A
// withdrawn instrument gets a Withdrawn banner with the withdrawing circular, its date and the replacement.
import type { ReactNode } from "react";
import type { Citation, FindingDoc } from "../data/types";
import { HASH_EXPLAINED, formatIsoDate, paraLabel, shortHash, sourceLinkLabel } from "./view";

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
  return "No RBI paragraph supports a conclusion for this clause. Argus does not guess; a person must review it.";
}

function SourceBody({ citation, replacement, onShowReplacement }: Omit<Props, "finding" | "citation"> & { citation: Citation }): ReactNode {
  const withdrawn = !citation.in_force;
  return (
    <div className="source">
      {withdrawn && (
        <div className="withdrawn-banner" role="note">
          <p className="withdrawn-title">
            <span aria-hidden="true">⟲ </span>Withdrawn
          </p>
          <p>
            {citation.withdrawn_by ? (
              <>
                Withdrawn by <span className="mono">{citation.withdrawn_by}</span>
              </>
            ) : (
              "Withdrawn; the withdrawing circular is not recorded in the library"
            )}
            {citation.withdrawn_on && <> on {formatIsoDate(citation.withdrawn_on)}</>}. It is not in force.
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
        <dt>Snapshot taken</dt>
        <dd>{formatIsoDate(citation.snapshot_date)}</dd>
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
