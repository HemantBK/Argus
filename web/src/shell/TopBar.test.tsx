// @vitest-environment jsdom
// Top bar and run banners (UI_SPEC 2, 5 honest labels, 6 states).
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { run } from "../data/fixtures.test-util";
import { RunFailedBanner, StartProblemBanner } from "./banners";
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
  it("live review unavailable offers the saved run with its recording time", () => {
    render(<StartProblemBanner problem={{ kind: "unavailable", message: "FAKE quota reached" }} saved={saved} onRetry={() => undefined} />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(`Live review is temporarily unavailable. Open the saved run recorded at ${recordedAt(saved)}?`);
    expect(alert.textContent).toMatch(/FAKE quota reached/);
    expect(screen.getByRole("link").getAttribute("href")).toBe(href.review({ source: "samples", runId: saved.run_id }));
  });

  it("a start error says what failed and that no saved run exists when none does", () => {
    render(<StartProblemBanner problem={{ kind: "error", title: "FAKE title", message: "FAKE message" }} saved={null} onRetry={() => undefined} />);
    expect(screen.getByRole("alert").textContent).toMatch(/FAKE title.*FAKE message.*No saved run of this policy is available yet/s);
  });

  it.each([
    { kind: "error", title: "FAKE title", message: "FAKE message" },
    { kind: "unavailable", message: "FAKE quota reached" },
  ] as const)("every start problem ($kind) offers Try again", (problem) => {
    const onRetry = vi.fn();
    render(<StartProblemBanner problem={problem} saved={saved} onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("a failed run shows its recorded reason and offers the saved run", () => {
    const failed = run({ status: "failed", error: "FAKE run budget exceeded" });
    render(<RunFailedBanner run={failed} saved={saved} />);
    expect(screen.getByRole("alert").textContent).toMatch(/This run failed.*Reason recorded: FAKE run budget exceeded/s);
    expect(screen.getByRole("link", { name: `Open the saved run recorded at ${recordedAt(saved)}` })).toBeTruthy();
  });
});
