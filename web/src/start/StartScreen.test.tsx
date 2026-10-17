// @vitest-environment jsdom
// Start screen in every state (UI_SPEC 3.1, 6, 16.1). Expected text is derived from the data and the
// production formatters, never typed in twice.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { run, T0 } from "../data/fixtures.test-util";
import { libraryLabel, runLibrary, savedRunLabel } from "../shell/format";
import { href } from "../shell/route";
import { live } from "../shell/screens.test-util";
import { DEMO_POLICY_ID } from "./catalog";
import { HEADLINE, TAGLINE } from "./copy";
import type { SamplePolicies, SamplePolicy } from "./samplePolicies";
import { StartScreen } from "./StartScreen";

afterEach(cleanup);

const clause = (i: number) => ({ clause_id: `c${i}`, title: `FAKE title ${i}`, text: `FAKE clause ${i}` });
const savedA = run({
  run_id: "fake-saved-a",
  policy_id: DEMO_POLICY_ID,
  policy_title: "FAKE FinFast NBFC (Synthetic)",
  saved: true,
  clauses: [1, 2, 3].map(clause),
  started_at: new Date(T0.getTime() + 60_000),
});
const NO_FILES: SamplePolicies = { policies: [], invalid: [] };
const fileA: SamplePolicy = { policyId: DEMO_POLICY_ID, title: "FAKE Sample NBFC A (file)", clauses: 8, synthetic: true, disclaimer: "SYNTHETIC. FAKE." };
const fileB: SamplePolicy = { policyId: "policy_b", title: "FAKE Sample NBFC B (file)", clauses: 7, synthetic: true, disclaimer: "SYNTHETIC. FAKE." };
const savedB = run({ run_id: "fake-saved-b", policy_id: "policy_b", policy_title: "FAKE LendRight NBFC (Synthetic)", saved: true, clauses: [1, 2].map(clause) });

describe("StartScreen", () => {
  it("says what Argus does and starts the demo policy from the big button", () => {
    const onRun = vi.fn();
    render(<StartScreen saved={live.ready({ runs: [savedA, savedB], invalid: [] })} onRun={onRun} />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(HEADLINE);
    expect(screen.getByText(TAGLINE).tagName).toBe("P");
    fireEvent.click(screen.getByRole("button", { name: "Run demo on a sample NBFC policy" }));
    expect(onRun).toHaveBeenCalledWith(DEMO_POLICY_ID);
  });

  it("shows a card for every sample policy file, with its saved runs, even before any saved run of it exists", () => {
    const onRun = vi.fn();
    render(
      <StartScreen saved={live.ready({ runs: [savedA], invalid: [] })} onRun={onRun} policies={{ policies: [fileB, fileA], invalid: [] }} />,
    );
    const cards = screen.getAllByRole("listitem").filter((li) => li.classList.contains("sample-card"));
    expect(cards).toHaveLength(2);
    const [a, b] = cards.map((c) => within(c));
    if (!a || !b) throw new Error("missing card");
    expect(a.getByRole("heading", { level: 3 }).textContent).toBe(fileA.title);
    expect(a.getByText(`${fileA.clauses} clauses`)).toBeTruthy();
    expect(a.getByRole("link", { name: savedRunLabel(savedA) })).toBeTruthy();
    expect(b.getByRole("heading", { level: 3 }).textContent).toBe(fileB.title);
    expect(b.getByText("Synthetic")).toBeTruthy();
    expect(b.getByText(`${fileB.clauses} clauses`)).toBeTruthy();
    expect(b.getByText(/No saved run yet/)).toBeTruthy();
    fireEvent.click(b.getByRole("button", { name: `Run live on ${fileB.title}` }));
    expect(onRun).toHaveBeenLastCalledWith(fileB.policyId);
  });

  it("shows the sample cards while the saved runs load, and says so on each card", () => {
    render(<StartScreen saved={live.loading} onRun={vi.fn()} policies={{ policies: [fileA, fileB], invalid: [] }} />);
    expect(screen.getAllByText("Loading saved runs…")).toHaveLength(2);
  });

  it("reports a sample policy file that cannot be read", () => {
    render(<StartScreen saved={live.ready({ runs: [], invalid: [] })} onRun={vi.fn()} policies={{ policies: [fileA], invalid: [new Error("samples/policy_x.json: title: expected a non-empty string")] }} />);
    expect(screen.getByRole("alert").textContent).toMatch(/1 sample policy file could not be read.*policy_x\.json: title/s);
  });

  it("still shows a card for a policy known only from saved runs, named and counted from its newest saved run", () => {
    const onRun = vi.fn();
    render(<StartScreen saved={live.ready({ runs: [savedB, savedA], invalid: [] })} onRun={onRun} policies={NO_FILES} />);
    const cards = screen.getAllByRole("listitem").filter((li) => li.classList.contains("sample-card"));
    expect(cards).toHaveLength(2);
    for (const [card, r] of [
      [cards[0], savedA],
      [cards[1], savedB],
    ] as const) {
      if (!card) throw new Error("missing card");
      const c = within(card);
      expect(c.getByRole("heading", { level: 3 }).textContent).toBe(r.policy_title);
      expect(c.getByText(`${r.clauses.length} clauses`)).toBeTruthy();
      const link = c.getByRole("link", { name: savedRunLabel(r) });
      expect(link.getAttribute("href")).toBe(href.review({ source: "samples", runId: r.run_id }));
      fireEvent.click(c.getByRole("button", { name: `Run live on ${r.policy_title}` }));
      expect(onRun).toHaveBeenLastCalledWith(r.policy_id);
    }
  });

  it("lists the newest saved runs and folds older ones away, dropping none", () => {
    const many = [0, 1, 2, 3].map((i) => ({ ...savedA, run_id: `fake-saved-a-${i}`, started_at: new Date(T0.getTime() + i * 60_000) }));
    render(<StartScreen saved={live.ready({ runs: many, invalid: [] })} onRun={vi.fn()} policies={NO_FILES} />);
    const links = screen.getAllByRole("link", { name: /Open saved run/ });
    expect(links).toHaveLength(many.length);
    const older = screen.getByText(/older saved runs?$/);
    expect(older.tagName).toBe("SUMMARY");
    const shown = links.filter((l) => !l.closest("details"));
    expect(shown[0]?.textContent).toBe(savedRunLabel(many[3] ?? savedA));
    expect(older.textContent).toBe(`${many.length - shown.length} older saved runs`);
  });

  it("has the footer with the library dates of the newest saved run", () => {
    render(<StartScreen saved={live.ready({ runs: [savedA, savedB], invalid: [] })} onRun={vi.fn()} />);
    const footer = screen.getByRole("contentinfo");
    const library = runLibrary(savedA);
    if (!library) throw new Error("the fixture run records its library dates");
    expect(footer.textContent).toBe(`${libraryLabel(library)} · Synthetic data only · Not legal advice`);
    expect(footer.textContent).toMatch(/^RBI text as updated on .+ · fetched .+ · Synthetic/);
    expect(document.body.textContent).not.toMatch(/snapshot/i);
  });

  it("a run recorded before rbi_as_on existed shows only the fetch date, never a guessed update date", () => {
    const old = { ...savedA, rbi_as_on: null };
    render(<StartScreen saved={live.ready({ runs: [old], invalid: [] })} onRun={vi.fn()} policies={NO_FILES} />);
    expect(screen.getByRole("contentinfo").textContent).toBe(`${libraryLabel({ kind: "in_force", updatedAsOn: null, fetchedOn: old.corpus_as_on })} · Synthetic data only · Not legal advice`);
    expect(screen.getByRole("contentinfo").textContent).not.toMatch(/updated on/);
  });

  it("offers no upload, and one paste box for a single clause (D-27, D-60)", () => {
    const { container } = render(<StartScreen saved={live.ready({ runs: [savedA], invalid: [] })} onRun={vi.fn()} />);
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(within(screen.getByRole("region", { name: "Try your own clause" })).getByRole("textbox")).toBeTruthy();
    expect(container.querySelector("input[type=file]")).toBeNull();
  });

  it("shows skeleton cards while loading, not blank space", () => {
    render(<StartScreen saved={live.loading} onRun={vi.fn()} policies={NO_FILES} />);
    expect(screen.getByLabelText("Loading sample policies").getAttribute("aria-busy")).toBe("true");
    expect(screen.getByRole("contentinfo").textContent).toMatch(/RBI library dates loading/);
    // The demo still works while saved runs load.
    expect(screen.getByRole("button", { name: "Run demo on a sample NBFC policy" })).toBeTruthy();
  });

  it("shows a load failure in plain view", () => {
    render(<StartScreen saved={live.error("FAKE unavailable")} onRun={vi.fn()} />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/could not be loaded/);
    expect(alert.textContent).toMatch(/FAKE unavailable/);
  });

  it("reports unreadable saved runs instead of dropping them", () => {
    render(<StartScreen saved={live.ready({ runs: [savedA], invalid: [new Error("FAKE samples/x: expected a string")] })} onRun={vi.fn()} />);
    expect(screen.getByRole("alert").textContent).toMatch(/1 saved run could not be read and is not listed.*FAKE samples\/x/s);
  });

  it("says so when nothing is available, and never invents a date", () => {
    render(<StartScreen saved={live.ready({ runs: [], invalid: [] })} onRun={vi.fn()} policies={NO_FILES} />);
    expect(screen.getByText(/No sample policies or saved runs are available/)).toBeTruthy();
    expect(screen.getByRole("contentinfo").textContent).toMatch(/RBI library dates not recorded yet/);
  });
});
