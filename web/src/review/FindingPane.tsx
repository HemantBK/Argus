// Middle pane (UI_SPEC 3.2): the clause text verbatim, then the finding card: type chip, rationale, citation chip,
// monitor badge, the outdated-reference chain as three boxes, and the officer's decision.
import type { ReactNode } from "react";
import { FAILED_CHIP, chipFor, outdatedChain, type Badge, type ClauseRow, type InjectionCatch, type OutdatedChain } from "../data/selectors";
import type { Citation, FindingDoc, StepDoc } from "../data/types";
import { MonitorBadge } from "./MonitorBadge";
import { SignoffActions, type SubmitSignoff } from "./SignoffActions";
import { Spinner, StatusChip } from "./StatusChip";
import { formatIsoDate, highlight, paraLabel, type SignoffAvailability } from "./view";

export type CitationChoice = "current" | "replaced";

interface Props {
  row: ClauseRow | null;
  badge: Badge | null;
  injection: InjectionCatch | null;
  steps: readonly StepDoc[];
  availability: SignoffAvailability;
  onSubmit: SubmitSignoff;
  shown: CitationChoice;
  onShowCitation: (which: CitationChoice) => void;
  /** The clause list is still loading (no row can be selected yet). */
  loading?: boolean;
}

export function FindingPane({ row, badge, injection, steps, availability, onSubmit, shown, onShowCitation, loading = false }: Props): ReactNode {
  if (!row) {
    return (
      <section className="pane pane-finding" aria-labelledby="finding-heading">
        <h2 id="finding-heading" className="pane-title">
          Clause
        </h2>
        <p className="text-muted pane-note">{loading ? "Loading the clauses…" : "Select a clause to see its finding."}</p>
      </section>
    );
  }
  const { finding } = row;
  return (
    <section className="pane pane-finding" aria-labelledby="finding-heading">
      <h2 id="finding-heading" className="pane-title">
        Clause <span className="mono">{row.clauseId}</span>
        {row.title !== row.clauseId && <span className="pane-subtitle"> · {row.title}</span>}
      </h2>
      <p className="clause-text">
        {highlight(row.text, injection?.quote ?? null).map((part, i) =>
          part.mark ? (
            <mark key={i} className="mark-injection">
              {part.text}
            </mark>
          ) : (
            <span key={i}>{part.text}</span>
          ),
        )}
      </p>

      {finding ? (
        <FindingCard
          key={finding.clause_id /* fresh badge and sign-off state per clause */}
          finding={finding}
          badge={badge}
          injection={injection}
          steps={steps}
          availability={availability}
          onSubmit={onSubmit}
          shown={shown}
          onShowCitation={onShowCitation}
        />
      ) : (
        <PendingCard row={row} />
      )}
    </section>
  );
}

function FindingCard({
  finding,
  badge,
  injection,
  steps,
  availability,
  onSubmit,
  shown,
  onShowCitation,
}: Omit<Props, "row" | "loading"> & { finding: FindingDoc }): ReactNode {
  const chain = outdatedChain(finding);
  const edited = finding.signoff?.decision === "edit" ? (finding.signoff.edited_rationale ?? null) : null;
  return (
    <article className="finding-card" aria-labelledby="finding-card-title">
      <h3 id="finding-card-title" className="finding-head">
        <span className="finding-kicker">Finding</span>
        <StatusChip chip={chipFor(finding)} />
      </h3>

      {finding.error ? (
        <div className="failed-note">
          <p>
            <strong>Couldn't complete.</strong> The review of this clause did not finish, so Argus concluded nothing. A
            person must review it.
          </p>
          <p className="text-muted">Recorded error: {finding.error}</p>
        </div>
      ) : edited !== null ? (
        <div className="rationale-pair">
          <p>
            <span className="rationale-label">Officer's rationale</span>
            {edited}
          </p>
          <p className="rationale-original">
            <span className="rationale-label">Argus's original rationale</span>
            {finding.rationale}
          </p>
        </div>
      ) : (
        <p className="rationale">{finding.rationale}</p>
      )}

      {chain && <ChainView chain={chain} shown={shown} onShow={onShowCitation} />}

      {finding.citation && (
        <p className="cites">
          Cites:{" "}
          <CitationChip
            citation={finding.citation}
            active={shown === "current"}
            onClick={() => {
              onShowCitation("current");
            }}
          />
        </p>
      )}

      {badge ? (
        <MonitorBadge badge={badge} finding={finding} steps={steps} injection={injection} />
      ) : (
        <p className="text-muted badge-loading">
          <Spinner label="Loading monitor verdicts" /> Loading the monitor's recorded verdicts…
        </p>
      )}

      <SignoffActions finding={finding} availability={availability} onSubmit={onSubmit} />
    </article>
  );
}

function CitationChip({ citation, active, onClick }: { citation: Citation; active: boolean; onClick: () => void }): ReactNode {
  return (
    <button type="button" className={`citation-chip${active ? " is-active" : ""}`} aria-pressed={active} onClick={onClick}>
      <span className="mono">{paraLabel(citation)}</span>
      <span className="citation-chip-instrument">{citation.instrument}</span>
      <span className="visually-hidden"> (show the RBI text in the source pane)</span>
    </button>
  );
}

/** Policy cites → withdrawn → now: three boxes, left to right, each opening its source (UI_SPEC 3.2 hero moment). */
function ChainView({ chain, shown, onShow }: { chain: OutdatedChain; shown: CitationChoice; onShow: (w: CitationChoice) => void }): ReactNode {
  const { cited, withdrawal, current } = chain;
  return (
    <ol className="chain" aria-label="Outdated reference chain">
      <li>
        <button
          type="button"
          className={`chain-box chain-old${shown === "replaced" ? " is-active" : ""}`}
          aria-pressed={shown === "replaced"}
          onClick={() => {
            onShow("replaced");
          }}
        >
          <span className="chain-step">Policy cites</span>
          <span className="chain-body">
            <span className="chain-main">{cited.instrument}</span> <span className="mono">{paraLabel(cited)}</span>
          </span>
        </button>
      </li>
      <li className="chain-arrow" aria-hidden="true">
        →
      </li>
      <li>
        <button
          type="button"
          className="chain-box chain-withdrawn"
          onClick={() => {
            onShow("replaced");
          }}
        >
          <span className="chain-step">
            <span aria-hidden="true">⟲ </span>
            {withdrawal ? "Withdrawn" : "Replaced"}
          </span>
          <span className="chain-body">
            {withdrawal ? (
              <>
                <span className="chain-main">
                  by <span className="mono">{withdrawal.by}</span>
                </span>{" "}
                <span>on {formatIsoDate(withdrawal.on)}</span>
              </>
            ) : (
              <span className="chain-main">The cited instrument is still in force; another paragraph applies</span>
            )}
          </span>
        </button>
      </li>
      <li className="chain-arrow" aria-hidden="true">
        →
      </li>
      <li>
        {current ? (
          <button
            type="button"
            className={`chain-box chain-now${shown === "current" ? " is-active" : ""}`}
            aria-pressed={shown === "current"}
            onClick={() => {
              onShow("current");
            }}
          >
            <span className="chain-step">Now in force</span>
            <span className="chain-body">
              <span className="chain-main">{current.instrument}</span> <span className="mono">{paraLabel(current)}</span>
            </span>
          </button>
        ) : (
          <div className="chain-box chain-now chain-missing">
            <span className="chain-step">Now in force</span>
            <span className="chain-body">
              <span className="chain-main">No current paragraph found. Needs your review.</span>
            </span>
          </div>
        )}
      </li>
    </ol>
  );
}

/** The finding card of a clause with no finding: what the data says about it, never more. */
function PendingCard({ row }: { row: ClauseRow }): ReactNode {
  if (row.state === "incomplete") {
    return (
      <article className="finding-card" aria-labelledby="finding-card-title">
        <h3 id="finding-card-title" className="finding-head">
          <span className="finding-kicker">Finding</span>
          <StatusChip chip={FAILED_CHIP} />
        </h3>
        <div className="failed-note">
          <p>
            <strong>Couldn't complete.</strong> The run ended before Argus saved a finding for this clause, so Argus
            concluded nothing. A person must review it.
          </p>
        </div>
      </article>
    );
  }
  if (row.state === "reviewing") {
    return (
      <article className="finding-card finding-pending" aria-busy="true">
        <Spinner label="Finding not ready yet" />
        <span>Argus is reviewing this clause. The finding appears here as soon as it is saved.</span>
      </article>
    );
  }
  if (row.state === "queued") {
    return (
      <article className="finding-card finding-pending">
        <span>Argus has not started this clause yet. The trace shows no step for it so far.</span>
      </article>
    );
  }
  return (
    <article className="finding-card finding-pending finding-loading" aria-busy="true" aria-label="Loading the finding">
      <span className="skeleton-bar" aria-hidden="true" />
      <span className="skeleton-bar skeleton-short" aria-hidden="true" />
    </article>
  );
}
