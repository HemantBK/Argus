import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ApiError, messageForStatus } from "../data/api";
import { startProblem, unavailableReason } from "./startProblem";

describe("startProblem", () => {
  it("is null while starting, running or done", () => {
    expect(startProblem({ status: "idle" })).toBeNull();
    expect(startProblem({ status: "waiting_for_listeners" })).toBeNull();
    expect(startProblem({ status: "posting" })).toBeNull();
    expect(startProblem({ status: "done" })).toBeNull();
  });
  it("treats quota, model failure and the kill switch as 'live review unavailable'", () => {
    for (const status of [429, 502, 503]) {
      const problem = startProblem({ status: "error", error: new ApiError("http", messageForStatus(status, undefined), status) });
      expect(problem?.kind).toBe("unavailable");
    }
  });
  it("says each reason the API gives in plain English, never the API's raw text", () => {
    // The API's sentences at the deployed build (runs.py RUNS_PAUSED and admit_counted, read_limits; main.py).
    const cases: [number, string, string][] = [
      [503, "Argus is paused: new runs are switched off. Open a saved run instead.", "New live runs are switched off right now."],
      [429, "Today's limit of 100 live runs is used up. Open a saved run, or try again tomorrow (India time).", "Today's limit of 100 live runs is used up. Try again tomorrow (India time)."],
      [429, "This hour's limit of 20 live runs is used up. Open a saved run, or try again after the hour (India time).", "This hour's limit of 20 live runs is used up. Try again after the hour (India time)."],
      [429, "You have used your 5 live runs for today. Open a saved run, or try again tomorrow (India time).", "You have used your 5 live runs for today. Try again tomorrow (India time)."],
      [503, "Argus is busy with other reviews right now. Please try again in a few seconds.", "Argus is busy with other reviews right now. Try again in a few seconds."],
      [503, "The database is not available right now. Please try again shortly.", "Argus's database is not available right now. Try again shortly."],
      [503, "Run limits are not configured: config/limits is missing", "Live runs are not set up on the server right now."],
      [503, "Run limits in config/limits are malformed: 2 problem(s)", "Live runs are not set up on the server right now."],
      [503, "Custom clause review is switched off right now.", "Pasting your own clause is switched off right now."],
      [429, "Today's limit of 5 custom clause reviews is used up. Try a sample policy, or paste a clause again tomorrow (India time).", "Today's limit of 5 pasted-clause reviews is used up. Try a sample policy, or paste a clause again tomorrow (India time)."],
    ];
    for (const [status, detail, said] of cases) {
      const error = new ApiError("http", messageForStatus(status, detail), status, detail);
      expect(startProblem({ status: "error", error })).toEqual({ kind: "unavailable", message: said });
      expect(unavailableReason(status, detail)).toBe(said);
    }
  });
  it("has its own words for every refusal sentence the backend's admission gate can send", () => {
    // Read from the backend source so that a new or reworded sentence fails here instead of falling to the status line.
    const runs = readFileSync(resolve(process.cwd(), "../backend/argus_api/runs.py"), "utf8");
    const constants = [...runs.matchAll(/^(?:RUNS_PAUSED|CUSTOM_OFF) = "([^"]+)"/gm)].map((m) => m[1] ?? "");
    const caps = [...runs.matchAll(/raise DailyCapReached\(\s*f"([^"]+)"/g)].map((m) => (m[1] ?? "").replace(/\{[^}]+\}/g, "7"));
    expect(constants).toHaveLength(2);
    expect(caps).toHaveLength(4);
    for (const sentence of [...constants, ...caps]) {
      for (const status of [429, 503]) {
        const said = unavailableReason(status, sentence);
        expect(said, sentence).not.toBe(unavailableReason(status, undefined));
        expect(said).not.toContain("Open a saved run");
      }
    }
  });
  it("never prints an unknown reason, a code or a proxy's error page: it says what the status means", () => {
    for (const detail of ["RUNS_PAUSED", "<html><body>502 Bad Gateway</body></html>", "FAKE detail", undefined, "  "]) {
      expect(unavailableReason(429, detail)).toBe("The live run limit has been reached for now. Try again later.");
      expect(unavailableReason(502, detail)).toBe("The review service did not answer properly. Try again shortly.");
      expect(unavailableReason(503, detail)).toBe("The review service is not available right now. Try again shortly.");
    }
    expect(startProblem({ status: "error", error: new ApiError("http", "FAKE", 503, "RUNS_PAUSED") })).toEqual({
      kind: "unavailable",
      message: "The review service is not available right now. Try again shortly.",
    });
  });
  it("names other failures in plain English and keeps the API's message", () => {
    const http = new ApiError("http", messageForStatus(422, "FAKE"), 422);
    expect(startProblem({ status: "error", error: http })).toMatchObject({ kind: "error", message: http.message });
    const net = new ApiError("network", "FAKE could not reach");
    expect(startProblem({ status: "error", error: net })).toMatchObject({ kind: "error", title: "Argus could not reach its review service." });
    const blocked = new Error("FAKE permission-denied");
    expect(startProblem({ status: "blocked", error: blocked })).toMatchObject({ kind: "error", message: blocked.message });
  });
});
