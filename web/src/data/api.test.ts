import { afterEach, describe, expect, it, vi } from "vitest";
import { NOTE_MAX_CHARS, RUN_ID_PATTERN } from "../generated/schema";

vi.mock("../firebase", () => ({ getIdToken: vi.fn(() => Promise.resolve("FAKE-TOKEN")) }));
vi.mock("../env", () => ({ appEnv: () => ({ apiBase: "http://api.test" }) }));

const { ApiError, messageForStatus, newRunId, signoff, startRun } = await import("./api");

function respond(status: number, body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(typeof body === "string" ? body : JSON.stringify(body), { status }))),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("newRunId", () => {
  it("matches the API's run id pattern", () => {
    for (let i = 0; i < 50; i++) expect(newRunId()).toMatch(RUN_ID_PATTERN);
  });
});

describe("startRun", () => {
  it("posts run_id and policy_id with the bearer token", async () => {
    const id = newRunId();
    respond(200, { run_id: id, status: "done" });
    await expect(startRun(id, "policy_a")).resolves.toEqual({ run_id: id, status: "done" });
    const call = vi.mocked(fetch).mock.calls[0];
    expect(call?.[0]).toBe("http://api.test/api/runs");
    const init = call?.[1];
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer FAKE-TOKEN");
    expect(JSON.parse(init?.body as string)).toEqual({ run_id: id, policy_id: "policy_a" });
  });
  it("turns an HTTP error into an ApiError with the server's detail", async () => {
    respond(429, { detail: "FAKE daily cap reached" });
    const err = await startRun(newRunId(), "policy_a").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ kind: "http", status: 429, detail: "FAKE daily cap reached" });
    expect((err as Error).message).toContain("FAKE daily cap reached");
  });
  it("formats FastAPI validation errors", async () => {
    respond(422, { detail: [{ loc: ["body", "policy_id"], msg: "Field required" }] });
    await expect(startRun(newRunId(), "x")).rejects.toThrow("body.policy_id: Field required");
  });
  it("reports an unreachable API as a network error", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(startRun(newRunId(), "x")).rejects.toMatchObject({ kind: "network" });
  });
  it("refuses a run id the API would reject, before calling it", async () => {
    respond(200, {});
    await expect(startRun("not-a-uuid", "x")).rejects.toBeInstanceOf(ApiError);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("signoff", () => {
  it("posts the decision to the clause's signoff URL", async () => {
    respond(200, { clause_id: "c1" });
    await signoff("r1", "c1", { decision: "accept", note: "" });
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe("http://api.test/api/runs/r1/findings/c1/signoff");
  });
  it("requires a rationale for an edit and enforces the note limit", async () => {
    respond(200, {});
    await expect(signoff("r", "c", { decision: "edit", note: "" })).rejects.toBeInstanceOf(ApiError);
    await expect(signoff("r", "c", { decision: "accept", note: "x".repeat(NOTE_MAX_CHARS + 1) })).rejects.toBeInstanceOf(ApiError);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("messageForStatus", () => {
  it("falls back to the status code for unknown statuses", () => {
    expect(messageForStatus(418, undefined)).toContain("418");
  });
});
