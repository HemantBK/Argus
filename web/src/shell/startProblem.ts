// What went wrong starting a live run, in plain English (UI_SPEC 6). Pure; tested in startProblem.test.ts.
import type { ApiError } from "../data/api";
import type { PostState } from "../data/useStartRun";

/** HTTP statuses that mean "live review cannot run right now", not "something is wrong with this request". */
const UNAVAILABLE_STATUSES = new Set([429, 502, 503]);

/**
 * The reasons the API gives when it will not start a live run (backend runs.py admit_counted, RUNS_PAUSED and
 * CUSTOM_OFF, read_limits; main.py's store-error handler), each matched on the start of the API's sentence and said again in
 * our own words. Only these reach the banner: an unknown detail, such as a proxy's HTML error page or a changed
 * API sentence, is never printed as it came (it stays on the ApiError for the console), and the banner says what
 * the HTTP status means instead. The saved runs are listed under the reason, so it does not offer them itself.
 */
const KNOWN_REASONS: readonly { match: RegExp; text: (n: string) => string }[] = [
  { match: /^Argus is paused: new runs are switched off\./, text: () => "New live runs are switched off right now." },
  {
    match: /^Today's limit of (\d+) live runs is used up\./,
    text: (n) => `Today's limit of ${n} live runs is used up. Try again tomorrow (India time).`,
  },
  {
    match: /^This hour's limit of (\d+) live runs is used up\./,
    text: (n) => `This hour's limit of ${n} live runs is used up. Try again after the hour (India time).`,
  },
  {
    match: /^You have used your (\d+) live runs for today\./,
    text: (n) => `You have used your ${n} live runs for today. Try again tomorrow (India time).`,
  },
  {
    match: /^Argus is busy with other reviews right now\./,
    text: () => "Argus is busy with other reviews right now. Try again in a few seconds.",
  },
  { match: /^The database is not available right now\./, text: () => "Argus's database is not available right now. Try again shortly." },
  { match: /^Run limits (are not configured|in \S+ are malformed)/, text: () => "Live runs are not set up on the server right now." },
  // Pasted-clause runs only (runs.py CUSTOM_OFF and the custom cap in admit_counted).
  { match: /^Custom clause review is switched off right now\./, text: () => "Pasting your own clause is switched off right now." },
  {
    match: /^Today's limit of (\d+) custom clause reviews is used up\./,
    text: (n) => `Today's limit of ${n} pasted-clause reviews is used up. Try a sample policy, or paste a clause again tomorrow (India time).`,
  },
];

/** What the status means when the API gave no reason we know. */
const STATUS_REASONS: Readonly<Record<number, string>> = {
  429: "The live run limit has been reached for now. Try again later.",
  502: "The review service did not answer properly. Try again shortly.",
  503: "The review service is not available right now. Try again shortly.",
};

/** Why live review is unavailable, in plain English: a known API reason in our words, else what the status means. */
export function unavailableReason(status: number, detail: string | undefined): string {
  const text = detail?.trim() ?? "";
  for (const { match, text: say } of KNOWN_REASONS) {
    const m = match.exec(text);
    if (m) return say(m[1] ?? "");
  }
  return STATUS_REASONS[status] ?? "Live review cannot start right now. Try again shortly.";
}

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
    // The banner's heading already says live review is unavailable; the detail says why, in our words.
    return { kind: "unavailable", message: unavailableReason(e.status, e.detail) };
  }
  if (e.kind === "network") {
    return { kind: "error", title: "Argus could not reach its review service.", message: e.message };
  }
  if (e.kind === "auth") {
    return { kind: "error", title: "Your session could not be verified.", message: e.message };
  }
  return { kind: "error", title: "The live run could not be started.", message: e.message };
}
