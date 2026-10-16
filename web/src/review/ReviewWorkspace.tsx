// The Review screen (UI_SPEC 3.2, 6, 16), presentational: it takes the live reads and callbacks, so it renders
// the same in tests as in the app. ReviewScreen wires it to Firestore and the API.
import { useMemo, useState, type ReactNode } from "react";
import { ErrorBanner } from "../ErrorBoundary";
import type { LiveRead } from "../data/hooks";
import { badgeFor, clauseRows, injectionFor, summarize, type Badge } from "../data/selectors";
import type { FindingDoc, RunDoc, RunSource, StepDoc } from "../data/types";
import { ClauseList } from "./ClauseList";
import { FindingPane, type CitationChoice } from "./FindingPane";
import type { SubmitSignoff } from "./SignoffActions";
import { SourcePane } from "./SourcePane";
import { SummaryBar } from "./SummaryBar";
import { TraceDrawer } from "./TraceDrawer";
import { lastSignoffAt, progressText, signoffAvailability } from "./view";
import "./review.css";

export interface ReviewWorkspaceProps {
  source: RunSource;
  run: LiveRead<RunDoc | null>;
  steps: LiveRead<StepDoc[]>;
  findings: LiveRead<FindingDoc[]>;
  onSignoff: SubmitSignoff;
  /** Clause selected on first render (a deep link); the first clause otherwise. */
  initialClauseId?: string;
}

export function ReviewWorkspace({ source, run, steps, findings, onSignoff, initialClauseId }: ReviewWorkspaceProps): ReactNode {
  const runDoc = run.status === "ready" ? run.data : null;
  const stepList = useMemo(() => (steps.status === "ready" ? steps.data : []), [steps]);
  const findingList = useMemo(() => (findings.status === "ready" ? findings.data : []), [findings]);
  const stepsReady = steps.status === "ready";

  const findingsReady = findings.status === "ready";
  const rows = useMemo(
    () => clauseRows(runDoc, findingList, { findingsLoaded: findingsReady, steps: stepsReady ? stepList : null }),
    [runDoc, findingList, findingsReady, stepList, stepsReady],
  );
  const counts = useMemo(() => summarize(runDoc, findingList, stepList), [runDoc, findingList, stepList]);
  const badges = useMemo(() => {
    const m = new Map<string, Badge>();
    if (stepsReady) for (const f of findingList) m.set(f.clause_id, badgeFor(f, stepList));
    return m;
  }, [findingList, stepList, stepsReady]);

  const [picked, setPicked] = useState<string | null>(initialClauseId ?? null);
  const [citationPick, setCitationPick] = useState<{ clauseId: string; which: CitationChoice } | null>(null);

  const selectedRow = rows.find((r) => r.clauseId === picked) ?? rows[0] ?? null;
  const selectedId = selectedRow?.clauseId ?? null;
  const finding = selectedRow?.finding ?? null;
  const which: CitationChoice =
    citationPick && citationPick.clauseId === selectedId
      ? citationPick.which
      : finding && !finding.citation && finding.replaced_citation
        ? "replaced"
        : "current";
  const shownCitation = finding ? (which === "replaced" ? finding.replaced_citation : finding.citation) : null;
  const replacement = finding && which === "replaced" ? finding.citation : null;

  const select = (clauseId: string): void => {
    setPicked(clauseId);
  };
  const showCitation = (w: CitationChoice): void => {
    if (selectedId !== null) setCitationPick({ clauseId: selectedId, which: w });
  };

  const running = runDoc?.status === "running";
  const listenErrors = (
    [
      ["the run", run],
      ["the trace", steps],
      ["the findings", findings],
    ] as const
  ).flatMap(([what, live]) => (live.status === "error" ? [{ what, error: live.error }] : []));

  const loading = run.status === "loading" || run.status === "idle";
  // No run document. A saved run that is missing does not exist. A live run may not exist yet: the shell attaches
  // the listeners before POST /api/runs creates it, and shows the start state and any start problem itself.
  const absent = run.status === "ready" && run.data === null;
  const waiting = loading || (absent && source === "runs");
  const skeleton = rows.length === 0 && (waiting || findings.status === "loading" || (running && counts.clauses === 0));

  return (
    <div className={`review${running ? " is-running" : ""}`}>
      {listenErrors.map(({ what, error }) => (
        <ErrorBanner key={what} title={`Could not read ${what}.`} error={error} />
      ))}

      {runDoc ? (
        <SummaryBar
          run={runDoc}
          counts={counts}
          progress={progressText(runDoc, counts)}
          completeAt={lastSignoffAt(findingList)}
          monitorReady={stepsReady}
          findingsReady={findingsReady}
        />
      ) : (
        <header className="summary summary-skeleton" aria-busy={waiting}>
          <h1>
            {run.status === "error"
              ? "Could not open the run"
              : absent && source === "samples"
                ? "Saved run not found"
                : absent
                  ? "Waiting for the run to start"
                  : "Opening the run…"}
          </h1>
          {absent && source === "samples" && <p>There is no saved run with this id.</p>}
          {absent && source === "runs" && (
            <p className="summary-progress" role="status" aria-live="polite">
              Results appear here as soon as Argus creates the run. Live runs are visible only to the browser that
              started them, so a link to a run from another browser stays on this message.
            </p>
          )}
          {loading && (
            <p className="summary-progress" role="status" aria-live="polite">
              Loading the run…
            </p>
          )}
        </header>
      )}

      {(runDoc !== null || waiting) && (
        <div className="review-panes">
          <ClauseList
            rows={rows}
            badges={badges}
            selectedId={selectedId}
            onSelect={select}
            skeleton={skeleton}
          />
          <FindingPane
            row={selectedRow}
            badge={selectedId !== null ? (badges.get(selectedId) ?? null) : null}
            injection={selectedId !== null && stepsReady ? injectionFor(selectedId, stepList) : null}
            steps={stepList}
            availability={signoffAvailability(source, runDoc, finding)}
            onSubmit={onSignoff}
            shown={which}
            onShowCitation={showCitation}
            loading={skeleton}
          />
          <SourcePane
            finding={finding}
            citation={shownCitation}
            replacement={replacement}
            onShowReplacement={() => {
              showCitation("current");
            }}
          />
        </div>
      )}

      {runDoc && <TraceDrawer steps={stepList} running={running} selectedClauseId={selectedId} onSelectClause={select} />}
    </div>
  );
}
