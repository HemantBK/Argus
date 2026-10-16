// Calls to the Argus API on Cloud Run (D-33: the browser calls it directly; no Hosting rewrite). Every call
// carries the Firebase ID token. Every failure becomes an ApiError whose `message` is fit to show the officer.
import { appEnv } from "../env";
import { getIdToken } from "../firebase";
import { NOTE_MAX_CHARS, RATIONALE_MAX_CHARS, RUN_ID_PATTERN } from "../generated/schema";
import type { SignoffDecision } from "./types";

export type ApiErrorKind = "auth" | "network" | "http" | "bad_response";

export class ApiError extends Error {
  override name = "ApiError";
  constructor(
    readonly kind: ApiErrorKind,
    message: string,
    readonly status?: number,
    /** The server's own wording (FastAPI `detail`), kept for the audit of what went wrong. */
    readonly detail?: string,
  ) {
    super(message);
  }
}

/** A run id as the API requires it: uuid4 hex, 32 lowercase hex characters, no dashes. */
export function newRunId(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

function detailText(body: unknown): string | undefined {
  if (typeof body !== "object" || body === null) return undefined;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: unknown) => {
        if (typeof d === "object" && d !== null) {
          const { loc, msg } = d as { loc?: unknown; msg?: unknown };
          const where = Array.isArray(loc) ? loc.join(".") : "";
          return `${where}${where ? ": " : ""}${typeof msg === "string" ? msg : JSON.stringify(d)}`;
        }
        return String(d);
      })
      .join("; ");
  }
  return undefined;
}

/** Plain-English message for an HTTP failure; the server's detail is appended when it has one. */
export function messageForStatus(status: number, detail: string | undefined): string {
  const base: Record<number, string> = {
    400: "The request was refused.",
    401: "Your session could not be verified. Reload the page to sign in again.",
    403: "You are not allowed to do this on this run.",
    404: "Not found.",
    409: "This already exists or was already decided.",
    413: "The request is too large.",
    422: "The request was not valid.",
    429: "The run limit has been reached for now. Open a saved run instead, or try again later.",
    502: "The AI model call failed.",
    503: "Live review is temporarily unavailable. Open a saved run instead.",
  };
  const head = base[status] ?? `The server returned an error (HTTP ${status}).`;
  return detail ? `${head} ${detail}` : head;
}

async function post<T>(path: string, body: unknown, parse: (data: unknown) => T): Promise<T> {
  let token: string;
  try {
    token = await getIdToken();
  } catch (e) {
    throw new ApiError("auth", e instanceof Error ? e.message : String(e));
  }
  const url = `${appEnv().apiBase}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError(
      "network",
      `Could not reach the Argus API at ${appEnv().apiBase}. ${e instanceof Error ? e.message : String(e)}`,
    );
  }
  const text = await res.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      if (res.ok) throw new ApiError("bad_response", `The API returned something that is not JSON (HTTP ${res.status}).`, res.status);
    }
  }
  if (!res.ok) {
    const detail = detailText(data) ?? (text.trim().slice(0, 300) || undefined);
    throw new ApiError("http", messageForStatus(res.status, detail), res.status, detail);
  }
  try {
    return parse(data);
  } catch (e) {
    throw new ApiError("bad_response", `The API response did not match the contract: ${e instanceof Error ? e.message : String(e)}`, res.status);
  }
}

function requireObject(data: unknown): Record<string, unknown> {
  if (typeof data !== "object" || data === null) throw new Error("expected a JSON object");
  return data as Record<string, unknown>;
}

export interface StartRunResult {
  run_id: string;
  status: string;
}

/**
 * POST /api/runs. Attach the run listener for `runId` (useRun) BEFORE calling this (useStartRun does): the API
 * works inside the request and streams steps to Firestore while it runs.
 */
export async function startRun(runId: string, policyId: string): Promise<StartRunResult> {
  if (!RUN_ID_PATTERN.test(runId)) throw new ApiError("http", `Run id ${runId} is not a uuid4 hex string.`);
  return post("/api/runs", { run_id: runId, policy_id: policyId }, (data) => {
    const o = requireObject(data);
    if (o.run_id !== runId) throw new Error(`run_id ${String(o.run_id)} does not match ${runId}`);
    if (typeof o.status !== "string") throw new Error("status missing");
    return { run_id: runId, status: o.status };
  });
}

export interface SignoffBody {
  decision: SignoffDecision;
  note: string;
  edited_rationale?: string;
}

/** POST /api/runs/{id}/findings/{clause}/signoff. The updated finding arrives through the findings listener. */
export async function signoff(runId: string, clauseId: string, body: SignoffBody): Promise<void> {
  if (body.decision === "edit" && !body.edited_rationale?.trim()) {
    throw new ApiError("http", "An edit needs the officer's rationale.");
  }
  if (body.note.length > NOTE_MAX_CHARS) throw new ApiError("http", `The note is longer than ${NOTE_MAX_CHARS} characters.`);
  if ((body.edited_rationale?.length ?? 0) > RATIONALE_MAX_CHARS) {
    throw new ApiError("http", `The rationale is longer than ${RATIONALE_MAX_CHARS} characters.`);
  }
  await post(
    `/api/runs/${encodeURIComponent(runId)}/findings/${encodeURIComponent(clauseId)}/signoff`,
    body,
    (data) => {
      requireObject(data);
    },
  );
}
