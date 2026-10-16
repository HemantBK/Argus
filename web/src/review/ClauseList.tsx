// Left pane: one row per clause in policy order (UI_SPEC 3.2). Each row says only what the data shows: a spinner
// for a clause the trace shows under review, "Waiting" for one not started, "Couldn't complete" for one a finished
// run left without a finding, a skeleton while the findings are still loading. ↑/↓ move between rows; the selected row carries aria-current.
import { useRef, type KeyboardEvent, type ReactNode } from "react";
import { FAILED_CHIP, chipFor, type Badge, type ClauseRow } from "../data/selectors";
import { Spinner, StatusChip } from "./StatusChip";
import { DECISION_TEXT } from "./view";

interface Props {
  rows: readonly ClauseRow[];
  badges: ReadonlyMap<string, Badge>;
  selectedId: string | null;
  onSelect: (clauseId: string) => void;
  /** No run document yet (or still loading): show skeleton rows, never blank space. */
  skeleton: boolean;
}

const SKELETON_ROWS = 6;

export function ClauseList({ rows, badges, selectedId, onSelect, skeleton }: Props): ReactNode {
  const listRef = useRef<HTMLOListElement>(null);

  const onKeyDown = (e: KeyboardEvent<HTMLOListElement>): void => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const buttons = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>("button.clause-row") ?? []);
    const at = buttons.findIndex((b) => b === document.activeElement);
    if (at < 0) return;
    const next = buttons[e.key === "ArrowDown" ? Math.min(at + 1, buttons.length - 1) : Math.max(at - 1, 0)];
    if (!next) return;
    e.preventDefault();
    next.focus();
    const id = next.dataset.clauseId;
    if (id) onSelect(id);
  };

  return (
    <nav className="pane pane-clauses" aria-labelledby="clauses-heading">
      <h2 id="clauses-heading" className="pane-title">
        Clauses
      </h2>
      {skeleton ? (
        <ol className="clause-list" aria-busy="true" aria-label="Loading clauses">
          {Array.from({ length: SKELETON_ROWS }, (_, i) => (
            <li key={i} className="clause-skeleton" aria-hidden="true">
              <span className="skeleton-bar skeleton-short" />
              <span className="skeleton-bar" />
            </li>
          ))}
        </ol>
      ) : rows.length === 0 ? (
        <p className="text-muted pane-note">No clauses yet. They appear here as soon as Argus splits the policy.</p>
      ) : (
        <ol className="clause-list" ref={listRef} onKeyDown={onKeyDown}>
          {rows.map((row) => {
            const selected = row.clauseId === selectedId;
            const badge = badges.get(row.clauseId);
            return (
              <li key={row.clauseId}>
                <button
                  type="button"
                  className={`clause-row${selected ? " is-selected" : ""}`}
                  aria-current={selected ? "true" : undefined}
                  data-clause-id={row.clauseId}
                  onClick={() => {
                    onSelect(row.clauseId);
                  }}
                >
                  <span className="clause-row-head">
                    <span className="clause-id mono">{row.clauseId}</span>
                    <span className="clause-title">{row.title}</span>
                  </span>
                  <span className="clause-row-status">
                    <RowStatus row={row} />
                    {badge && (badge.state === "stopped" || badge.state === "revised") && (
                      <span className={`row-tag row-tag-${badge.state}`}>
                        <span aria-hidden="true">{badge.icon} </span>
                        {badge.state === "stopped" ? "Stopped" : "Revised"}
                      </span>
                    )}
                    {row.finding?.signoff && <span className="row-decision">{DECISION_TEXT[row.finding.signoff.decision]}</span>}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </nav>
  );
}

function RowStatus({ row }: { row: ClauseRow }): ReactNode {
  if (row.finding) return <StatusChip chip={chipFor(row.finding)} compact />;
  switch (row.state) {
    case "reviewing":
      return (
        <>
          <Spinner label={`Clause ${row.clauseId} is being reviewed`} />
          <span className="text-muted">Reviewing…</span>
        </>
      );
    case "queued":
      return <span className="text-muted">Waiting to be reviewed</span>;
    case "incomplete":
      return <StatusChip chip={FAILED_CHIP} compact />;
    default:
      return (
        <span className="skeleton-bar skeleton-chip" role="img" aria-label={`Loading the result for clause ${row.clauseId}`} />
      );
  }
}
