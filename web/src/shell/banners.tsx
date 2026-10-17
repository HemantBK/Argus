// Run-level banners shown above the Review and Audit screens (UI_SPEC 6): a live run that could not start, a
// live review that is unavailable (run cap, kill switch, model failure), a live run whose clauses failed because
// Gemini was busy, and a run that ended in failure. Each says what happened in plain English and offers saved
// runs, labelled as saved. When live review itself cannot help right now (unavailable, Gemini busy) the offer is
// the newest saved run of every sample policy (busy.ts savedRunOffers); otherwise it is the saved run of the same
// policy. A start problem also offers "Try again", which starts a new live run of the same policy. A pasted clause
// has no saved run of its own: its start problems (CustomStartProblemBanner) say what to change and lead back to
// the clause, and when the limit is reached or review is off they offer the sample policies' saved runs too.
import type { ReactNode } from "react";
import { ApiError, CUSTOM_CLAUSE_MAX_CHARS, CUSTOM_CLAUSE_MIN_CHARS } from "../data/api";
import type { RunDoc } from "../data/types";
import { savedRunOfferText } from "./busy";
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

/** The saved runs of every sample policy, for when live review cannot help right now. */
function SavedRunsOffer({ runs }: { runs: readonly RunDoc[] }): ReactNode {
  if (runs.length === 0) return <p className="banner-offer">No saved run is available yet.</p>;
  return (
    <>
      <p className="banner-offer">Saved runs were recorded earlier and are not live; opening one does not need Gemini:</p>
      <ul className="banner-offer-list">
        {runs.map((r) => (
          <li key={r.run_id}>
            <a href={href.review({ source: "samples", runId: r.run_id })}>{savedRunOfferText(r)}</a>
          </li>
        ))}
      </ul>
    </>
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

export function StartProblemBanner({
  problem,
  saved,
  offers,
  onRetry,
}: {
  problem: StartProblem;
  /** The saved run of the same policy, offered when the start itself went wrong. */
  saved: RunDoc | null;
  /** The saved run of every sample policy, offered when live review is unavailable. */
  offers: readonly RunDoc[];
  onRetry: () => void;
}): ReactNode {
  if (problem.kind === "unavailable") {
    return (
      <div className="run-banner" role="alert">
        <strong>Live review is temporarily unavailable.</strong>
        <p className="banner-detail">{problem.message}</p>
        <SavedRunsOffer runs={offers} />
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

/**
 * A live run in which Gemini's capacity failed the review of some clauses (their error starts with the API's
 * PROVIDER_BUSY wording, busy.ts). Those clauses already say "Couldn't complete"; this says why, once, and offers
 * the saved runs.
 */
export function BusyRunBanner({ busyClauses, offers }: { busyClauses: number; offers: readonly RunDoc[] }): ReactNode {
  const clauses = `${busyClauses} clause${busyClauses === 1 ? "" : "s"}`;
  return (
    <div className="run-banner" role="status">
      <strong>Gemini capacity was busy, so {clauses} of this run could not be completed.</strong>
      <p className="banner-detail">
        {busyClauses === 1 ? "That clause is" : "Those clauses are"} marked “Couldn't complete” for a person to review.
        Start a new live run later, or open a saved run now.
      </p>
      <SavedRunsOffer runs={offers} />
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

/** The identifier types the API's prescan reports (argus_api/custom.py IDENTIFIERS), with an article; others shown as sent. */
const IDENTIFIER_NAMES: Readonly<Record<string, string>> = {
  "email address": "an email address",
  "card number": "a card number",
  "mobile number": "a mobile number",
  "aadhaar number": "an Aadhaar-like number",
  pan: "a PAN",
  "ifsc code": "an IFSC code",
  "account number": "an account number",
};

function identifierName(type: string | undefined): string {
  if (type === undefined) return "a personal identifier";
  return IDENTIFIER_NAMES[type.trim().toLowerCase()] ?? `a personal identifier (${type})`;
}

const fmt = (n: number): string => n.toLocaleString("en-IN");

interface CustomProblem {
  title: string;
  message: string;
  /** Whether sending the same clause again could work (a limit or an outage, not a refusal of the text). */
  retry: boolean;
}

/** What went wrong starting a pasted-clause run, and what to do about it. Never quotes the clause. */
function customProblem(error: Error): CustomProblem {
  if (!(error instanceof ApiError)) {
    return { title: "The review was not started: Argus could not watch it.", message: error.message, retry: true };
  }
  switch (error.refusal?.code) {
    case "too_short":
      return {
        title: "Your clause is too short to review.",
        message: `Paste one complete clause of at least ${fmt(CUSTOM_CLAUSE_MIN_CHARS)} characters.`,
        retry: false,
      };
    case "too_long":
      return {
        title: "Your clause is too long to review.",
        message: `Paste one clause of at most ${fmt(CUSTOM_CLAUSE_MAX_CHARS)} characters, not a whole policy.`,
        retry: false,
      };
    case "not_attested":
      return {
        title: "Please confirm the text contains no personal data.",
        message: "Tick the box that says the text is synthetic or anonymised, then select Review again.",
        retry: false,
      };
    case "identifier_found":
      return {
        title: `Your clause seems to contain ${identifierName(error.refusal.identifierType)}.`,
        message:
          "Argus reviews synthetic or anonymised text only, so the clause was not reviewed. Replace the identifier with a placeholder and send it again.",
        retry: false,
      };
    case undefined:
      break;
  }
  if (error.kind === "network") return { title: "Argus could not reach its review service.", message: error.message, retry: true };
  if (error.kind === "auth") return { title: "Your session could not be verified.", message: error.message, retry: true };
  if (error.status === 429) {
    return {
      title: "The live review limit has been reached for now.",
      message: `${error.detail ? `${error.detail} ` : ""}Try again later, or run one of the sample policies.`,
      retry: true,
    };
  }
  if (error.status === 503) {
    return {
      title: "Custom clause review is not available right now.",
      message: `${error.detail ? `${error.detail} ` : ""}The sample policies and their saved runs are still on the start screen.`,
      retry: true,
    };
  }
  const retry = error.status === undefined || error.status >= 500;
  return { title: "The review of your clause could not be started.", message: error.message, retry };
}

/** A pasted-clause run that did not start: what happened, "Edit your clause", and "Try again" when it could help. */
export function CustomStartProblemBanner({
  error,
  offers,
  onRetry,
}: {
  error: Error;
  /** The saved run of every sample policy, offered when the limit is reached or review is off. */
  offers: readonly RunDoc[];
  onRetry: () => void;
}): ReactNode {
  const problem = customProblem(error);
  const unavailable = error instanceof ApiError && error.refusal === undefined && (error.status === 429 || error.status === 503);
  return (
    <div className="run-banner" role="alert">
      <strong>{problem.title}</strong>
      <p className="banner-detail">{problem.message}</p>
      <p className="banner-offer">
        <a href={href.start()}>Edit your clause</a>
      </p>
      {unavailable && <SavedRunsOffer runs={offers} />}
      {problem.retry && <TryAgain onRetry={onRetry} />}
    </div>
  );
}
