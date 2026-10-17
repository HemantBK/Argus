// Start a live run in the order the contract requires (UI_SPEC 16.4): sign in, generate the run id on the
// client, attach the run listener (an owner query, allowed before the run exists), and only then POST /api/runs.
// The steps/findings/audit listeners attach as soon as the API creates the run document (data/hooks.ts explains
// why that misses nothing). The POST stays open while the API works; results stream in through the listeners.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, CUSTOM_POLICY_ID, newRunId, startCustomRun, startRun, type CustomClause } from "./api";
import { useRunData, type RunData } from "./hooks";

export type PostState =
  | { status: "idle" }
  | { status: "waiting_for_listeners" }
  | { status: "posting" }
  | { status: "done" }
  | { status: "error"; error: ApiError }
  /** The run listener failed before the POST, so the run was not started (it could not have been watched). */
  | { status: "blocked"; error: Error };

export interface StartRun extends RunData {
  runId: string | null;
  post: PostState;
  /**
   * Begin a new live run and return its run id: a sample policy, or with `custom` a pasted clause
   * (policyId CUSTOM_POLICY_ID). The clause is held only in memory, for this request.
   */
  start: (policyId: string, custom?: CustomClause) => string;
}

interface RunRequest {
  runId: string;
  policyId: string;
  custom: CustomClause | undefined;
}

function send({ runId, policyId, custom }: RunRequest): ReturnType<typeof startRun> {
  if (custom) return startCustomRun(runId, custom);
  if (policyId === CUSTOM_POLICY_ID) {
    return Promise.reject(new ApiError("http", "No clause text was given for this custom run. Go back to the start screen and paste the clause again."));
  }
  return startRun(runId, policyId);
}

export function useStartRun(): StartRun {
  const [request, setRequest] = useState<RunRequest | null>(null);
  const [post, setPost] = useState<{ runId: string; state: PostState } | null>(null);
  const sent = useRef<string | null>(null);
  const data = useRunData(request?.runId ?? null, "runs");
  const listenerError = firstError(data);

  useEffect(() => {
    if (!request || !data.attached || listenerError || sent.current === request.runId) return;
    sent.current = request.runId;
    const { runId } = request;
    send(request).then(
      () => {
        setPost({ runId, state: { status: "done" } });
      },
      (e: unknown) => {
        const error = e instanceof ApiError ? e : new ApiError("network", e instanceof Error ? e.message : String(e));
        setPost({ runId, state: { status: "error", error } });
      },
    );
  }, [request, data.attached, listenerError]);

  const start = useCallback((policyId: string, custom?: CustomClause) => {
    const runId = newRunId();
    setRequest({ runId, policyId: custom ? CUSTOM_POLICY_ID : policyId, custom });
    return runId;
  }, []);

  let state: PostState = { status: "idle" };
  if (request) {
    if (post?.runId === request.runId) state = post.state;
    else if (listenerError) state = { status: "blocked", error: listenerError };
    else if (!data.attached) state = { status: "waiting_for_listeners" };
    else state = { status: "posting" };
  }
  return { ...data, runId: request?.runId ?? null, post: state, start };
}

function firstError(data: RunData): Error | null {
  for (const live of [data.run, data.steps, data.findings, data.audit]) {
    if (live.status === "error") return live.error;
  }
  return null;
}
