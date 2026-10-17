// GET /health for the Start screen's "Gemini is busy" note: only a known breaker state counts, and every failure
// is silent (null), never an error on screen.
import { describe, expect, it, vi } from "vitest";
import { breakerOf, fetchAgentBreaker } from "./health";

const answer = (status: number, body: string): typeof fetch => vi.fn(() => Promise.resolve(new Response(body, { status })));

describe("breakerOf", () => {
  it.each(["closed", "open", "half_open"])("reads %s", (state) => {
    expect(breakerOf({ status: "ok", agent_breaker: state })).toBe(state);
  });
  it.each([[null], [{}], [{ agent_breaker: "OPEN" }], [{ agent_breaker: 1 }], ["open"]])("gives null for %j", (body) => {
    expect(breakerOf(body)).toBeNull();
  });
});

describe("fetchAgentBreaker", () => {
  it("asks <apiBase>/health without a token and reads agent_breaker", async () => {
    const f = answer(200, JSON.stringify({ status: "ok", agent_breaker: "open" }));
    await expect(fetchAgentBreaker("http://api.test", f)).resolves.toBe("open");
    expect(vi.mocked(f).mock.calls[0]?.[0]).toBe("http://api.test/health");
    expect(vi.mocked(f).mock.calls[0]?.[1]?.headers).toBeUndefined();
  });

  it.each([
    ["an HTTP error", answer(503, JSON.stringify({ agent_breaker: "open" }))],
    ["a body that is not JSON", answer(200, "<html>FAKE</html>")],
    ["no agent_breaker", answer(200, JSON.stringify({ status: "ok" }))],
    ["a rejected fetch", vi.fn(() => Promise.reject(new TypeError("FAKE Failed to fetch"))) as unknown as typeof fetch],
  ])("fails silently on %s", async (_, f) => {
    await expect(fetchAgentBreaker("http://api.test", f)).resolves.toBeNull();
  });
});
