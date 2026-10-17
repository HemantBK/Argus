// Slim top bar (UI_SPEC 2): Argus, the run name, tabs Review · Audit log · Evaluation, and the persistent
// "Synthetic data only · Not legal advice · <library dates>" label (UI_SPEC 13: both visible on every screen; the
// app reviews only the synthetic sample policies, since upload is cut). The library dates (libraryLabel) come from
// the run, or the newest saved run.
import type { ReactNode } from "react";
import type { LiveRead } from "../data/hooks";
import type { RunDoc } from "../data/types";
import { libraryLabel, recordedAt, type LibraryDates } from "./format";
import { href, type Route, type RunRef } from "./route";

export interface TopBarProps {
  route: Route;
  /** The run the tabs point at (the current one, or the last one viewed before Evaluation). */
  runRef: RunRef | null;
  /** Its run document, when there is a run context. */
  run: LiveRead<RunDoc | null> | null;
  /** The RBI library dates to show, or null when no data has given them yet. */
  library: LibraryDates | null;
}

function RunName({ runRef, run }: { runRef: RunRef; run: LiveRead<RunDoc | null> | null }): ReactNode {
  if (!run || run.status === "loading" || run.status === "idle") {
    return <span className="topbar-run text-muted">Loading run…</span>;
  }
  if (run.status === "error") return <span className="topbar-run text-muted">Run could not be read</span>;
  if (run.data === null) {
    return (
      <span className="topbar-run text-muted">
        {runRef.source === "runs" ? "Live run starting…" : "Saved run not found"}
      </span>
    );
  }
  const r = run.data;
  const kind = r.saved || runRef.source === "samples" ? `saved run (recorded ${recordedAt(r)})` : "live run";
  return (
    <span className="topbar-run" title={`${r.policy_title} · ${kind}`}>
      <span className="topbar-run-title">{r.policy_title}</span>
      <span className="text-muted"> · {kind}</span>
    </span>
  );
}

function Tab({ label, to, current, disabledReason }: { label: string; to: string | null; current: boolean; disabledReason: string }): ReactNode {
  if (to === null) {
    return (
      <li>
        <span className="topbar-tab topbar-tab-disabled" aria-disabled="true" title={disabledReason}>
          {label}
        </span>
      </li>
    );
  }
  return (
    <li>
      <a className="topbar-tab" href={to} aria-current={current ? "page" : undefined}>
        {label}
      </a>
    </li>
  );
}

export function TopBar({ route, runRef, run, library }: TopBarProps): ReactNode {
  const noRun = "Run the demo or open a saved run first.";
  return (
    <header className="topbar">
      <a className="topbar-brand" href={href.start()}>
        Argus
      </a>
      {runRef && <RunName runRef={runRef} run={run} />}
      <nav aria-label="Screens" className="topbar-nav">
        <ul>
          <Tab label="Review" to={runRef ? href.review(runRef) : null} current={route.screen === "review"} disabledReason={noRun} />
          <Tab label="Audit log" to={runRef ? href.audit(runRef) : null} current={route.screen === "audit"} disabledReason={noRun} />
          <Tab label="Evaluation" to={href.eval()} current={route.screen === "eval"} disabledReason="" />
        </ul>
      </nav>
      <span className="topbar-legal">
        <span className="tag-synthetic">Synthetic data only</span> · Not legal advice · {library ? libraryLabel(library) : "RBI library dates not loaded yet"}
      </span>
    </header>
  );
}
