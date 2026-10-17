// @vitest-environment jsdom
// App shell: routing, the live-run start order, and the run-level banners. Firebase, the listeners and the
// Review screen (tested in src/review/) are replaced by FAKE stand-ins.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ApiError, CUSTOM_CLAUSE_MIN_CHARS, CUSTOM_POLICY_ID, messageForStatus, type CustomClause } from "./data/api";
import { finding, run, T0 } from "./data/fixtures.test-util";
import type { PostState } from "./data/useStartRun";
import { BREAKER_OPEN_NOTE, PROVIDER_BUSY_PREFIX, savedRunOfferText } from "./shell/busy";
import { libraryLabel, recordedAt } from "./shell/format";
import { href } from "./shell/route";
import { startProblem } from "./shell/startProblem";
import { live } from "./shell/screens.test-util";
import { DEMO_POLICY_ID } from "./start/catalog";
import { ATTEST_LABEL } from "./start/CustomClauseCard";
import { forgetSentDraft } from "./start/customDraft";

const LIVE_ID = "0123456789ab4def8123456789abcdef";
const SAVED_ID = "fedcba9876544321a123456789abcdef";
const RETRY_ID = "abcdefabcdef4abc8abcabcdefabcdef";

function httpError(status: number, detail: string): PostState {
  return { status: "error", error: new ApiError("http", messageForStatus(status, detail), status, detail) };
}

/** Every way a live start can fail, as useStartRun reports it. */
const FAILED_STARTS: [string, PostState][] = [
  [
    "network error (fetch rejected, as for a 500 without CORS headers)",
    { status: "error", error: new ApiError("network", "Could not reach the Argus API at FAKE. Failed to fetch") },
  ],
  ["sign-in token unavailable", { status: "error", error: new ApiError("auth", "FAKE token refresh failed") }],
  ["HTTP 400", httpError(400, "FAKE unknown policy")],
  ["HTTP 401", httpError(401, "FAKE invalid token")],
  ["HTTP 500", httpError(500, "FAKE Internal Server Error")],
  // The API's own sentences for its daily cap and kill switch (backend runs.py), which the banner says in our words.
  ["HTTP 429 run cap", httpError(429, "Today's limit of 100 live runs is used up. Open a saved run, or try again tomorrow (India time).")],
  ["HTTP 503 kill switch", httpError(503, "Argus is paused: new runs are switched off. Open a saved run instead.")],
  ["listeners refused", { status: "blocked", error: new Error("FAKE permission-denied") }],
];

/** The banner heading the shell shows for a start problem. */
function bannerTitle(post: PostState): string {
  const problem = startProblem(post);
  if (!problem) throw new Error("not a start problem");
  return problem.kind === "unavailable" ? "Live review is temporarily unavailable." : problem.title;
}

/** What the FAKE hooks return; each test sets it in beforeEach. */
interface Fake {
  saved: unknown;
  runs: Map<string, unknown>;
  post: unknown;
  liveRunId: string | null;
  start: (policyId: string, custom?: CustomClause) => string;
  signIn: () => Promise<{ uid: string }>;
  /** env.ts customClauseOpen. */
  customOpen: boolean;
  /** What /health said about the agent model's breaker (data/health.ts). */
  breaker: string | null;
  /** The findings every useFindings call returns. */
  findings: unknown;
}

const fake = vi.hoisted(
  (): Fake => ({
    saved: undefined,
    runs: new Map(),
    post: undefined,
    liveRunId: null,
    start: (policyId) => policyId,
    signIn: () => Promise.resolve({ uid: "FAKE-uid" }),
    customOpen: true,
    breaker: null,
    findings: undefined,
  }),
);

vi.mock("./env", () => ({ appEnv: () => ({ customClauseOpen: fake.customOpen }) }));
vi.mock("./data/health", () => ({ useAgentBreaker: () => fake.breaker }));
vi.mock("./firebase", () => ({ ensureSignedIn: () => fake.signIn() }));
vi.mock("./data/hooks", () => ({
  useSavedRuns: () => fake.saved,
  useRun: (runId: string | null, source: string) =>
    runId === null ? { status: "idle", attached: false } : (fake.runs.get(`${source}/${runId}`) ?? { status: "loading", attached: false }),
  useAudit: () => ({ status: "ready", data: [], attached: true }),
  useFindings: (runId: string | null) => (runId === null ? { status: "idle", attached: false } : fake.findings),
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
const savedB = run({ run_id: "bbbbbbbbbbbb4bbb8bbbbbbbbbbbbbbb", policy_id: "policy_b", saved: true, policy_title: "FAKE LendRight NBFC (Synthetic)", started_at: T0 });

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
  const ids = [LIVE_ID, RETRY_ID]; // a second start (Try again) gets a new run id
  fake.start = vi.fn(() => {
    const id = ids.shift() ?? RETRY_ID;
    fake.liveRunId = id;
    fake.post = { status: "waiting_for_listeners" };
    return id;
  });
  fake.signIn = () => Promise.resolve({ uid: "FAKE-uid" });
  fake.customOpen = true;
  fake.breaker = null;
  fake.findings = live.ready([]);
});
afterEach(() => {
  cleanup();
  forgetSentDraft();
});

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

  it("shows 'live review unavailable' with the saved run of every sample policy when the API refuses the run", async () => {
    fake.saved = live.ready({ runs: [saved, savedB], invalid: [] });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Run demo on a sample NBFC policy" }));
    const paused = "Argus is paused: new runs are switched off. Open a saved run instead.";
    fake.post = { status: "error", error: new ApiError("http", messageForStatus(503, paused), 503, paused) } satisfies PostState;
    go(window.location.hash);
    const alert = await screen.findByRole("alert");
    expect(alert.querySelector("strong")?.textContent).toBe("Live review is temporarily unavailable.");
    expect(alert.textContent).toContain("New live runs are switched off right now.");
    expect(alert.textContent).not.toContain(paused);
    expect(within(alert).getAllByRole("link").map((a) => a.textContent)).toEqual([saved, savedB].map(savedRunOfferText));
  });

  it("says Gemini is busy on the Start screen only while /health shows the agent breaker open", async () => {
    for (const breaker of [null, "closed", "half_open"]) {
      fake.breaker = breaker;
      render(<App />);
      expect(screen.queryByText(BREAKER_OPEN_NOTE)).toBeNull();
      cleanup();
    }
    fake.breaker = "open";
    render(<App />);
    expect(screen.getByRole("note").textContent).toBe(BREAKER_OPEN_NOTE);
    await act(async () => {});
  });

  it("a live run whose clauses failed because Gemini was busy says so once and offers every saved run", async () => {
    fake.saved = live.ready({ runs: [saved, savedB], invalid: [] });
    const liveRun = run({ run_id: LIVE_ID, policy_id: DEMO_POLICY_ID, status: "done" });
    fake.runs.set(`runs/${LIVE_ID}`, live.ready(liveRun));
    fake.findings = live.ready([
      finding({ clause_id: "c1", error: `${PROVIDER_BUSY_PREFIX}; try again shortly. FAKE: HTTP 429 on every try.` }),
      finding({ clause_id: "c2", error: "FAKE: the reply was not usable." }),
      finding({ clause_id: "c3" }),
    ]);
    render(<App />);
    go(href.review({ source: "runs", runId: LIVE_ID }));
    const banner = await screen.findByRole("status");
    expect(banner.querySelector("strong")?.textContent).toBe("Gemini capacity was busy, so 1 clause of this run could not be completed.");
    expect(within(banner).getAllByRole("link").map((a) => a.textContent)).toEqual([saved, savedB].map(savedRunOfferText));
    // A saved run never shows it, whatever its findings say.
    go(href.review({ source: "samples", runId: SAVED_ID }));
    expect(screen.queryByText(/Gemini capacity was busy/)).toBeNull();
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

  it.each(FAILED_STARTS)(
    "a failed start (%s) shows a banner with Try again and the saved run instead of 'Waiting for the run to start'",
    async (_, failure) => {
      render(<App />);
      fireEvent.click(screen.getByRole("button", { name: "Run demo on a sample NBFC policy" }));
      fake.runs.set(`runs/${LIVE_ID}`, live.ready(null)); // the API never created the run
      fake.post = failure;
      go(window.location.hash);

      const alert = await screen.findByRole("alert");
      expect(alert.querySelector("strong")?.textContent).toContain(bannerTitle(failure));
      expect(alert.textContent).toContain(startProblem(failure)?.message ?? "");
      // Any other failure keeps the API's own reason; live review unavailable says it in our words (startProblem.ts).
      if (failure.status === "error" && failure.error.detail && startProblem(failure)?.kind !== "unavailable") {
        expect(alert.textContent).toContain(failure.error.detail);
      }
      // Live review unavailable (cap, kill switch, model failure) offers every sample policy's saved run; any other
      // failure offers the saved run of the same policy.
      const link =
        startProblem(failure)?.kind === "unavailable"
          ? screen.getByRole("link", { name: savedRunOfferText(saved) })
          : screen.getByRole("link", { name: `Open the saved run recorded at ${recordedAt(saved)}` });
      expect(link.getAttribute("href")).toBe(href.review({ source: "samples", runId: SAVED_ID }));
      // Nothing on the page still says the run is starting.
      expect(screen.queryByTestId("review")).toBeNull();
      expect(document.body.textContent).not.toMatch(/Waiting for the run to start|Live run starting|Starting the live review/);
      expect(document.querySelector(".topbar-run")?.textContent).toBe("Live run not started");

      // Try again starts a new live run of the same policy and opens it.
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(fake.start).toHaveBeenCalledTimes(2);
      expect(fake.start).toHaveBeenLastCalledWith(DEMO_POLICY_ID);
      expect(window.location.hash).toBe(href.review({ source: "runs", runId: RETRY_ID }));
      go(window.location.hash);
      expect((await screen.findByTestId("review")).textContent).toBe(`runs/${RETRY_ID}`);
      expect(screen.queryByRole("alert")).toBeNull();
    },
  );

  it("offers Try again without a saved-run link when the policy has no saved run", async () => {
    fake.saved = live.ready({ runs: [], invalid: [] });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Run demo on a sample NBFC policy" }));
    fake.post = httpError(500, "FAKE Internal Server Error");
    go(window.location.hash);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/No saved run of this policy is available yet/);
    expect(screen.queryByRole("link", { name: /Open the saved run/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("keeps the Review screen under the banner when the API created the run before the start failed", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Run demo on a sample NBFC policy" }));
    fake.runs.set(`runs/${LIVE_ID}`, live.ready(run({ run_id: LIVE_ID, policy_id: DEMO_POLICY_ID, status: "running" })));
    fake.post = httpError(500, "FAKE crashed mid-review");
    go(window.location.hash);
    expect((await screen.findByRole("alert")).textContent).toMatch(/FAKE crashed mid-review/);
    expect(screen.getByTestId("review").textContent).toBe(`runs/${LIVE_ID}`);
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
  describe("Try your own clause", () => {
    /** A FAKE clause just over the minimum length the API accepts. */
    const CLAUSE = `FAKE clause: the lender shall disclose the APR before sanction. ${"x".repeat(CUSTOM_CLAUSE_MIN_CHARS)}`;

    function pasteAndReview(text: string): void {
      fireEvent.change(screen.getByRole("textbox", { name: "One clause of a digital-lending policy" }), { target: { value: text } });
      fireEvent.click(screen.getByRole("checkbox", { name: ATTEST_LABEL }));
      fireEvent.click(screen.getByRole("button", { name: "Review" }));
    }

    it("Review passes the pasted clause from the Start screen through the shell into start()", async () => {
      render(<App />);
      pasteAndReview(CLAUSE);
      expect(fake.start).toHaveBeenCalledTimes(1);
      expect(fake.start).toHaveBeenCalledWith(CUSTOM_POLICY_ID, { clauseText: CLAUSE, attest: true });
      expect(window.location.hash).toBe(href.review({ source: "runs", runId: LIVE_ID }));
      go(window.location.hash);
      expect((await screen.findByTestId("review")).textContent).toBe(`runs/${LIVE_ID}`);
    });

    it("Try again after a failed custom start resends the same clause", async () => {
      render(<App />);
      pasteAndReview(CLAUSE);
      fake.runs.set(`runs/${LIVE_ID}`, live.ready(null)); // the API never created the run
      fake.post = httpError(500, "FAKE Internal Server Error");
      go(window.location.hash);
      const alert = await screen.findByRole("alert");
      expect(alert.querySelector("strong")?.textContent).toBe("The review of your clause could not be started.");
      expect(screen.getByRole("link", { name: "Edit your clause" }).getAttribute("href")).toBe(href.start());

      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(fake.start).toHaveBeenCalledTimes(2);
      expect(fake.start).toHaveBeenLastCalledWith(CUSTOM_POLICY_ID, { clauseText: CLAUSE, attest: true });
      expect(window.location.hash).toBe(href.review({ source: "runs", runId: RETRY_ID }));
    });
  });
});
