import { describe, expect, it } from "vitest";
import { href, parseRoute, runRefOf } from "./route";

const ID = "0123456789ab4def8123456789abcdef";

describe("parseRoute", () => {
  it("maps the empty hash and #/ to the start screen", () => {
    expect(parseRoute("")).toEqual({ screen: "start" });
    expect(parseRoute("#")).toEqual({ screen: "start" });
    expect(parseRoute("#/")).toEqual({ screen: "start" });
  });
  it("maps live runs, saved runs, their audit logs and the evaluation page", () => {
    expect(parseRoute(`#/run/${ID}`)).toEqual({ screen: "review", source: "runs", runId: ID });
    expect(parseRoute(`#/sample/${ID}`)).toEqual({ screen: "review", source: "samples", runId: ID });
    expect(parseRoute(`#/run/${ID}/audit`)).toEqual({ screen: "audit", source: "runs", runId: ID });
    expect(parseRoute(`#/sample/${ID}/audit`)).toEqual({ screen: "audit", source: "samples", runId: ID });
    expect(parseRoute("#/eval")).toEqual({ screen: "eval" });
    expect(parseRoute("#/eval/")).toEqual({ screen: "eval" });
  });
  it("reports anything else as not found instead of guessing", () => {
    for (const hash of ["#/runs/x", `#/run/${ID}/steps`, `#/run/${ID}/audit/x`, "#/run/", "#/run/a b", "#main"]) {
      expect(parseRoute(hash)).toEqual({ screen: "not_found", hash });
    }
  });
  it("round-trips every href it builds", () => {
    for (const source of ["runs", "samples"] as const) {
      const ref = { source, runId: ID };
      expect(runRefOf(parseRoute(href.review(ref)))).toEqual(ref);
      expect(parseRoute(href.audit(ref))).toEqual({ screen: "audit", ...ref });
    }
    expect(parseRoute(href.start())).toEqual({ screen: "start" });
    expect(parseRoute(href.eval())).toEqual({ screen: "eval" });
  });
});
