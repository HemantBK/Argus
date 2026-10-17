// @vitest-environment jsdom
// useAgentBreaker: the Start screen's one read of GET /health on load. It reads once, gives null until the answer
// and for good on any failure, and never throws.
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const env: { apiBase: string | null } = vi.hoisted(() => ({ apiBase: "http://api.test" }));
vi.mock("../env", () => ({
  appEnv: () => {
    if (env.apiBase === null) throw new Error("FAKE missing setting VITE_API_BASE");
    return { apiBase: env.apiBase };
  },
}));

import { useAgentBreaker } from "./health";

function answering(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  return vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status })));
}

beforeEach(() => {
  env.apiBase = "http://api.test";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useAgentBreaker", () => {
  it("is null until /health answers, then the breaker state", async () => {
    const f = answering({ status: "ok", agent_breaker: "open" });
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useAgentBreaker());
    expect(result.current).toBeNull();
    await waitFor(() => {
      expect(result.current).toBe("open");
    });
    expect(f).toHaveBeenCalledTimes(1);
    expect(f.mock.calls[0]?.[0]).toBe("http://api.test/health");
  });

  it("reads /health once on load, not again when the screen re-renders", async () => {
    const f = answering({ status: "ok", agent_breaker: "half_open" });
    vi.stubGlobal("fetch", f);
    const { result, rerender } = renderHook(() => useAgentBreaker());
    await waitFor(() => {
      expect(result.current).toBe("half_open");
    });
    rerender();
    rerender();
    expect(f).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["an HTTP error", () => answering({ agent_breaker: "open" }, 503)],
    ["an unknown state", () => answering({ agent_breaker: "OPEN" })],
    ["a rejected fetch", () => vi.fn(() => Promise.reject(new TypeError("FAKE Failed to fetch")))],
  ])("stays null on %s", async (_, make) => {
    const f = make();
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useAgentBreaker());
    await waitFor(() => {
      expect(f).toHaveBeenCalledTimes(1);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current).toBeNull();
  });

  it("stays null and asks nothing when the API setting is missing (the shell reports that itself)", async () => {
    env.apiBase = null;
    const f = answering({ agent_breaker: "open" });
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useAgentBreaker());
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current).toBeNull();
    expect(f).not.toHaveBeenCalled();
  });

  it("a late answer after the screen is gone is harmless: no error, nothing shown", async () => {
    let answer: (r: Response) => void = () => undefined;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => (answer = resolve))));
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { result, unmount } = renderHook(() => useAgentBreaker());
    unmount();
    await act(async () => {
      answer(new Response(JSON.stringify({ agent_breaker: "open" }), { status: 200 }));
      await Promise.resolve();
    });
    expect(result.current).toBeNull();
    expect(errors).not.toHaveBeenCalled();
  });
});
