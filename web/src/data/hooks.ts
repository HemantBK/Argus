// Live Firestore reads. Every screen reads through these listeners; nothing polls and nothing writes.
//
// Each hook returns a Live<T> (idle / loading / ready / error) plus `attached`, true once the onSnapshot
// listener for the current runId has delivered its first snapshot (so it is live on the server).
//
// Live runs (UI_SPEC 16.4, 16.8). firestore.rules let a user read runs/{id} and its subcollections only when the
// run document exists and its owner_uid is theirs; a direct read of a run that does not exist yet is refused. So a
// live run is watched in two stages, neither of which can miss a write:
//   1. the run itself through the query `runs where owner_uid == me and run_id == id`, which the rules allow
//      before the document exists (it returns nothing until the API creates it). useStartRun waits for this
//      listener before it POSTs;
//   2. steps, findings and audit once that query returns the run. The API creates the run document before it
//      writes anything under it (admission), and a listener's first snapshot holds every document already
//      written, so nothing written before stage 2 attaches is lost.
// Another user's run id gives an empty result, which the screens show as "not created yet, or another browser's".
import {
  collection,
  where,
  doc,
  limit as limitTo,
  onSnapshot,
  orderBy,
  query,
  type FirestoreDataConverter,
  type Firestore,
  type FirestoreError,
  type Query,
  type Unsubscribe,
} from "firebase/firestore";
import { useEffect, useState } from "react";
import { db, ensureSignedIn } from "../firebase";
import { auditConverter, findingConverter, parseRun, runConverter, stepConverter } from "./convert";
import type { AuditDoc, FindingDoc, Live, RunDoc, RunSource, StepDoc } from "./types";

export type LiveRead<T> = Live<T> & { attached: boolean };

/**
 * A refused or failed Firestore listener. `message` is plain English for the screen; the raw Firestore text (for
 * example the rules engine's evaluation trace) is kept in `detail` and written to the console, never shown to the
 * user (UI_SPEC 11).
 */
export class ListenError extends Error {
  override name = "ListenError";
  constructor(
    readonly path: string,
    readonly code: string,
    message: string,
    readonly detail: string,
  ) {
    super(message);
  }
}

const LISTEN_HINT: Record<string, string> = {
  "permission-denied": "You do not have access to this run (live runs are visible only to the browser that started them).",
  unavailable: "Firestore is unreachable. Check your connection.",
  unauthenticated: "This browser is not signed in, so Firestore refused the read.",
  "resource-exhausted": "Firestore is over its usage limit right now. Try again in a minute.",
};

export function listenError(path: string, e: Pick<FirestoreError, "code" | "message">): ListenError {
  console.error(`Argus: listener on ${path} ended (${e.code})`, e);
  const hint = LISTEN_HINT[e.code] ?? "Firestore stopped sending updates for it.";
  return new ListenError(path, e.code, `Could not read ${path} (${e.code}). ${hint}`, e.message);
}

function asError(e: unknown): Error {
  return e instanceof Error ? e : new Error(String(e));
}

/** `uid` is the signed-in user's uid when the listener needs auth, else null. */
type Listen<T> = (onData: (data: T) => void, onError: (e: Error) => void, uid: string | null) => Unsubscribe;

/**
 * Shared listener plumbing. `key` identifies what is being read (null = nothing yet); `make` builds the
 * listener for that key and must depend only on it. State is stored per key, so a key change shows
 * "loading" at once without a synchronous setState in the effect.
 */
function useLive<T>(key: string | null, needsAuth: boolean, make: () => Listen<T>): LiveRead<T> {
  const [state, setState] = useState<{ key: string; live: Live<T> } | null>(null);

  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    let unsubscribe: Unsubscribe | undefined;
    const listen = make();
    const begin = async (): Promise<void> => {
      const uid = needsAuth ? (await ensureSignedIn()).uid : null;
      if (cancelled) return;
      unsubscribe = listen(
        (data) => {
          if (!cancelled) setState({ key, live: { status: "ready", data } });
        },
        (error) => {
          if (!cancelled) setState({ key, live: { status: "error", error } });
        },
        uid,
      );
    };
    begin().catch((e: unknown) => {
      if (!cancelled) setState({ key, live: { status: "error", error: asError(e) } });
    });
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
    // `make` is rebuilt every render but depends only on `key` by contract.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, needsAuth]);

  if (key === null) return { status: "idle", attached: false };
  if (state?.key !== key) return { status: "loading", attached: false };
  // A first snapshot means the listener is live (rules passed); later snapshots keep it attached.
  return { ...state.live, attached: state.live.status === "ready" };
}

function runKey(source: RunSource, runId: string | null): string | null {
  return runId === null ? null : `${source}/${runId}`;
}

/**
 * The run document; data is null while it does not exist yet (a live run before the API creates it) or, for a live
 * run, when it belongs to another user. Live runs are read through an owner-filtered query (see the top of this file).
 */
export function useRun(runId: string | null, source: RunSource): LiveRead<RunDoc | null> {
  return useLive(runKey(source, runId), source === "runs", () => (onData, onError, uid) => {
    const id = runId ?? "";
    if (source === "samples") {
      const ref = doc(db(), source, id).withConverter(runConverter);
      return onSnapshot(
        ref,
        (snap) => {
          try {
            onData(snap.exists() ? snap.data() : null);
          } catch (e) {
            onError(asError(e));
          }
        },
        (e) => {
          onError(listenError(ref.path, e));
        },
      );
    }
    const path = `runs/${id}`;
    if (uid === null) throw new Error(`Cannot watch ${path}: no signed-in user`);
    const q = liveRunQuery(db(), uid, id).withConverter(runConverter);
    return onSnapshot(
      q,
      (snap) => {
        try {
          onData(ownedRun(path, id, snap.docs));
        } catch (e) {
          onError(asError(e));
        }
      },
      (e) => {
        onError(listenError(path, e));
      },
    );
  });
}

/** The query that watches the live run runs/{runId} for its owner `uid`; the rules allow it before the run exists. */
export function liveRunQuery(firestore: Firestore, uid: string, runId: string): Query {
  return query(collection(firestore, "runs"), where("owner_uid", "==", uid), where("run_id", "==", runId));
}

/** The single document the owner query for runs/{id} returned, or null when it returned none. */
export function ownedRun(path: string, runId: string, docs: { id: string; data: () => RunDoc }[]): RunDoc | null {
  const [first, ...rest] = docs;
  if (!first) return null;
  if (rest.length > 0 || first.id !== runId) {
    const ids = docs.map((d) => d.id).join(", ");
    throw new Error(`Could not read ${path}: the owner query for run_id ${runId} returned the documents ${ids}, expected only ${runId}.`);
  }
  return first.data();
}

/**
 * A subcollection of a run. `run` is the same run's useRun result: for a live run the listener attaches only once
 * the run document exists (stage 2 at the top of this file). Until then it reports an empty list when the run is
 * known not to exist yet (nothing can be under it), and loading while the run itself is still loading.
 */
function useSubcollection<T>(
  runId: string | null,
  source: RunSource,
  run: LiveRead<RunDoc | null>,
  name: "steps" | "findings" | "audit",
  converter: FirestoreDataConverter<T>,
  ordered: boolean,
): LiveRead<T[]> {
  const key = runKey(source, runId);
  const open = key !== null && (source === "samples" || (run.status === "ready" && run.data !== null));
  const live = useLive<T[]>(open ? `${key}/${name}` : null, source === "runs", () => (onData, onError) => {
    const col = collection(db(), source, runId ?? "", name).withConverter(converter);
    const q = ordered ? query(col, orderBy("seq")) : col;
    return onSnapshot(
      q,
      (snap) => {
        try {
          onData(snap.docs.map((d) => d.data()));
        } catch (e) {
          onError(asError(e));
        }
      },
      (e) => {
        onError(listenError(col.path, e));
      },
    );
  });
  if (key === null || open) return live;
  if (run.status === "ready") return { status: "ready", data: [], attached: false };
  return { status: "loading", attached: false };
}

/** Live trace, ordered by seq. `run` is useRun(runId, source). */
export function useSteps(runId: string | null, source: RunSource, run: LiveRead<RunDoc | null>): LiveRead<StepDoc[]> {
  return useSubcollection<StepDoc>(runId, source, run, "steps", stepConverter, true);
}

/**
 * One finding per reviewed clause, unordered: use selectors.sortFindings with run.clauses for policy order.
 * `run` is useRun(runId, source).
 */
export function useFindings(runId: string | null, source: RunSource, run: LiveRead<RunDoc | null>): LiveRead<FindingDoc[]> {
  return useSubcollection<FindingDoc>(runId, source, run, "findings", findingConverter, false);
}

/** Audit log, ordered by seq (chronological, append-only). `run` is useRun(runId, source). */
export function useAudit(runId: string | null, source: RunSource, run: LiveRead<RunDoc | null>): LiveRead<AuditDoc[]> {
  return useSubcollection<AuditDoc>(runId, source, run, "audit", auditConverter, true);
}

export interface RunData {
  run: LiveRead<RunDoc | null>;
  steps: LiveRead<StepDoc[]>;
  findings: LiveRead<FindingDoc[]>;
  audit: LiveRead<AuditDoc[]>;
  /**
   * The run listener is live on the server for this runId, so POST /api/runs may be sent (UI_SPEC 16.4). The
   * subcollection listeners of a live run attach later, when the run document appears.
   */
  attached: boolean;
}

export function useRunData(runId: string | null, source: RunSource): RunData {
  const run = useRun(runId, source);
  const steps = useSteps(runId, source, run);
  const findings = useFindings(runId, source, run);
  const audit = useAudit(runId, source, run);
  return { run, steps, findings, audit, attached: run.attached };
}

export interface SavedRuns {
  runs: RunDoc[];
  /** Documents in samples/ that do not match the contract: shown as a warning, never silently dropped. */
  invalid: Error[];
}

/**
 * Saved sample runs (the public samples/ collection holds only saved runs), newest first, for the Start screen.
 * Ordered on one field so no composite index is needed.
 */
export function useSavedRuns(max = 10): LiveRead<SavedRuns> {
  return useLive(`samples?limit=${max}`, false, () => (onData, onError) => {
    const q = query(collection(db(), "samples"), orderBy("started_at", "desc"), limitTo(max));
    return onSnapshot(
      q,
      (snap) => {
        const result: SavedRuns = { runs: [], invalid: [] };
        for (const d of snap.docs) {
          try {
            result.runs.push(parseRun(d.data(), d.ref.path));
          } catch (e) {
            result.invalid.push(asError(e));
          }
        }
        onData(result);
      },
      (e) => {
        onError(listenError("samples", e));
      },
    );
  });
}

/**
 * eval_results/latest as stored (its schema is not in schema.py yet), or null when it does not exist, in which
 * case the Evaluation page says "not measured yet".
 */
export function useEvalLatest(): LiveRead<Record<string, unknown> | null> {
  return useLive("eval_results/latest", false, () => (onData, onError) => {
    const ref = doc(db(), "eval_results", "latest");
    return onSnapshot(
      ref,
      (snap) => {
        onData(snap.exists() ? snap.data() : null);
      },
      (e) => {
        onError(listenError(ref.path, e));
      },
    );
  });
}
