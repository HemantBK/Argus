import { afterEach, describe, expect, it, vi } from "vitest";
import { listenError, ownedRun } from "./hooks";
import type { RunDoc } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("listenError", () => {
  it("tells the user what happened in plain English and keeps the rules engine's text out of the message", () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const raw = "\nevaluation error at L20:22 for 'get' @ L20, Null value error. for 'get' @ L20";
    const e = listenError("runs/FAKE", { code: "permission-denied", message: raw });
    expect(e.message).toBe(
      "Could not read runs/FAKE (permission-denied). You do not have access to this run (live runs are visible only to the browser that started them).",
    );
    expect(e.message).not.toContain("evaluation error");
    expect(e.detail).toBe(raw);
    expect(log).toHaveBeenCalledOnce();
  });

  it("still explains a code it has no specific hint for", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(listenError("samples", { code: "internal", message: "FAKE raw" }).message).toBe(
      "Could not read samples (internal). Firestore stopped sending updates for it.",
    );
  });
});

describe("ownedRun", () => {
  const fake = (id: string) => ({ id, data: () => ({ run_id: id }) as unknown as RunDoc });

  it("is null while the owner query returns nothing (run not created yet, or another user's)", () => {
    expect(ownedRun("runs/FAKE", "FAKE", [])).toBeNull();
  });

  it("returns the run the owner query found", () => {
    expect(ownedRun("runs/FAKE", "FAKE", [fake("FAKE")])).toEqual({ run_id: "FAKE" });
  });

  it("refuses a result that is not exactly the requested run", () => {
    expect(() => ownedRun("runs/FAKE", "FAKE", [fake("FAKE-OTHER")])).toThrow(/expected only FAKE/);
    expect(() => ownedRun("runs/FAKE", "FAKE", [fake("FAKE"), fake("FAKE-2")])).toThrow(/FAKE, FAKE-2/);
  });
});
