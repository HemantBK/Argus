// @vitest-environment jsdom
// App shell: routing, the live-run start order, and the run-level banners. Firebase, the listeners and the
// Review screen (tested in src/review/) are replaced by FAKE stand-ins.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ApiError, messageForStatus } from "./data/api";
import { run, T0 } from "./data/fixtures.test-util";
import type { PostState } from "./data/useStartRun";
import { libraryLabel, recordedAt } from "./shell/format";
import { href } from "./shell/route";
import { live } from "./shell/screens.test-util";
import { DEMO_POLICY_ID } from "./start/catalog";

const LIVE_ID = "0123456789ab4def8123456789abcdef";
const SAVED_ID = "fedcba9876544321a123456789abcdef";

/** What the FAKE hooks return; each test sets it in beforeEach. */
interface Fake {
  saved: unknown;
  runs: Map<string, unknown>;
  post: unknown;
  liveRunId: string | null;
  start: (policyId: string) => string;
  signIn: () => Promise<{ uid: string }>;
}

const fake = vi.hoisted(
  (): Fake => ({
    saved: undefined,
    runs: new Map(),
    post: undefined,
    liveRunId: null,
    start: (policyId) => policyId,
    signIn: () => Promise.resolve({ uid: "FAKE-uid" }),
  }),
);

vi.mock("./env", () => ({ appEnv: () => ({}) }));
vi.mock("./firebase", () => ({ ensureSignedIn: () => fake.signIn() }));
vi.mock("./data/hooks", () => ({
  useSavedRuns: () => fake.saved,
  useRun: (runId: string | null, source: string) =>
    runId === null ? { status: "idle", attached: false } : (fake.runs.get(`${source}/${runId}`) ?? { status: "loading", attached: false }),
  useAudit: () => ({ status: "ready", data: [], attached: true }),
  useFindings: () => ({ status: "ready", data: [], attached: true }),
  useEvalLatest: () => ({ status: "ready", data: null, attached: true }),
}));
vi.mock("./data/useStartRun", () => ({
  useStartRun: () => ({ runId: fake.liveRunId, post: fake.post, start: fake.start }),
}));
vi.mock("./review/ReviewScreen", () => ({
  ReviewScreen: ({ source, runId }: { source: string; runId: string }): ReactNode => <div data-testid="review">{`${source}/${runId}`}</div>,
}));

const { App } = await import("./App");

const saved = run({ run_id: SAVED_ID, policy_id: DEMO_POLICY_ID, saved: true, policy_title: "FAKE FinFast NBFC (Synthetic)", started_at: T0 });

function go(hash: string): void {
  act(() => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
}

beforeEach(() => {
  window.location.hash = "";
  fake.saved = live.ready({ runs: [saved], invalid: [] });
  fake.runs = new Map([[`samples/${SAVED_ID}`, live.ready(saved)]]);
  fake.post = { status: "idle" };
  fake.liveRunId = null;
  fake.start = vi.fn(() => {
    fake.liveRunId = LIVE_ID;
    fake.post = { status: "waiting_for_listeners" };
    return LIVE_ID;
  });
  fake.signIn = () => Promise.resolve({ uid: "FAKE-uid" });
});
afterEach(cleanup);

describe("App", () => {
  it("opens on the Start screen with the library dates from data", async () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/Check your digital-lending policy/);
    expect(document.querySelector(".topbar-legal")?.textContent).toBe(`Synthetic data only · Not legal advice · ${libraryLabel({ kind: "in_force", updatedAsOn: saved.rbi_as_on ?? null, fetchedOn: saved.corpus_as_on })}`);
    await act(async () => {}); // let the FAKE sign-in settle
  });

  it("Run demo starts the demo policy and opens its Review screen at once", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Run demo on a sample NBFC policy" }));
    expect(fake.start).toHaveBeenCalledWith(DEMO_POLICY_ID);
    expect(window.location.hash).toBe(href.review({ source: "runs", runId: LIVE_ID }));
    go(window.location.hash);
    expect((await screen.findByTestId("review")).textContent).toBe(`runs/${LIVE_ID}`);
    expect(screen.getByRole("status").textContent).toMatch(/Starting the live review/);
  });

  it("shows 'live review unavailable' with the saved run when the API refuses the run", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Run demo on a sample NBFC policy" }));
    fake.post = { status: "error", error: new ApiError("http", messageForStatus(503, "FAKE kill switch"), 503) } satisfies PostState;
    go(window.location.hash);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(`Live review is temporarily unavailable. Open the saved run recorded at ${recordedAt(saved)}?`);
    expect(alert.textContent).toMatch(/FAKE kill switch/);
  });

  it("reports a run that was never started once, in the shell banner, not again in the Review screen", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Run demo on a sample NBFC policy" }));
    fake.post = { status: "blocked", error: new Error("FAKE permission-denied") } satisfies PostState;
    go(window.location.hash);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/The live run was not started/);
    expect(alert.textContent).toMatch(/FAKE permission-denied/);
    expect(screen.queryByTestId("review")).toBeNull();
    // The same run's Audit log tab does not repeat it either.
    go(href.audit({ source: "runs", runId: LIVE_ID }));
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "Audit log" })).toBeNull();
  });

  it("shows the run-failed banner above the Audit log, offering the saved run", async () => {
    const failed = run({ run_id: LIVE_ID, policy_id: DEMO_POLICY_ID, status: "failed", error: "FAKE run budget exceeded" });
    fake.runs.set(`runs/${LIVE_ID}`, live.ready(failed));
    window.location.hash = href.audit({ source: "runs", runId: LIVE_ID });
    render(<App />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Audit log");
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/Reason recorded: FAKE run budget exceeded/);
    expect(screen.getByRole("link", { name: `Open the saved run recorded at ${recordedAt(saved)}` }).getAttribute("href")).toBe(
      href.review({ source: "samples", runId: SAVED_ID }),
    );
    await act(async () => {});
  });

  it("keeps the run's tabs on the Evaluation page", async () => {
    window.location.hash = href.review({ source: "samples", runId: SAVED_ID });
    render(<App />);
    expect(screen.getByTestId("review").textContent).toBe(`samples/${SAVED_ID}`);
    go(href.eval());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Evaluation");
    expect(screen.getByRole("link", { name: "Review" }).getAttribute("href")).toBe(href.review({ source: "samples", runId: SAVED_ID }));
    expect(screen.getByRole("link", { name: "Evaluation" }).getAttribute("aria-current")).toBe("page");
    await act(async () => {});
  });

  it("says when a page does not exist", async () => {
    window.location.hash = "#/nowhere";
    render(<App />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Page not found");
    await act(async () => {});
  });

  it("shows a sign-in failure", async () => {
    fake.signIn = () => Promise.reject(new Error("FAKE auth emulator down"));
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/Could not sign in.*FAKE auth emulator down/s);
  });
});
