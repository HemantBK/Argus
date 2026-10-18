// "Gemini is busy" and the evidence report in a real browser against the Firebase emulators. GET /health is answered
// with page.route, so no API (local or deployed) decides what the Start screen says, and nothing is started.
import { expect, test, type Page } from "@playwright/test";
import { apiBase, watchConsole } from "./helpers";

const ORIGIN = "http://localhost:5173";
// Written out, not imported from src/shell/busy.ts, so a change of wording there must be made here too (and in the
// claims ledger, rows W1 and W24).
const BREAKER_OPEN_TITLE = "Gemini is busy right now.";
const BREAKER_OPEN_SAVED = "Opening the saved run below does not need Gemini; it is a complete review recorded earlier on this app.";

async function answerHealth(page: Page, body: object): Promise<void> {
  await page.route(`${apiBase()}/health`, (route) =>
    route.fulfill({ status: 200, headers: { "Access-Control-Allow-Origin": ORIGIN }, contentType: "application/json", body: JSON.stringify(body) }),
  );
}

test("while /health shows the agent breaker open, the Start screen leads with the demo policy's saved run", async ({ page }) => {
  const errors = watchConsole(page);
  await answerHealth(page, { status: "ok", agent_breaker: "open" });
  await page.goto("/");
  await expect(page.getByRole("note")).toHaveText(`${BREAKER_OPEN_TITLE} ${BREAKER_OPEN_SAVED}`);
  await expect(page.getByRole("button", { name: "Start a live review anyway" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run demo on a sample NBFC policy" })).toHaveCount(0);
  // The saved run opens without Gemini: a saved run's Review screen, labelled as saved, never as live.
  await page.getByRole("link", { name: "Open the saved run" }).click();
  await expect(page).toHaveURL(/#\/sample\/[^/]+$/);
  await expect(page.locator(".topbar-run")).toContainText("saved run (recorded");
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(errors.filter((e) => !e.includes("Failed to load resource"))).toEqual([]);
});

for (const [name, answer] of [
  ["closed", { status: "ok", agent_breaker: "closed" }],
  ["half open", { status: "ok", agent_breaker: "half_open" }],
  ["missing", { status: "ok" }],
] as const) {
  test(`no busy note and the usual button when the breaker is ${name}`, async ({ page }) => {
    await answerHealth(page, answer);
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Run demo on a sample NBFC policy" })).toBeVisible();
    await expect(page.getByText(BREAKER_OPEN_TITLE)).toHaveCount(0);
    await expect(page.getByRole("note")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Open the saved run" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Start a live review anyway" })).toHaveCount(0);
  });
}

test("an unreachable /health fails silently: no note, no error on the page", async ({ page }) => {
  const errors = watchConsole(page);
  await page.route(`${apiBase()}/health`, (route) => route.abort("failed"));
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Run demo on a sample NBFC policy" })).toBeVisible();
  await expect(page.getByRole("note")).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  // The browser itself logs the aborted request; the app adds nothing.
  expect(errors.filter((e) => !e.includes("/health") && !e.includes("Failed to load resource"))).toEqual([]);
});

test("a saved run prints its evidence report, and only while printing", async ({ page }) => {
  await page.goto("/");
  const saved = page.getByRole("link", { name: /Open saved run \(recorded/ }).first();
  await expect(saved).toBeVisible();
  await saved.click();
  const button = page.getByRole("button", { name: "Print evidence report" });
  await expect(button).toBeVisible({ timeout: 15_000 });
  const report = page.locator(".evidence-report");
  await expect(report).toHaveCount(0);

  await page.evaluate(() => window.dispatchEvent(new Event("beforeprint")));
  await page.emulateMedia({ media: "print" });
  await expect(report).toBeVisible();
  await expect(report.getByRole("heading", { level: 1 })).toHaveText(/^Argus evidence report: not legal advice/);
  await expect(report.getByRole("heading", { level: 2, name: "Audit summary" })).toBeVisible();
  // The seeded saved run's findings predate per-finding model labels: each names the run's agent model and says so.
  await expect(report.getByText("(run's agent model; recorded before per-finding labels)").first()).toBeVisible();
  await expect(report.getByText(/^Monitor: \d+ findings? revised · \d+ stopped for human review$/)).toBeVisible();
  // On paper the report stands alone: the top bar and the panes are not printed.
  await expect(page.locator(".topbar")).toBeHidden();
  await expect(page.locator(".review-panes")).toBeHidden();

  await page.emulateMedia({ media: "screen" });
  await page.evaluate(() => window.dispatchEvent(new Event("afterprint")));
  await expect(report).toHaveCount(0);
  await expect(page.locator(".review-panes")).toBeVisible();
});
