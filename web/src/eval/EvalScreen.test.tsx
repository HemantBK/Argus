// @vitest-environment jsdom
// Evaluation page (UI_SPEC 3.4, 16.1): every number with its n; while eval_results/latest is absent, an honest
// pointer to the official evaluation in the repository, with its run ids and no number.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { formatDate } from "../shell/format";
import { fakeEval, live } from "../shell/screens.test-util";
import { formatMetric, parseEvalResults } from "./evalResults";
import { EvalView, NONE_PUBLISHED, NOT_MEASURED, NOT_PUBLISHED } from "./EvalScreen";
import { EVALUATION_DOC_URL, EVALUATION_EVIDENCE_URL, OFFICIAL_RUNS } from "./officialEvaluation";

afterEach(cleanup);

/** Wordings that say no evaluation exists or was run, false since the official runs of 17 Oct (W22). */
const NO_EVALUATION_CLAIMS: readonly RegExp[] = [
  /\bno (official )?evaluation( run)?s? (exists?|existed|has been (run|recorded)|have been (run|recorded)|was run|were run|run has|yet)\b/i,
  /\bevaluation (has|have|was|were|is) not (yet )?(been )?run\b/i,
  /\bevaluation (hasn't|haven't|wasn't|weren't|isn't) (yet )?(been )?run\b/i,
  /\bnot (yet )?(been )?evaluated\b/i,
  /\bno evaluation (results|numbers|figures)\b/i,
];

/** A figure in words: a percentage, a decimal ("point nine") or a tens word ("ninety-one"). */
const NUMBER_IN_WORDS =
  /%|\bper ?cent\b|\bpoint (zero|one|two|three|four|five|six|seven|eight|nine)\b|\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\b/i;

function bodyCells(): HTMLElement[] {
  const table = screen.getByRole("table");
  return within(table).getAllByRole("cell");
}

describe("EvalView", () => {
  describe("when no evaluation is published to the page (eval_results/latest absent)", () => {
    it("points to the official evaluation in the repository, with every run id, and never says none was run", () => {
      render(<EvalView latest={live.ready(null)} fallbackAsOn="2026-07-15" />);
      const pointer = screen.getByRole("region", { name: "Official evaluation" });
      // The sentences as written, not imported, so a rewording has to pass the claims ledger (W22) again.
      expect(pointer.textContent).toContain("No evaluation has been published to this page.");
      expect(pointer.textContent).toContain(
        "The official evaluation (build 7c3072d, 17 Oct 2026, on Vertex AI; the review logic it measured is unchanged in the running build) is published in the repository instead",
      );
      expect(pointer.textContent).toContain("Its numbers are kept there, so that one source states them.");
      expect(NONE_PUBLISHED).toBe("No evaluation has been published to this page.");

      const doc = within(pointer).getByRole("link", { name: "docs/evaluation.md" });
      expect(doc.getAttribute("href")).toBe("https://github.com/HemantBK/Argus/blob/main/docs/evaluation.md");
      const evidence = within(pointer).getByRole("link", { name: "evidence/2026-10-17/eval-official/" });
      expect(evidence.getAttribute("href")).toBe("https://github.com/HemantBK/Argus/tree/main/evidence/2026-10-17/eval-official");
      expect([doc.getAttribute("href"), evidence.getAttribute("href")]).toEqual([EVALUATION_DOC_URL, EVALUATION_EVIDENCE_URL]);
      for (const link of [doc, evidence]) {
        expect(link.getAttribute("target")).toBe("_blank");
        expect(link.getAttribute("rel")).toBe("noopener noreferrer");
      }

      const runs = within(pointer).getAllByRole("listitem").map((li) => li.textContent);
      expect(runs).toEqual(OFFICIAL_RUNS.map((r) => `${r.label}: ${r.runId}`));
      expect(runs).toHaveLength(6);

      const page = document.body.textContent;
      expect(page).not.toMatch(/not measured yet/i);
      expect(page).not.toMatch(/No failure cases recorded yet/i);
      expect(page).not.toMatch(/once (the evaluation has run|they are measured)/i);
      for (const claim of NO_EVALUATION_CLAIMS) expect(page).not.toMatch(claim);
    });

    it("says nowhere on the page that no evaluation exists or was run", () => {
      // Each pattern must catch a wording that was or could be written; NONE_PUBLISHED (published *to this page*)
      // must not trip them, or the page could never pass.
      for (const claim of NO_EVALUATION_CLAIMS) expect(NONE_PUBLISHED).not.toMatch(claim);
      for (const wrong of [
        "No evaluation run has been recorded yet.",
        "No evaluation exists yet.",
        "No evaluation has been run.",
        "No official evaluation has been run yet.",
        "The evaluation has not been run yet.",
        "The evaluation hasn't run.",
        "Argus has not been evaluated yet.",
        "There are no evaluation results.",
        "Not evaluated yet.",
      ]) {
        expect(NO_EVALUATION_CLAIMS.some((claim) => claim.test(wrong)), wrong).toBe(true);
      }
      render(<EvalView latest={live.ready(null)} fallbackAsOn="2026-07-15" />);
      const page = document.body.textContent;
      for (const claim of NO_EVALUATION_CLAIMS) expect(page).not.toMatch(claim);
    });

    it("states no number from the evaluation anywhere on the page: only run ids, run names, the build and dates", () => {
      render(<EvalView latest={live.ready(null)} fallbackAsOn="2026-07-15" />);
      expect(screen.queryByRole("table")).toBeNull();
      // The whole page, so a figure added to the intro, the failure cases or the corpus box is caught too.
      let text = document.body.textContent;
      for (const r of OFFICIAL_RUNS) text = text.replace(r.runId, "");
      text = text.replace("7c3072d", "").replace("17 Oct 2026", "").replaceAll("2026-10-17", "");
      // The corpus box's "As on" date, from the saved runs.
      text = text.replace(formatDate("2026-07-15"), "");
      // R1..R5 labels are run names, not results.
      text = text.replace(/\bR[1-5]\b/g, "");
      expect(text).not.toMatch(/\d/);
      // A result written in words: a percentage, a decimal or a tens figure ("ninety-one percent").
      expect(text).not.toMatch(NUMBER_IN_WORDS);
    });

    it("sends the failure cases to docs/evaluation.md", () => {
      render(<EvalView latest={live.ready(null)} fallbackAsOn="2026-07-15" />);
      const failures = screen.getByRole("region", { name: "Failure cases" });
      expect(failures.textContent).toBe("Failure cases" + "The official evaluation's misses are described in docs/evaluation.md.");
      expect(within(failures).getByRole("link", { name: "docs/evaluation.md" }).getAttribute("href")).toBe(EVALUATION_DOC_URL);
    });

    it("fills the corpus box from the saved runs' date, and says not published here for the rest", () => {
      render(<EvalView latest={live.ready(null)} fallbackAsOn="2026-07-15" />);
      const box = screen.getByRole("region", { name: "RBI library used" });
      expect(within(box).getByText(formatDate("2026-07-15"))).toBeTruthy();
      expect(within(box).getAllByText(NOT_PUBLISHED)).toHaveLength(3);
      expect(within(box).queryByText(NOT_MEASURED)).toBeNull();
    });
  });

  it("shows not measured yet for a metric a stored evaluation did not measure", () => {
    const doc = fakeEval();
    const parsed = parseEvalResults(doc);
    render(<EvalView latest={live.ready(doc)} fallbackAsOn={null} />);
    const cells = parsed.conditions.length * parsed.metrics.length;
    const measured = parsed.results.filter((r) => r.value !== null).length;
    expect(bodyCells()).toHaveLength(cells);
    expect(within(screen.getByRole("table")).getAllByText(NOT_MEASURED)).toHaveLength(cells - measured);
    expect(screen.queryByRole("region", { name: "Official evaluation" })).toBeNull();
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
