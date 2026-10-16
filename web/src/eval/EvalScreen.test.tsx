// @vitest-environment jsdom
// Evaluation page (UI_SPEC 3.4, 16.1): "not measured yet" until data exists, every number with its n.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { formatDate } from "../shell/format";
import { fakeEval, live } from "../shell/screens.test-util";
import { DEFAULT_CONDITIONS, DEFAULT_METRICS, formatMetric, parseEvalResults } from "./evalResults";
import { EvalView, NOT_MEASURED } from "./EvalScreen";

afterEach(cleanup);

function bodyCells(): HTMLElement[] {
  const table = screen.getByRole("table");
  return within(table).getAllByRole("cell");
}

describe("EvalView", () => {
  it("shows every metric as not measured, and no number, when nothing is stored", () => {
    render(<EvalView latest={live.ready(null)} fallbackAsOn="2026-07-15" />);
    const cells = bodyCells();
    expect(cells).toHaveLength(DEFAULT_CONDITIONS.length * DEFAULT_METRICS.length);
    for (const c of cells) expect(c.textContent).toBe(NOT_MEASURED);
    expect(within(screen.getByRole("table")).queryByText(/\d/)).toBeNull();
    for (const c of DEFAULT_CONDITIONS) expect(screen.getByRole("columnheader", { name: c.label })).toBeTruthy();
    expect(screen.getByText(/No failure cases recorded yet/)).toBeTruthy();
  });

  it("fills the corpus box from data, with not measured yet for what is missing", () => {
    render(<EvalView latest={live.ready(null)} fallbackAsOn="2026-07-15" />);
    const box = screen.getByRole("region", { name: "RBI library used" });
    expect(within(box).getByText(formatDate("2026-07-15"))).toBeTruthy();
    expect(within(box).getAllByText(NOT_MEASURED)).toHaveLength(3);
  });

  it("shows a stored number with its sample size and the run date", () => {
    const doc = fakeEval();
    const parsed = parseEvalResults(doc);
    render(<EvalView latest={live.ready(doc)} fallbackAsOn={null} />);
    const measured = parsed.results.find((r) => r.value !== null);
    const metric = parsed.metrics.find((m) => m.id === measured?.metric);
    if (!measured || measured.value === null || !metric) throw new Error("FAKE eval has no measured cell");
    expect(screen.getByText(formatMetric(measured.value, metric.unit))).toBeTruthy();
    expect(screen.getByText(`on ${measured.n_label ?? ""}`)).toBeTruthy();
    expect(screen.getByText(`Measured on ${formatDate(parsed.run_date)}`)).toBeTruthy();
    const box = screen.getByRole("region", { name: "RBI library used" });
    expect(within(box).getByText(String(parsed.corpus?.paragraphs))).toBeTruthy();
    expect(screen.getByText(parsed.failure_cases[0] ?? "")).toBeTruthy();
  });

  it("refuses to show numbers from a document that breaks the contract", () => {
    render(<EvalView latest={live.ready(fakeEval({ results: [{ metric: "latency", condition: "full_argus", value: 9, n_label: null }] }))} fallbackAsOn={null} />);
    expect(screen.getByRole("alert").textContent).toMatch(/no numbers are shown.*n_label/s);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows skeleton cells while loading and read failures in plain view", () => {
    render(<EvalView latest={live.loading} fallbackAsOn={null} />);
    expect(screen.getByRole("table").getAttribute("aria-busy")).toBe("true");
    cleanup();
    render(<EvalView latest={live.error("FAKE offline")} fallbackAsOn={null} />);
    expect(screen.getByRole("alert").textContent).toMatch(/FAKE offline/);
  });
});
