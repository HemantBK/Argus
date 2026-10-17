import { describe, expect, it } from "vitest";
import { ApiError, messageForStatus } from "../data/api";
import { startProblem } from "./startProblem";

describe("startProblem", () => {
  it("is null while starting, running or done", () => {
    expect(startProblem({ status: "idle" })).toBeNull();
    expect(startProblem({ status: "waiting_for_listeners" })).toBeNull();
    expect(startProblem({ status: "posting" })).toBeNull();
    expect(startProblem({ status: "done" })).toBeNull();
  });
  it("treats quota, model failure and the kill switch as 'live review unavailable', with the API's own reason", () => {
    for (const status of [429, 502, 503]) {
      const message = messageForStatus(status, "FAKE detail");
      expect(startProblem({ status: "error", error: new ApiError("http", message, status, "FAKE detail") })).toEqual({ kind: "unavailable", message: "FAKE detail" });
      // No reason from the API: the plain-English sentence for the status.
      expect(startProblem({ status: "error", error: new ApiError("http", messageForStatus(status, undefined), status) })).toEqual({
        kind: "unavailable",
        message: messageForStatus(status, undefined),
      });
    }
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
