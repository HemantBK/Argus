// Header summary bar (UI_SPEC 3.2, the 5-second answer): counts by finding type with icon + label + number, the
// monitor summary, sign-off progress, and what kind of run this is. Every figure is counted from Firestore.
import type { ReactNode } from "react";
import { FAILED_CHIP, LABEL_CHIPS, formatIst, type SummaryCounts } from "../data/selectors";
import type { FindingLabel, RunDoc } from "../data/types";
import { formatIsoDate, plural } from "./view";

/** Label order in the bar: what needs attention first. Gap and conflict share the red chip, each with its own label. */
const ORDER: FindingLabel[] = ["outdated_reference", "gap", "conflict", "compliant", "insufficient_evidence"];

interface Props {
  run: RunDoc;
  counts: SummaryCounts;
  progress: string | null;
  completeAt: Date | null;
  /** The trace (where verdicts are recorded) has loaded; until then the monitor figures are not shown. */
  monitorReady: boolean;
  /** The findings have loaded; until then no finding count or sign-off figure is shown (none has been measured). */
  findingsReady: boolean;
}

/** A count, or a placeholder while it has not been read: an unread count is never shown as 0. */
function Count({ n, ready }: { n: number; ready: boolean }): ReactNode {
  return ready ? <strong>{n}</strong> : <span className="count-loading" role="img" aria-label="loading" />;
}

export function SummaryBar({ run, counts, progress, completeAt, monitorReady, findingsReady }: Props): ReactNode {
  // The clause total comes from run.clauses when recorded, else from the findings once they are read.
  const clausesKnown = run.clauses.length > 0 || findingsReady;
  return (
    <header className="summary">
      <div className="summary-title">
        <h1>{run.policy_title}</h1>
        <p className="summary-meta">
          {run.saved ? (
            <span className="run-kind run-kind-saved">Saved run (recorded {formatIst(run.started_at, true)})</span>
          ) : (
            <span className="run-kind">Live run, started {formatIst(run.started_at, true)}</span>
          )}
          <span>RBI snapshot as on {formatIsoDate(run.corpus_as_on)}</span>
          <span>
            Agent <span className="mono">{run.agent_model}</span> · Monitor <span className="mono">{run.monitor_model}</span>
          </span>
        </p>
      </div>

      <div className="summary-row">
        <ul className="summary-counts" aria-label="Findings by type">
          <li className="count count-total">
            <Count n={counts.clauses} ready={clausesKnown} /> {clausesKnown && counts.clauses === 1 ? "clause" : "clauses"}
          </li>
          {ORDER.map((label) => {
            const chip = LABEL_CHIPS[label];
            return (
              <li key={label} className={`count count-${chip.tone}`}>
                <span className="chip-icon" aria-hidden="true">
                  {chip.icon}
                </span>
                <Count n={counts.byLabel[label]} ready={findingsReady} /> {chip.label}
              </li>
            );
          })}
          {findingsReady && counts.failed > 0 && (
            <li className={`count count-${FAILED_CHIP.tone}`}>
              <span className="chip-icon" aria-hidden="true">
                {FAILED_CHIP.icon}
              </span>
              <strong>{counts.failed}</strong> {FAILED_CHIP.label}
            </li>
          )}
        </ul>

        <p className="summary-monitor">
          {monitorReady
            ? `Monitor: ${plural(counts.monitorRevised, "citation")} revised · ${counts.monitorStopped} stopped for human review`
            : "Monitor: loading recorded verdicts…"}
        </p>

        <p className={`signoff-progress${findingsReady && counts.reviewComplete ? " signoff-complete" : ""}`}>
          {findingsReady && counts.reviewComplete && (
            <span className="chip-icon" aria-hidden="true">
              ✓{" "}
            </span>
          )}
          Signed off <Count n={counts.signedOff} ready={findingsReady} /> / <Count n={counts.clauses} ready={clausesKnown} />
          {findingsReady && counts.reviewComplete && completeAt && <> · Review complete {formatIst(completeAt, true)}</>}
        </p>
      </div>

      {/* Only a running run has progress to announce; a finished or saved run does not reserve the line. */}
      {run.status === "running" && (
        <p className="summary-progress" role="status" aria-live="polite">
          {progress ?? ""}
        </p>
      )}
    </header>
  );
}
