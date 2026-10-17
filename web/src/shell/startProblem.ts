// What went wrong starting a live run, in plain English (UI_SPEC 6). Pure; tested in startProblem.test.ts.
import type { ApiError } from "../data/api";
import type { PostState } from "../data/useStartRun";

/** HTTP statuses that mean "live review cannot run right now", not "something is wrong with this request". */
const UNAVAILABLE_STATUSES = new Set([429, 502, 503]);

export type StartProblem =
  | { kind: "unavailable"; message: string }
  | { kind: "error"; title: string; message: string };

/** What, if anything, went wrong starting a live run. Pending and finished starts are not problems. */
export function startProblem(post: PostState): StartProblem | null {
  if (post.status === "blocked") {
    return {
      kind: "error",
      title: "The live run was not started: Argus could not watch it.",
      message: post.error.message,
    };
  }
  if (post.status !== "error") return null;
  const e: ApiError = post.error;
  if (e.kind === "http" && e.status !== undefined && UNAVAILABLE_STATUSES.has(e.status)) {
    // The banner's heading already says live review is unavailable; the API's own reason (which says which limit,
    // or that runs are switched off) is the detail, without the generic sentence messageForStatus puts before it.
    const detail = e.detail?.trim();
    return { kind: "unavailable", message: detail ? detail : e.message };
  }
  if (e.kind === "network") {
    return { kind: "error", title: "Argus could not reach its review service.", message: e.message };
  }
  if (e.kind === "auth") {
    return { kind: "error", title: "Your session could not be verified.", message: e.message };
  }
  return { kind: "error", title: "The live run could not be started.", message: e.message };
}
