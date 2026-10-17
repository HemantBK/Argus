// Start screen (UI_SPEC 3.1 as cut by 16.1): what Argus does in one line, one big Run demo button, the sample
// policies with their saved runs, and the footer. No upload, no paste box. Names and clause counts come from the
// sample policy files the API reviews; saved runs and dates from Firestore.
import type { ReactNode } from "react";
import { ErrorBanner } from "../ErrorBoundary";
import type { LiveRead, SavedRuns } from "../data/hooks";
import type { RunDoc } from "../data/types";
import { libraryLabel, savedRunLabel } from "../shell/format";
import { href } from "../shell/route";
import { DEMO_POLICY_ID, latestLibrary, sampleCards, type SampleCard } from "./catalog";
import { HEADLINE, TAGLINE } from "./copy";
import { SAMPLE_POLICIES, type SamplePolicies } from "./samplePolicies";
import "./start.css";

export interface StartScreenProps {
  saved: LiveRead<SavedRuns>;
  /** Start a live run of a sample policy (listeners first, then the POST) and open its Review screen. */
  onRun: (policyId: string) => void;
  /** The sample policy files bundled into this build (tests pass their own). */
  policies?: SamplePolicies;
}

function SkeletonCards(): ReactNode {
  return (
    <ul className="sample-cards" aria-busy="true" aria-label="Loading sample policies">
      {[0, 1].map((i) => (
        <li key={i} className="sample-card skeleton-card" aria-hidden="true">
          <span className="skeleton-line skeleton-line-wide" />
          <span className="skeleton-line" />
          <span className="skeleton-line" />
        </li>
      ))}
    </ul>
  );
}

/** How many saved runs a card lists before the rest fold away under "older saved runs". */
const SHOWN_SAVED_RUNS = 2;

function SavedRunLink({ run }: { run: RunDoc }): ReactNode {
  return (
    <li>
      <a href={href.review({ source: "samples", runId: run.run_id })}>{savedRunLabel(run)}</a>
    </li>
  );
}

function SavedRunLinks({ runs }: { runs: RunDoc[] }): ReactNode {
  const shown = runs.slice(0, SHOWN_SAVED_RUNS);
  const older = runs.slice(SHOWN_SAVED_RUNS);
  return (
    <>
      <ul className="saved-run-list">
        {shown.map((r) => (
          <SavedRunLink key={r.run_id} run={r} />
        ))}
      </ul>
      {older.length > 0 && (
        <details className="saved-run-older">
          <summary>
            {older.length} older saved run{older.length === 1 ? "" : "s"}
          </summary>
          <ul className="saved-run-list">
            {older.map((r) => (
              <SavedRunLink key={r.run_id} run={r} />
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

/** A card as shown: savedRuns is null while the saved runs are still loading. */
type CardView = Omit<SampleCard, "savedRuns"> & { savedRuns: RunDoc[] | null };

function Card({ card, onRun }: { card: CardView; onRun: (policyId: string) => void }): ReactNode {
  const headingId = `sample-${card.policyId}`;
  return (
    <li className="sample-card" aria-labelledby={headingId}>
      <div className="sample-card-head">
        <h3 id={headingId}>{card.title}</h3>
        {card.synthetic && <span className="tag-synthetic">Synthetic</span>}
      </div>
      <p className="text-muted sample-card-meta">
        {card.clauses === null ? "Clause count not recorded" : `${card.clauses} clause${card.clauses === 1 ? "" : "s"}`}
      </p>
      {card.savedRuns === null ? (
        <p className="text-muted">Loading saved runs…</p>
      ) : card.savedRuns.length === 0 ? (
        <p className="text-muted">No saved run yet. Run it live to see the review.</p>
      ) : (
        <SavedRunLinks runs={card.savedRuns} />
      )}
      <button
        type="button"
        className="sample-card-run"
        onClick={() => {
          onRun(card.policyId);
        }}
        aria-label={`Run live on ${card.title}`}
      >
        Run live
      </button>
    </li>
  );
}

function Samples({ saved, onRun, policies }: StartScreenProps & { policies: SamplePolicies }): ReactNode {
  const loading = saved.status === "idle" || saved.status === "loading";
  // Cards come from the sample files at once; saved runs fill in when Firestore answers.
  if (loading && policies.policies.length === 0) return <SkeletonCards />;
  const savedRuns = saved.status === "ready" ? saved.data.runs : [];
  const cards: CardView[] = sampleCards(savedRuns, policies.policies).map((c) => ({
    ...c,
    savedRuns: saved.status === "ready" ? c.savedRuns : null,
  }));
  return (
    <>
      {policies.invalid.length > 0 && (
        <ErrorBanner
          title={`${policies.invalid.length} sample polic${policies.invalid.length === 1 ? "y file" : "y files"} could not be read and ${policies.invalid.length === 1 ? "is" : "are"} not listed.`}
          error={new Error(policies.invalid.map((e) => e.message).join("\n"))}
        />
      )}
      {saved.status === "error" && <ErrorBanner title="The saved runs could not be loaded." error={saved.error} />}
      {saved.status === "ready" && saved.data.invalid.length > 0 && (
        <ErrorBanner
          title={`${saved.data.invalid.length} saved run${saved.data.invalid.length === 1 ? "" : "s"} could not be read and ${saved.data.invalid.length === 1 ? "is" : "are"} not listed.`}
          error={new Error(saved.data.invalid.map((e) => e.message).join("\n"))}
        />
      )}
      {cards.length === 0 ? (
        saved.status === "ready" && <p className="text-muted">No sample policies or saved runs are available in this build.</p>
      ) : (
        <ul className="sample-cards">
          {cards.map((c) => (
            <Card key={c.policyId} card={c} onRun={onRun} />
          ))}
        </ul>
      )}
    </>
  );
}

function Footer({ saved }: { saved: LiveRead<SavedRuns> }): ReactNode {
  const library = saved.status === "ready" ? latestLibrary(saved.data.runs) : null;
  const dates =
    saved.status === "ready" ? (library ? libraryLabel(library) : "RBI library dates not recorded yet") : "RBI library dates loading…";
  return (
    <footer className="start-footer">
      {dates} · Synthetic data only · Not legal advice
    </footer>
  );
}

export function StartScreen({ saved, onRun, policies = SAMPLE_POLICIES }: StartScreenProps): ReactNode {
  return (
    <div className="start">
      <section className="start-hero" aria-labelledby="start-title">
        <h1 id="start-title">{HEADLINE}</h1>
        <p className="start-sub">{TAGLINE}</p>
        <button
          type="button"
          className="primary start-cta"
          onClick={() => {
            onRun(DEMO_POLICY_ID);
          }}
        >
          Run demo on a sample NBFC policy
        </button>
      </section>
      <section className="start-samples" aria-labelledby="samples-title">
        <h2 id="samples-title">Sample policies</h2>
        <Samples saved={saved} onRun={onRun} policies={policies} />
      </section>
      <Footer saved={saved} />
    </div>
  );
}
