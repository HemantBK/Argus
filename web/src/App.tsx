// App shell: anonymous sign-in on load, hash routing, the top bar, and the four screens (Start, Review, Audit
// log, Evaluation). A live run is started here, not inside the Start screen, so its listeners and its open POST
// outlive the navigation to the Review screen (UI_SPEC 16.4: listeners first, then POST /api/runs).
//
// Run-level banners (live run blocked or not started, live review unavailable, run failed, each offering the
// saved run of the same policy) belong to the shell and show above both the Review and the Audit log screens;
// the screens show only what is inside the run.
import { useCallback, useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { ErrorBanner } from "./ErrorBoundary";
import { AuditScreen } from "./audit/AuditScreen";
import { useRun, useSavedRuns, type LiveRead, type SavedRuns } from "./data/hooks";
import type { RunDoc } from "./data/types";
import { useStartRun, type StartRun } from "./data/useStartRun";
import { EvalScreen } from "./eval/EvalScreen";
import { appEnv } from "./env";
import { ensureSignedIn } from "./firebase";
import { ReviewScreen } from "./review/ReviewScreen";
import { RunFailedBanner, StartingNotice, StartProblemBanner } from "./shell/banners";
import { startProblem } from "./shell/startProblem";
import { href, navigate, runRefOf, useNavigation, type Route, type RunRef } from "./shell/route";
import { runLibrary } from "./shell/format";
import { TopBar } from "./shell/TopBar";
import { latestCorpusAsOn, latestLibrary, savedRunFor } from "./start/catalog";
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
}

interface ScreenProps {
  route: Route;
  saved: LiveRead<SavedRuns>;
  onRun: (policyId: string) => void;
  fallbackAsOn: string | null;
  /**
   * The live run on screen was never started because its listeners were refused (useStartRun "blocked"). The
   * shell's banner explains that and offers the saved run; the run screens would only repeat the same listener
   * errors for a run that does not exist, so they are not rendered.
   */
  runNotStarted: boolean;
}

function Screen({ route, saved, onRun, fallbackAsOn, runNotStarted }: ScreenProps): ReactNode {
  if (runNotStarted && (route.screen === "review" || route.screen === "audit")) return null;
  switch (route.screen) {
    case "start":
      return <StartScreen saved={saved} onRun={onRun} />;
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

function RunBanners({ runDoc, live, isStartedRun, offer }: {
  runDoc: RunDoc | null;
  live: StartRun;
  isStartedRun: boolean;
  offer: RunDoc | null;
}): ReactNode {
  const problem = isStartedRun ? startProblem(live.post) : null;
  return (
    <>
      {problem && <StartProblemBanner problem={problem} saved={offer} />}
      {isStartedRun && live.post.status === "waiting_for_listeners" && <StartingNotice />}
      {!problem && runDoc?.status === "failed" && <RunFailedBanner run={runDoc} saved={offer} />}
    </>
  );
}

export function App(): ReactNode {
  appEnv(); // throws a ConfigError (shown by the ErrorBoundary) when a setting is missing
  const signIn = useSignIn();
  const { route, lastRun } = useNavigation();
  const saved = useSavedRuns(SAVED_RUNS_LISTED);
  const live = useStartRun();
  const [started, setStarted] = useState<StartedRun | null>(null);

  const routeRun = runRefOf(route);
  const tabsRun: RunRef | null = routeRun ?? (route.screen === "eval" ? lastRun : null);
  const run = useRun(tabsRun?.runId ?? null, tabsRun?.source ?? "samples");
  const runDoc: RunDoc | null = run.status === "ready" ? run.data : null;
  const savedRuns = saved.status === "ready" ? saved.data.runs : [];
  const savedAsOn = latestCorpusAsOn(savedRuns);
  const library = (runDoc && runLibrary(runDoc)) ?? latestLibrary(savedRuns);

  const { start } = live;
  const onRun = useCallback(
    (policyId: string) => {
      const runId = start(policyId);
      setStarted({ runId, policyId });
      navigate(href.review({ source: "runs", runId }));
    },
    [start],
  );

  const isStartedRun =
    routeRun !== null && routeRun.source === "runs" && started !== null && routeRun.runId === started.runId && live.runId === started.runId;
  const policyId = runDoc?.policy_id ?? (isStartedRun ? started.policyId : null);
  const offer = policyId === null ? null : savedRunFor(policyId, savedRuns, routeRun?.runId ?? null);

  return (
    <>
      {/* The hash is the router, so the skip link moves focus itself instead of navigating to "#main". */}
      <a className="visually-hidden skip-link" href="#main" onClick={skipToMain}>
        Skip to main content
      </a>
      <TopBar route={route} runRef={tabsRun} run={tabsRun ? run : null} library={library} />
      <div className="shell-banners">
        {signIn.status === "error" && (
          <ErrorBanner title="Could not sign in. Live runs cannot start until sign-in works." error={signIn.error} />
        )}
        {routeRun && <RunBanners runDoc={runDoc} live={live} isStartedRun={isStartedRun} offer={offer} />}
      </div>
      <main id="main" tabIndex={-1}>
        <Screen route={route} saved={saved} onRun={onRun} fallbackAsOn={savedAsOn} runNotStarted={isStartedRun && live.post.status === "blocked"} />
      </main>
    </>
  );
}
