// The five hero moments of UI_SPEC section 10, each on one 1920x1080 screen without scrolling, reached from the
// Start screen on the seeded FAKE FinFast saved run. Screenshots go to e2e/screenshots/ for the video and README.
import { expect, test, type Locator, type Page } from "@playwright/test";
import { SCREENSHOTS, apiBase, apiUnreachable, liveRunsBlocked, openSeededSampleFromStart, watchConsole } from "./helpers";

test.use({ viewport: { width: 1920, height: 1080 } });

function clauseRow(page: Page, clauseId: string): Locator {
  return page.locator(".clause-row").filter({ has: page.locator(".clause-id", { hasText: new RegExp(`^${clauseId}$`) }) });
}

/** Fully on screen: the hero moment needs no scrolling of the page or of its pane. */
async function onScreen(locator: Locator): Promise<void> {
  await expect(locator).toBeVisible();
  await expect(locator).toBeInViewport({ ratio: 1 });
}

test.describe("hero moments on the seeded saved run", () => {
  let errors: string[] = [];

  test.beforeEach(async ({ page }) => {
    errors = watchConsole(page);
    await openSeededSampleFromStart(page);
  });

  test.afterEach(() => {
    expect(errors).toEqual([]);
  });

  test("1. summary bar with the counts", async ({ page }) => {
    const summary = page.locator("header.summary");
    await onScreen(summary);
    await expect(summary.getByRole("heading", { level: 1 })).toContainText("FAKE");
    await expect(summary).toContainText(/Saved run \(recorded .+ IST\)/);
    const counts = summary.getByRole("list", { name: "Findings by type" });
    for (const label of ["Outdated reference", "Gap", "Conflict", "Compliant", "Needs review"]) {
      await expect(counts.getByRole("listitem").filter({ hasText: new RegExp(`^\\D*\\d+ ${label}$`) })).toBeVisible();
    }
    await expect(summary.locator(".summary-monitor")).toContainText(/revised/);
    await expect(summary.locator(".summary-monitor")).toContainText(/stopped/);
    await page.screenshot({ path: `${SCREENSHOTS}1-summary-bar.png` });
  });

  test("2. outdated-reference chain with the RBI quote in the source pane", async ({ page }) => {
    await clauseRow(page, "c1").click();
    const chain = page.getByRole("list", { name: "Outdated reference chain" });
    await onScreen(chain);
    await expect(chain).toContainText("Policy cites");
    await expect(chain).toContainText(/Withdrawn/i);
    await expect(chain).toContainText("Now in force");
    const quote = page.locator(".pane-source .source-quote");
    await onScreen(quote);
    await expect(quote).not.toBeEmpty();
    await onScreen(page.locator(".pane-source").getByRole("link", { name: /Open on .+ \(opens in a new tab\)/ }));
    await page.screenshot({ path: `${SCREENSHOTS}2-outdated-chain.png` });
  });

  test("3. monitor revising a fake paragraph number: trace line and Revised badge", async ({ page }) => {
    await clauseRow(page, "c2").click();
    await expect(clauseRow(page, "c2")).toContainText("Revised");
    await page.getByRole("button", { name: "Show trace" }).click();
    const reviseLine = page.locator(".trace-line.trace-revise.is-selected").first();
    await onScreen(reviseLine);
    await expect(reviseLine).toContainText(/revised/i);
    const badge = page.locator(".pane-finding .badge-block").getByRole("button", { name: /Revised by monitor/ });
    await badge.click();
    await expect(badge).toHaveAttribute("aria-expanded", "true");
    await onScreen(page.locator(".pane-finding .badge-detail .before-after"));
    await onScreen(badge);
    await page.screenshot({ path: `${SCREENSHOTS}3-monitor-revised.png` });
  });

  test("4. Stopped badge with the quoted hidden instruction", async ({ page }) => {
    await clauseRow(page, "c3").click();
    await expect(clauseRow(page, "c3")).toContainText("Stopped");
    const badge = page.locator(".pane-finding .badge-block").getByRole("button", { name: /Stopped/ });
    await onScreen(badge);
    await expect(badge, "the Stopped detail opens by default").toHaveAttribute("aria-expanded", "true");
    const quote = page.locator(".pane-finding .badge-detail figure.injection blockquote");
    await onScreen(quote);
    await expect(quote).toContainText(/\S/);
    await onScreen(page.locator(".pane-finding mark.mark-injection").first());
    await expect(page.locator(".pane-finding .badge-detail")).toContainText("What Argus did not do");
    await page.screenshot({ path: `${SCREENSHOTS}4-stopped-injection.png` });
  });
});

// UI_SPEC 8 records the video at 1280 px too, where the finding pane is narrow: the chain stacks, and the whole of
// it plus the RBI quote must still fit one screen, with no label broken mid-word.
test.describe("hero moment 2 at 1280 px", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test("the outdated-reference chain and the RBI quote fit one 1280x900 screen", async ({ page }) => {
    const errors = watchConsole(page);
    await openSeededSampleFromStart(page);
    await clauseRow(page, "c1").click();
    const chain = page.getByRole("list", { name: "Outdated reference chain" });
    await onScreen(chain);
    await onScreen(page.locator(".pane-source .source-quote"));
    for (const label of await chain.locator(".chain-step").all()) {
      const lineHeight = await label.evaluate((el) => parseFloat(getComputedStyle(el).lineHeight));
      const box = await label.boundingBox();
      expect(box?.height ?? Infinity).toBeLessThan(lineHeight * 1.5);
    }
    await page.screenshot({ path: `${SCREENSHOTS}2-outdated-chain-1280.png` });
    expect(errors).toEqual([]);
  });
});

test("5. officer accepts a finding and the audit log row appears (live run)", async ({ page }) => {
  const base = apiBase();
  const unreachable = await apiUnreachable(base);
  test.skip(
    unreachable !== null,
    `BLOCKED: the Argus API at VITE_API_BASE=${base} is unreachable (${unreachable ?? ""}). Saved runs are read-only ` +
      "(UI_SPEC 16.3), so sign-off needs a live run from a running backend (BACKEND_REQUESTS.md item 8; the web side is covered by e2e/live-stub.spec.ts).",
  );
  const blocked = await liveRunsBlocked();
  test.skip(blocked !== null, `BLOCKED: the local API would refuse a live run: ${blocked ?? ""}. The web side is covered by e2e/live-stub.spec.ts.`);
  // A real live review takes up to the API's 90 s run budget, then the sign-off and the audit log: the test's
  // own limit must outlast the waits below, or the default 30 s cuts it short whatever the run does.
  test.setTimeout(300_000);
  const errors = watchConsole(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Run demo on a sample NBFC policy" }).click();
  await expect(page).toHaveURL(/#\/run\/[0-9a-f]{32}$/);
  await expect(page.getByRole("alert")).toHaveCount(0);
  // Sign-off is enabled once the live run has finished (it is disabled with a stated reason while it runs).
  const accept = page.locator(".signoff").getByRole("button", { name: "Accept" });
  await expect(accept).toBeEnabled({ timeout: 180_000 });
  await accept.click();
  await expect(page.locator(".clause-row.is-selected .row-decision")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: `${SCREENSHOTS}5a-officer-accepted.png` });
  await page.getByRole("navigation", { name: "Screens" }).getByRole("link", { name: "Audit log" }).click();
  await expect(page.getByRole("row").filter({ hasText: "Accepted finding" })).toBeVisible();
  await page.screenshot({ path: `${SCREENSHOTS}5b-audit-row.png` });
  expect(errors).toEqual([]);
});
