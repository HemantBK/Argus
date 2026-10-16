// Hash routes (Firebase Hosting serves one index.html, so the hash keeps every screen linkable):
//   #/                 Start
//   #/run/<id>         Review of a live run (runs/)
//   #/sample/<id>      Review of a saved sample run (samples/, read-only)
//   #/run/<id>/audit   Audit log of a live run
//   #/sample/<id>/audit Audit log of a saved run
//   #/eval             Evaluation
// Anything else is a "not found" route that is shown as such, never silently redirected.
import { useEffect, useState } from "react";
import type { RunSource } from "../data/types";

export interface RunRef {
  source: RunSource;
  runId: string;
}

export type Route =
  | { screen: "start" }
  | ({ screen: "review" } & RunRef)
  | ({ screen: "audit" } & RunRef)
  | { screen: "eval" }
  | { screen: "not_found"; hash: string };

/** Run ids are uuid4 hex from the API; saved runs use the same format. Keep the route check loose (a-z0-9_-). */
const RUN_ID = /^[A-Za-z0-9_-]{1,128}$/;

const SOURCE_BY_SEGMENT: Record<string, RunSource> = { run: "runs", sample: "samples" };

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#/, "").replace(/^\/+/, "").replace(/\/+$/, "");
  if (path === "") return { screen: "start" };
  if (path === "eval") return { screen: "eval" };
  const parts = path.split("/");
  const [kind, runId, sub, ...rest] = parts;
  const source = kind === undefined ? undefined : SOURCE_BY_SEGMENT[kind];
  if (source && runId !== undefined && RUN_ID.test(runId) && rest.length === 0) {
    if (sub === undefined) return { screen: "review", source, runId };
    if (sub === "audit") return { screen: "audit", source, runId };
  }
  return { screen: "not_found", hash };
}

function segment(source: RunSource): string {
  return source === "runs" ? "run" : "sample";
}

export const href = {
  start: (): string => "#/",
  review: (r: RunRef): string => `#/${segment(r.source)}/${r.runId}`,
  audit: (r: RunRef): string => `#/${segment(r.source)}/${r.runId}/audit`,
  eval: (): string => "#/eval",
};

export function runRefOf(route: Route): RunRef | null {
  return route.screen === "review" || route.screen === "audit" ? { source: route.source, runId: route.runId } : null;
}

export interface Navigation {
  route: Route;
  /** The run the officer last looked at, kept while they visit the Evaluation tab so Review/Audit link back. */
  lastRun: RunRef | null;
}

function navigationFor(hash: string, previous: RunRef | null): Navigation {
  const route = parseRoute(hash);
  const run = runRefOf(route);
  // The start screen clears the run context; Evaluation keeps it.
  return { route, lastRun: run ?? (route.screen === "start" ? null : previous) };
}

/** The current route, following hashchange. */
export function useNavigation(): Navigation {
  const [nav, setNav] = useState<Navigation>(() => navigationFor(window.location.hash, null));
  useEffect(() => {
    const onChange = (): void => {
      setNav((prev) => navigationFor(window.location.hash, prev.lastRun));
    };
    window.addEventListener("hashchange", onChange);
    return () => {
      window.removeEventListener("hashchange", onChange);
    };
  }, []);
  return nav;
}

export function navigate(to: string): void {
  window.location.hash = to;
}
