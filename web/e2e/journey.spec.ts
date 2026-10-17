// What a judge does first (UI_SPEC 13): reach a finding with its RBI quote in two clicks or fewer, see every
// screen render without console errors, at 1280px and phone width without sideways scrolling, and Tab through
// the page with a visible focus ring.
import { expect, test, type Page } from "@playwright/test";
import { SEEDED_SAMPLE_ID, expectNoHorizontalOverflow, watchConsole } from "./helpers";

const SAMPLE = `/#/sample/${SEEDED_SAMPLE_ID}`;

test("start, saved review, audit and evaluation screens render with no console errors", async ({ page }) => {
  const errors = watchConsole(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run demo on a sample NBFC policy" })).toBeVisible();
  await expect(page.locator("footer.start-footer")).toContainText("Synthetic data only · Not legal advice");
  // Every sample policy file gets a card with a Run live button, saved run or not (UI_SPEC 16.1: two samples).
  const cards = page.locator("li.sample-card");
  await expect(cards).toHaveCount(2);
  for (const card of await cards.all()) await expect(card.getByRole("button", { name: /^Run live on / })).toBeVisible();

  // UI_SPEC 13: "Synthetic data" and "Not legal advice" visible on every screen, whatever the run is called.
  const legal = page.locator(".topbar-legal");
  await page.goto(SAMPLE);
  await expect(page.locator("header.summary").getByRole("heading", { level: 1 })).toBeVisible();
  await expect(legal).toContainText("Synthetic data only · Not legal advice");
  await expect(page.locator(".pane-source .source-quote")).toBeVisible();

  const tabs = page.getByRole("navigation", { name: "Screens" });
  await tabs.getByRole("link", { name: "Audit log" }).click();
  await expect(page).toHaveURL(new RegExp(`#/sample/${SEEDED_SAMPLE_ID}/audit$`));
  await expect(page.getByRole("heading", { name: "Audit log", level: 1 })).toBeVisible();
  await expect(page.getByRole("row").nth(1)).toBeVisible();
  await expect(legal).toContainText("Synthetic data only · Not legal advice");

  await tabs.getByRole("link", { name: "Evaluation" }).click();
  await expect(page).toHaveURL(/#\/eval$/);
  await expect(page.getByRole("heading", { name: "Evaluation", level: 1 })).toBeVisible();
  // The seeded emulator has no eval_results/latest, as production does not: the page points to the official
  // evaluation in the repository instead of a table, and never says no evaluation was run.
  const official = page.getByRole("region", { name: "Official evaluation" });
  await expect(official).toContainText("No evaluation has been published to this page.");
  await expect(official.getByRole("link", { name: "docs/evaluation.md" })).toHaveAttribute(
    "href",
    "https://github.com/HemantBK/Argus/blob/main/docs/evaluation.md",
  );
  await expect(official.getByRole("link", { name: "evidence/2026-10-17/eval-official/" })).toHaveAttribute(
    "href",
    "https://github.com/HemantBK/Argus/tree/main/evidence/2026-10-17/eval-official",
  );
  await expect(official.getByRole("listitem")).toHaveCount(6);
  await expect(official).toContainText("R2 strong, hard (headline): 20261017T154026Z-446cd6");
  await expect(page.getByRole("table")).toHaveCount(0);
  await expect(page.locator("main#main")).not.toContainText(/not measured yet|No evaluation run has been recorded/);
  await expect(legal).toContainText("Synthetic data only · Not legal advice");

  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("a finding and its RBI quote are reached from the Start screen in two clicks or fewer", async ({ page }) => {
  const errors = watchConsole(page);
  await page.goto("/");
  let clicks = 0;
  // Click 1: the newest saved run of the first sample card, whatever the emulator holds.
  await page.getByRole("link", { name: /Open saved run \(recorded/ }).first().click();
  clicks += 1;
  await expect(page.locator(".clause-row").first()).toBeVisible();
  const finding = page.locator(".pane-finding .finding-card");
  const quote = page.locator(".pane-source .source-quote");
  await expect(finding).toBeVisible();
  if (!(await quote.isVisible())) {
    // Click 2: the first clause whose finding cites an RBI paragraph.
    await page.locator(".pane-finding .citation-chip").first().click();
    clicks += 1;
  }
  await expect(finding.locator(".rationale, .rationale-pair").first()).toBeVisible();
  await expect(quote).toBeVisible();
  await expect(quote).not.toBeEmpty();
  expect(clicks).toBeLessThanOrEqual(2);
  expect(errors).toEqual([]);
});

for (const width of [1280, 390]) {
  test(`no horizontal overflow at ${width}px`, async ({ page }) => {
    const errors = watchConsole(page);
    await page.setViewportSize({ width, height: width === 390 ? 844 : 800 });
    const screens: [string, string][] = [
      ["start", "/"],
      ["review", SAMPLE],
      ["audit", `${SAMPLE}/audit`],
      ["evaluation", "/#/eval"],
    ];
    for (const [name, path] of screens) {
      await page.goto(path);
      await expect(page.locator("main#main")).not.toBeEmpty();
      if (name === "review") await expect(page.locator(".pane-source .source-quote")).toBeVisible();
      if (name === "audit") await expect(page.getByRole("row").nth(1)).toBeVisible();
      await expectNoHorizontalOverflow(page, `${name} at ${width}px`);
    }
    expect(errors).toEqual([]);
  });
}

async function focusRing(page: Page): Promise<{ desc: string; ring: boolean } | null> {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!(el instanceof HTMLElement) || el === document.body) return null;
    const s = getComputedStyle(el);
    const outline = s.outlineStyle !== "none" && parseFloat(s.outlineWidth) >= 2;
    const shadow = s.boxShadow !== "none" && s.boxShadow !== "";
    const label = (el.getAttribute("aria-label") ?? el.textContent).trim().slice(0, 40);
    return { desc: `<${el.tagName.toLowerCase()}> "${label}"`, ring: outline || shadow };
  });
}

test("Tab moves focus through the review screen with a visible focus ring on every stop", async ({ page }) => {
  await page.goto(SAMPLE);
  await expect(page.locator(".pane-source .source-quote")).toBeVisible();
  const seen: string[] = [];
  for (let i = 0; i < 60; i += 1) {
    await page.keyboard.press("Tab");
    const f = await focusRing(page);
    // Focus leaves the page (to the browser) after the last stop: one full pass is done.
    if (f === null) break;
    expect(f.ring, `Tab stop ${i + 1} ${f.desc} has no visible focus ring`).toBe(true);
    seen.push(f.desc);
  }
  // The first stop is the skip link, then the top bar, then the review panes (UI_SPEC 7 tab order).
  expect(seen[0]).toContain("Skip to main content");
  const at = (text: string): number => seen.findIndex((d) => d.includes(text));
  expect(at("c1"), `stops: ${seen.join(" | ")}`).toBeGreaterThan(at("Evaluation"));
  expect(at("Open on")).toBeGreaterThan(at("c1"));
  expect(at("trace")).toBeGreaterThan(at("Open on"));
});
