// Run-level banners shown above the Review and Audit screens (UI_SPEC 6): a live run that could not start, a
// live review that is unavailable (model quota, kill switch, run cap), and a run that ended in failure. Each
// says what happened in plain English and offers the saved run of the same policy, labelled as saved. A start
// problem also offers "Try again", which starts a new live run of the same policy.
import type { ReactNode } from "react";
import type { RunDoc } from "../data/types";
import { recordedAt } from "./format";
import { href } from "./route";
import type { StartProblem } from "./startProblem";

function SavedRunOffer({ saved }: { saved: RunDoc | null }): ReactNode {
  if (!saved) return <p className="banner-offer">No saved run of this policy is available yet.</p>;
  return (
    <p className="banner-offer">
      <a href={href.review({ source: "samples", runId: saved.run_id })}>
        Open the saved run recorded at {recordedAt(saved)}
      </a>
      <span className="text-muted"> (a saved run, not live)</span>
    </p>
  );
}

function TryAgain({ onRetry }: { onRetry: () => void }): ReactNode {
  return (
    <p className="banner-actions">
      <button type="button" className="primary" onClick={onRetry}>
        Try again
      </button>
    </p>
  );
}

export function StartProblemBanner({ problem, saved, onRetry }: { problem: StartProblem; saved: RunDoc | null; onRetry: () => void }): ReactNode {
  if (problem.kind === "unavailable") {
    return (
      <div className="run-banner" role="alert">
        <strong>
          Live review is temporarily unavailable.
          {saved ? ` Open the saved run recorded at ${recordedAt(saved)}?` : ""}
        </strong>
        <p className="banner-detail">{problem.message}</p>
        <SavedRunOffer saved={saved} />
        <TryAgain onRetry={onRetry} />
      </div>
    );
  }
  return (
    <div className="run-banner" role="alert">
      <strong>{problem.title}</strong>
      <p className="banner-detail">{problem.message}</p>
      <SavedRunOffer saved={saved} />
      <TryAgain onRetry={onRetry} />
    </div>
  );
}

export function RunFailedBanner({ run, saved }: { run: RunDoc; saved: RunDoc | null }): ReactNode {
  return (
    <div className="run-banner" role="alert">
      <strong>This run failed before every clause was reviewed.</strong>
      <p className="banner-detail">
        {run.error && run.error.trim() !== "" ? `Reason recorded: ${run.error}` : "No reason was recorded for the failure."}{" "}
        Finished clauses are still shown; clauses that could not be completed are marked.
      </p>
      <SavedRunOffer saved={saved} />
    </div>
  );
}

export function StartingNotice(): ReactNode {
  return (
    <p className="run-notice" role="status" aria-live="polite">
      Starting the live review…
    </p>
  );
}
