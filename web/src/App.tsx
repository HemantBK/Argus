// App shell: anonymous sign-in on load, hash routing, the top bar, and the four screens (Start, Review, Audit
// log, Evaluation). A live run is started here, not inside the Start screen, so its listeners and its open POST
// outlive the navigation to the Review screen (UI_SPEC 16.4: listeners first, then POST /api/runs).
//
// Run-level banners (live run blocked or not started, live review unavailable, Gemini busy, some clauses not
// finished, run failed, each offering saved runs) belong to the shell and show above both the Review and the Audit log screens; the screens
// show only what is inside the run. The shell also reads /health once on load, so the Start screen can say when
// Gemini is busy (data/health.ts).
import { useCallback, useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { ErrorBanner } from "./ErrorBoundary";
import { AuditScreen } from "./audit/AuditScreen";
import { CUSTOM_POLICY_ID, type CustomClause } from "./data/api";
import { useAgentBreaker, type BreakerState } from "./data/health";
import { useFindings, useRun, useSavedRuns, type LiveRead, type SavedRuns } from "./data/hooks";
import type { FindingDoc, RunDoc } from "./data/types";
import { useStartRun, type StartRun } from "./data/useStartRun";
import { EvalScreen } from "./eval/EvalScreen";
import { appEnv } from "./env";
import { ensureSignedIn } from "./firebase";
import { ReviewScreen } from "./review/ReviewScreen";
import { BusyRunBanner, CustomStartProblemBanner, PartialRunBanner, RunFailedBanner, StartingNotice, StartProblemBanner } from "./shell/banners";
import { busyClauseIds, isPartialRun, runCounts, savedRunOffers } from "./shell/busy";
import { startProblem } from "./shell/startProblem";
import { href, navigate, runRefOf, useNavigation, type Route, type RunRef } from "./shell/route";
import { runLibrary } from "./shell/format";
import { TopBar } from "./shell/TopBar";
import { latestCorpusAsOn, latestLibrary, savedRunFor } from "./start/catalog";
import { SAMPLE_POLICIES } from "./start/samplePolicies";
import { StartScreen } from "./start/StartScreen";
import "./shell/shell.css";

/** Saved runs read for the Start screen and the saved-run offers (newest first). */
const SAVED_RUNS_LISTED = 50;

type SignIn = { status: "pending" } | { status: "ok"; uid: string } | { status: "error"; error: Error };

function useSignIn(): SignIn {
  const [signIn, setSignIn] = useState<SignIn>({ status: "pending" });
  useEffect(() => {
    ensureSignedIn().then(
      (user) => {
        setSignIn({ status: "ok", uid: user.uid });
        if (import.meta.env.DEV) console.info(`Argus: signed in anonymously as ${user.uid} (seed live runs with --owner-uid ${user.uid})`);
      },
      (e: unknown) => {
        setSignIn({ status: "error", error: e instanceof Error ? e : new Error(String(e)) });
      },
    );
  }, []);
  return signIn;
}

function NotFound({ hash }: { hash: string }): ReactNode {
  return (
    <div className="page">
      <h1>Page not found</h1>
      <p>
        There is no Argus screen at <code>{hash}</code>. <a href={href.start()}>Go to the start screen</a>.
      </p>
    </div>
  );
}

function skipToMain(e: MouseEvent<HTMLAnchorElement>): void {
  e.preventDefault();
  document.getElementById("main")?.focus();
}

/** The live run this page started. */
interface StartedRun {
  runId: string;
  policyId: string;
  /** A pasted clause (held in memory only, so Try again can resend it). */
  custom?: CustomClause;
}

interface ScreenProps {
  route: Route;
  saved: LiveRead<SavedRuns>;
  onRun: (policyId: string, custom?: CustomClause) => void;
  fallbackAsOn: string | null;
  /** The agent model's breaker from /health, read once on load; null until read or when it cannot be read. */
  agentBreaker: BreakerState | null;
  /** Whether "Try your own clause" is open in this build (env.ts customClauseOpen). */
  customOpen: boolean;
  /**
   * The live run on screen could not be started and its run document does not exist: its listeners were refused
   * (useStartRun "blocked") or POST /api/runs failed before the API created the run (network error, 4xx, 5xx,
   * run cap, kill switch). The shell's banner says so and offers "Try again" and the saved run. The run screens
   * are not rendered: for a run that does not exist they could only say "Waiting for the run to start", which
   * would never change and would contradict the banner.
   */
  runNotStarted: boolean;
}

function Screen({ route, saved, onRun, fallbackAsOn, agentBreaker, customOpen, runNotStarted }: ScreenProps): ReactNode {
  if (runNotStarted && (route.screen === "review" || route.screen === "audit")) return null;
  switch (route.screen) {
    case "start":
      return <StartScreen saved={saved} onRun={onRun} agentBreaker={agentBreaker} customOpen={customOpen} />;
    case "review":
      return <ReviewScreen key={`${route.source}/${route.runId}`} source={route.source} runId={route.runId} />;
    case "audit":
      return <AuditScreen key={`${route.source}/${route.runId}`} source={route.source} runId={route.runId} />;
    case "eval":
      return <EvalScreen fallbackAsOn={fallbackAsOn} />;
    case "not_found":
      return <NotFound hash={route.hash} />;
  }
}

function RunBanners({ runDoc, findings, live, isStartedRun, isCustom, offer, offers, busyClauses, onRetry }: {
  runDoc: RunDoc | null;
  /** The live run's findings, for the per-clause lines of the partial-run banner (none for a saved run). */
  findings: readonly FindingDoc[];
  live: StartRun;
  isStartedRun: boolean;
  /** The started run reviews a pasted clause: no saved run to offer, and its refusals lead back to the clause. */
  isCustom: boolean;
  /** The newest saved run of the same policy. */
  offer: RunDoc | null;
  /** The newest saved run of every sample policy (busy.ts savedRunOffers). */
  offers: readonly RunDoc[];
  /** Clauses of this live run that failed because Gemini was busy (busy.ts busyClauseIds). */
  busyClauses: number;
  onRetry: () => void;
}): ReactNode {
  const problem = isStartedRun ? startProblem(live.post) : null;
  const startError = live.post.status === "error" || live.post.status === "blocked" ? live.post.error : null;
  // Some clauses reviewed and some failed, read from the summary whatever the status (busy.ts isPartialRun).
  const partialRun = runDoc !== null && isPartialRun(runCounts(runDoc)) ? runDoc : null;
  const partial = partialRun !== null;
  return (
    <>
      {problem &&
        (isCustom && startError ? (
          <CustomStartProblemBanner error={startError} offers={offers} onRetry={onRetry} />
        ) : (
          <StartProblemBanner problem={problem} saved={offer} offers={offers} onRetry={onRetry} />
        ))}
      {isStartedRun && live.post.status === "waiting_for_listeners" && <StartingNotice />}
      {!problem && partialRun && <PartialRunBanner run={partialRun} findings={findings} offers={offers} />}
      {/* The partial-run banner already gives each busy clause's reason, so the busy banner is not repeated. */}
      {!problem && !partial && busyClauses > 0 && <BusyRunBanner busyClauses={busyClauses} offers={offers} />}
      {/* Red only when no clause was reviewed: a run with some findings is a partial run, whatever its status. */}
      {!problem && !partial && runDoc?.status === "failed" && <RunFailedBanner run={runDoc} saved={offer} />}
    </>
  );
}

export function App(): ReactNode {
  appEnv(); // throws a ConfigError (shown by the ErrorBoundary) when a setting is missing
  const signIn = useSignIn();
  const { route, lastRun } = useNavigation();
  const saved = useSavedRuns(SAVED_RUNS_LISTED);
  const live = useStartRun();
  const agentBreaker = useAgentBreaker();
  const [started, setStarted] = useState<StartedRun | null>(null);

  const routeRun = runRefOf(route);
  const tabsRun: RunRef | null = routeRun ?? (route.screen === "eval" ? lastRun : null);
  const run = useRun(tabsRun?.runId ?? null, tabsRun?.source ?? "samples");
  const runDoc: RunDoc | null = run.status === "ready" ? run.data : null;
  // A live run's findings, for the Gemini-busy banner. The same query as the Review screen's, so Firestore serves
  // both from one listener. Saved runs are not read here: they are what the banner offers.
  const liveRun = routeRun?.source === "runs" ? routeRun : null;
  const liveFindings = useFindings(liveRun?.runId ?? null, "runs", run);
  const savedRuns = saved.status === "ready" ? saved.data.runs : [];
  const savedAsOn = latestCorpusAsOn(savedRuns);
  const library = (runDoc && runLibrary(runDoc)) ?? latestLibrary(savedRuns);

  const { start } = live;
  const onRun = useCallback(
    (policyId: string, custom?: CustomClause) => {
      const runId = custom ? start(CUSTOM_POLICY_ID, custom) : start(policyId);
      setStarted(custom ? { runId, policyId: CUSTOM_POLICY_ID, custom } : { runId, policyId });
      navigate(href.review({ source: "runs", runId }));
    },
    [start],
  );

  const isStartedRun =
    routeRun !== null && routeRun.source === "runs" && started !== null && routeRun.runId === started.runId && live.runId === started.runId;
  const policyId = runDoc?.policy_id ?? (isStartedRun ? started.policyId : null);
  const offer = policyId === null ? null : savedRunFor(policyId, savedRuns, routeRun?.runId ?? null);
  const offers = savedRunOffers(savedRuns, SAMPLE_POLICIES.policies, routeRun?.runId ?? null);
  const busyClauses = liveFindings.status === "ready" ? busyClauseIds(liveFindings.data).length : 0;
  // The start failed (startProblem) and the API never created the run. A run the API created before failing
  // (for example a 500 mid-review) still has content to show, so its screens stay, under the banner.
  const runNotStarted = isStartedRun && startProblem(live.post) !== null && runDoc === null;
  const retry = useCallback(() => {
    if (started) onRun(started.policyId, started.custom);
  }, [started, onRun]);

  return (
    <>
      {/* The hash is the router, so the skip link moves focus itself instead of navigating to "#main". */}
      <a className="visually-hidden skip-link" href="#main" onClick={skipToMain}>
        Skip to main content
      </a>
      <TopBar route={route} runRef={tabsRun} run={tabsRun ? run : null} library={library} startFailed={runNotStarted} />
      <div className="shell-banners">
        {signIn.status === "error" && (
          <ErrorBanner title="Could not sign in. Live runs cannot start until sign-in works." error={signIn.error} />
        )}
        {routeRun && <RunBanners runDoc={runDoc} findings={liveFindings.status === "ready" ? liveFindings.data : []} live={live} isStartedRun={isStartedRun} isCustom={isStartedRun && started.policyId === CUSTOM_POLICY_ID} offer={offer} offers={offers} busyClauses={busyClauses} onRetry={retry} />}
      </div>
      <main id="main" tabIndex={-1}>
        <Screen
          route={route}
          saved={saved}
          onRun={onRun}
          fallbackAsOn={savedAsOn}
          agentBreaker={agentBreaker}
          customOpen={appEnv().customClauseOpen}
          runNotStarted={runNotStarted}
        />
      </main>
    </>
  );
}
