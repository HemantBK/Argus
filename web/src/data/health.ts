// GET /health on the Argus API, read once when the page loads, for one thing: the agent model's circuit breaker
// (backend breaker.py). While it is open, new clauses go to the backup model, so the Start screen says Gemini is
// busy. The read is a courtesy, never a dependency: any failure (no network, CORS, a non-JSON answer, an unknown
// value) gives null and the screen shows nothing extra. No sign-in token is sent; /health needs none.
import { useEffect, useState } from "react";
import { appEnv } from "../env";

export type BreakerState = "closed" | "open" | "half_open";

const STATES: readonly BreakerState[] = ["closed", "open", "half_open"];

/** How long the read may take before it is given up (silently). */
export const HEALTH_TIMEOUT_MS = 5_000;

/** The agent_breaker field of a /health body, or null when it is missing or not a known state. */
export function breakerOf(body: unknown): BreakerState | null {
  if (typeof body !== "object" || body === null) return null;
  const value = (body as { agent_breaker?: unknown }).agent_breaker;
  return STATES.find((s) => s === value) ?? null;
}

/** The agent model's breaker state from `${apiBase}/health`, or null on any failure. Never throws. */
export async function fetchAgentBreaker(apiBase: string, fetchImpl: typeof fetch = fetch): Promise<BreakerState | null> {
  try {
    const res = await fetchImpl(`${apiBase}/health`, { signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS) });
    if (!res.ok) return null;
    return breakerOf(await res.json());
  } catch {
    return null;
  }
}

/** The breaker state read once on load; null until it is read, and null for good when it cannot be read. */
export function useAgentBreaker(): BreakerState | null {
  const [state, setState] = useState<BreakerState | null>(null);
  useEffect(() => {
    let live = true;
    let apiBase: string;
    try {
      apiBase = appEnv().apiBase;
    } catch {
      return; // a missing setting is reported by the shell itself; this read stays silent
    }
    void fetchAgentBreaker(apiBase).then((s) => {
      if (live) setState(s);
    });
    return () => {
      live = false;
    };
  }, []);
  return state;
}
