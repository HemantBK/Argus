// @vitest-environment jsdom
// Top bar and run banners (UI_SPEC 2, 5 honest labels, 6 states).
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { finding, run } from "../data/fixtures.test-util";
import { BusyRunBanner, PartialRunBanner, RunFailedBanner, StartProblemBanner } from "./banners";
import { MODEL_OUTPUT_PREFIX, PROVIDER_BUSY_PREFIX, savedRunOfferText } from "./busy";
import { libraryLabel, recordedAt, runLibrary, type LibraryDates } from "./format";
import { href } from "./route";
import { live } from "./screens.test-util";
import { TopBar } from "./TopBar";

afterEach(cleanup);

function runName(): string | null | undefined {
  return document.querySelector(".topbar-run")?.textContent;
}

const ID = "0123456789ab4def8123456789abcdef";
const saved = run({ run_id: ID, saved: true, policy_title: "FAKE FinFast NBFC (Synthetic)" });
const savedLibrary = runLibrary(saved);
const savedB = run({ run_id: "fedcba9876544321a123456789abcdef", saved: true, policy_id: "policy_b", policy_title: "FAKE LendRight NBFC (Synthetic)" });
const offers = [saved, savedB];
const legal = (dates: string): string => `Synthetic data only · Not legal advice · ${dates}`;

describe("TopBar", () => {
  it("without a run: Review and Audit are unavailable, Evaluation links, the legal label is there", () => {
    render(<TopBar route={{ screen: "start" }} runRef={null} run={null} library={savedLibrary} />);
    expect(screen.getByRole("link", { name: "Argus" }).getAttribute("href")).toBe(href.start());
    expect(screen.queryByRole("link", { name: "Review" })).toBeNull();
    expect(screen.getByText("Review").getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("link", { name: "Evaluation" }).getAttribute("href")).toBe(href.eval());
    if (!savedLibrary) throw new Error("the fixture run records its library dates");
    expect(document.querySelector(".topbar-legal")?.textContent).toBe(legal(libraryLabel(savedLibrary)));
  });

  it("shows both library dates of a run in force, and the withdrawal of a withdrawn instrument", () => {
    const inForce: LibraryDates = { kind: "in_force", updatedAsOn: saved.rbi_as_on ?? null, fetchedOn: saved.corpus_as_on };
    render(<TopBar route={{ screen: "start" }} runRef={null} run={null} library={inForce} />);
    expect(document.querySelector(".topbar-legal")?.textContent).toBe(legal("RBI text as updated on 15 Jul 2026 · fetched 16 Oct 2026"));
    cleanup();
    const withdrawn: LibraryDates = { kind: "withdrawn", withdrawnOn: "2025-11-28", withdrawnBy: "FAKE/2025-26/100", fetchedOn: saved.corpus_as_on };
    render(<TopBar route={{ screen: "start" }} runRef={null} run={null} library={withdrawn} />);
    expect(document.querySelector(".topbar-legal")?.textContent).toBe(legal("Withdrawn 28 Nov 2025 by FAKE/2025-26/100 · fetched 16 Oct 2026"));
    expect(document.body.textContent).not.toMatch(/snapshot/i);
  });

  it("names a saved run honestly and marks the current tab", () => {
    const ref = { source: "samples" as const, runId: ID };
    render(<TopBar route={{ screen: "audit", ...ref }} runRef={ref} run={live.ready(saved)} library={savedLibrary} />);
    expect(runName()).toBe(`${saved.policy_title} · saved run (recorded ${recordedAt(saved)})`);
    expect(screen.getByRole("link", { name: "Audit log" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Review" }).getAttribute("href")).toBe(href.review(ref));
  });

  it("labels a live run as live, and a live run not created yet as starting", () => {
    const ref = { source: "runs" as const, runId: ID };
    render(<TopBar route={{ screen: "review", ...ref }} runRef={ref} run={live.ready({ ...saved, saved: false })} library={null} />);
    expect(runName()).toBe(`${saved.policy_title} · live run`);
    expect(document.querySelector(".topbar-legal")?.textContent).toBe(legal("RBI library dates not loaded yet"));
    cleanup();
    render(<TopBar route={{ screen: "review", ...ref }} runRef={ref} run={live.ready(null)} library={null} />);
    expect(screen.getByText("Live run starting…")).toBeTruthy();
  });

  it("labels a live run whose start failed as not started, not as starting", () => {
    const ref = { source: "runs" as const, runId: ID };
    render(<TopBar route={{ screen: "review", ...ref }} runRef={ref} run={live.ready(null)} library={null} startFailed />);
    expect(runName()).toBe("Live run not started");
  });
});

describe("run banners", () => {
  it("live review unavailable offers the saved run of every sample policy, each named and dated", () => {
    render(<StartProblemBanner problem={{ kind: "unavailable", message: "FAKE quota reached" }} saved={saved} offers={offers} onRetry={() => undefined} />);
    const alert = screen.getByRole("alert");
    expect(alert.querySelector("strong")?.textContent).toBe("Live review is temporarily unavailable.");
    expect(alert.textContent).toMatch(/FAKE quota reached/);
    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual(offers.map(savedRunOfferText));
    expect(links.map((a) => a.getAttribute("href"))).toEqual(offers.map((r) => href.review({ source: "samples", runId: r.run_id })));
    expect(savedRunOfferText(saved)).toBe(`Open the saved run of ${saved.policy_title}, recorded at ${recordedAt(saved)}`);
  });

  it("live review unavailable with no saved run says so", () => {
    render(<StartProblemBanner problem={{ kind: "unavailable", message: "FAKE quota reached" }} saved={null} offers={[]} onRetry={() => undefined} />);
    expect(screen.getByRole("alert").textContent).toMatch(/No saved run is available yet/);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("a live run with clauses failed by busy Gemini says how many and offers every saved run", () => {
    render(<BusyRunBanner busyClauses={2} offers={offers} />);
    const status = screen.getByRole("status");
    expect(status.querySelector("strong")?.textContent).toBe("Gemini capacity was busy, so 2 clauses of this run could not be completed.");
    expect(status.textContent).toMatch(/Those clauses are marked/);
    expect(status.textContent).toContain("Start a new live run later, or open a saved run now.");
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(offers.map(savedRunOfferText));
    cleanup();
    render(<BusyRunBanner busyClauses={1} offers={[]} />);
    expect(screen.getByRole("status").textContent).toMatch(/so 1 clause of this run.*That clause is marked.*No saved run is available yet/s);
  });

  it("with no saved run, the busy banner does not tell the officer to open one", () => {
    render(<BusyRunBanner busyClauses={1} offers={[]} />);
    const text = screen.getByRole("status").textContent;
    expect(text).toContain("Start a new live run later.");
    expect(text).not.toMatch(/open a saved run/i);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("a start error says what failed and that no saved run exists when none does", () => {
    render(<StartProblemBanner problem={{ kind: "error", title: "FAKE title", message: "FAKE message" }} saved={null} offers={offers} onRetry={() => undefined} />);
    expect(screen.getByRole("alert").textContent).toMatch(/FAKE title.*FAKE message.*No saved run of this policy is available yet/s);
  });

  it.each([
    { kind: "error", title: "FAKE title", message: "FAKE message" },
    { kind: "unavailable", message: "FAKE quota reached" },
  ] as const)("every start problem ($kind) offers Try again", (problem) => {
    const onRetry = vi.fn();
    render(<StartProblemBanner problem={problem} saved={saved} offers={offers} onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  const partialSummary = { ...run().summary, clauses: 8, compliant: 4, gap: 3, failed: 1 };

  it("a partial run says how many clauses were reviewed and why each other one was not, as a status, not an alert", () => {
    const partial = run({ status: "done", summary: { ...partialSummary, compliant: 3, failed: 2 } });
    const findings = [
      finding({ clause_id: "c1" }),
      finding({ clause_id: "c5", error: `${MODEL_OUTPUT_PREFIX}; a person must review this clause. FAKE.` }),
      finding({ clause_id: "c6", error: `${PROVIDER_BUSY_PREFIX}; try again shortly. FAKE.` }),
    ];
    render(<PartialRunBanner run={partial} findings={findings} offers={offers} />);
    const status = screen.getByRole("status");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(status.classList.contains("run-banner-neutral")).toBe(true);
    expect(status.querySelector("strong")?.textContent).toBe("6 of 8 clauses reviewed; 2 need a person.");
    expect([...status.querySelectorAll("li")].slice(0, 2).map((li) => li.textContent)).toEqual([
      "c5: Gemini returned an unusable reply, so Argus did not guess.",
      "c6: Gemini was busy, so Argus did not finish this clause.",
    ]);
    expect(status.textContent).toContain("They are marked “Couldn't complete”. Start a new live run to try them again, or open a saved run (recorded earlier, not live).");
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(offers.map(savedRunOfferText));
    expect(status.textContent).not.toMatch(/failed/i);
  });

  it("a partial run with one failed clause of another cause, and no saved run, offers only a new live run", () => {
    const partial = run({ status: "failed", error: "FAKE 1 of 8 clauses failed", summary: { ...partialSummary, compliant: 4 } });
    render(<PartialRunBanner run={partial} findings={[finding({ clause_id: "c2", error: "FAKE: internal." })]} offers={[]} />);
    const status = screen.getByRole("status");
    expect(status.querySelector("strong")?.textContent).toBe("7 of 8 clauses reviewed; 1 needs a person.");
    expect(status.querySelector("li")?.textContent).toBe("c2: Argus could not finish this clause.");
    expect(status.textContent).toContain("It is marked “Couldn't complete”. Start a new live run to try it again.");
    expect(status.textContent).not.toMatch(/saved run/);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("a failed run shows its recorded reason and offers the saved run", () => {
    const failed = run({ status: "failed", error: "FAKE run budget exceeded" });
    render(<RunFailedBanner run={failed} saved={saved} />);
    expect(screen.getByRole("alert").textContent).toMatch(/This run failed.*Reason recorded: FAKE run budget exceeded/s);
    expect(screen.getByRole("link", { name: `Open the saved run recorded at ${recordedAt(saved)}` })).toBeTruthy();
  });
});
