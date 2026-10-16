import { describe, expect, it } from "vitest";
import { DEFAULT_CONDITIONS, DEFAULT_METRICS, EvalContractError, evalTable, formatMetric, parseEvalResults } from "./evalResults";
import { fakeEval } from "../shell/screens.test-util";

describe("parseEvalResults", () => {
  it("reads a well-formed document", () => {
    const r = parseEvalResults(fakeEval());
    expect(r.results).toHaveLength(2);
    expect(r.corpus?.documents[0]?.instrument_ref).toBe("FAKE/0001");
    expect(r.failure_cases).toEqual(["FAKE failure case one."]);
  });
  it("refuses a measured number without its sample size", () => {
    const doc = fakeEval({ results: [{ metric: "latency", condition: "full_argus", value: 12, n_label: null }] });
    expect(() => parseEvalResults(doc)).toThrow(EvalContractError);
    expect(() => parseEvalResults(doc)).toThrow(/n_label/);
  });
  it("refuses cells for unknown metrics or conditions, unknown units and missing dates", () => {
    expect(() => parseEvalResults(fakeEval({ results: [{ metric: "nope", condition: "full_argus", value: null }] }))).toThrow(/metric/);
    expect(() => parseEvalResults(fakeEval({ results: [{ metric: "latency", condition: "nope", value: null }] }))).toThrow(/condition/);
    expect(() => parseEvalResults(fakeEval({ metrics: [{ id: "x", label: "X", unit: "furlongs" }], results: [] }))).toThrow(/unit/);
    expect(() => parseEvalResults(fakeEval({ run_date: "" }))).toThrow(/run_date/);
    expect(() => parseEvalResults(fakeEval({ results: [{ metric: "latency", condition: "full_argus", value: -1, n_label: "FAKE" }] }))).toThrow(/value/);
  });
  it("accepts a document with no corpus or failure cases yet", () => {
    const r = parseEvalResults(fakeEval({ corpus: null, failure_cases: undefined }));
    expect(r.corpus).toBeNull();
    expect(r.failure_cases).toEqual([]);
  });
});

describe("evalTable", () => {
  it("shows the spec's conditions and metrics, all unmeasured, when nothing is stored", () => {
    const t = evalTable(null);
    expect(t.conditions).toBe(DEFAULT_CONDITIONS);
    expect(t.metrics).toBe(DEFAULT_METRICS);
    for (const m of t.metrics) for (const c of t.conditions) expect(t.cell(m.id, c.id)).toBeNull();
  });
  it("finds recorded cells and returns null for the rest", () => {
    const t = evalTable(parseEvalResults(fakeEval()));
    expect(t.cell("citation_accuracy", "full_argus")?.value).toBe(0.5);
    expect(t.cell("latency", "full_argus")).toBeNull();
  });
});

describe("formatMetric", () => {
  it("formats each unit", () => {
    expect(formatMetric(0.5, "ratio")).toBe("50%");
    expect(formatMetric(12.34, "seconds")).toBe("12.3 s");
    expect(formatMetric(3, "count")).toBe("3");
    expect(formatMetric(0.0123, "usd")).toBe("$0.0123");
  });
});
