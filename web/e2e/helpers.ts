// Shared helpers for the e2e tests. They run against the dev server and the local Firebase emulators, seeded
// with `npm run seed-demo` (web/scripts/seed_demo.py).
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, type Page } from "@playwright/test";

export const SCREENSHOTS = fileURLToPath(new URL("./screenshots/", import.meta.url));

/** The run id seed_demo.py gives a demo run: uuid4 hex derived from sha256("argus-demo-<name>"). */
export function demoRunId(name: string): string {
  const raw = createHash("sha256").update(`argus-demo-${name}`).digest().subarray(0, 16);
  raw[6] = ((raw[6] ?? 0) & 0x0f) | 0x40;
  raw[8] = ((raw[8] ?? 0) & 0x3f) | 0x80;
  return raw.toString("hex");
}

/** The seeded FAKE FinFast saved run (samples/), which holds every hero moment of UI_SPEC section 10. */
export const SEEDED_SAMPLE_ID = demoRunId("sample-finfast");

/** Collects console errors and page errors; assert it is empty at the end of a test. */
export function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()} (${msg.location().url})`);
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

/** VITE_API_BASE as the dev server sees it: the environment first, then web/.env.local. */
export function apiBase(): string {
  const fromEnv = process.env.VITE_API_BASE;
  if (fromEnv && fromEnv.trim() !== "") return fromEnv.trim().replace(/\/+$/, "");
  const file = fileURLToPath(new URL("../.env.local", import.meta.url));
  const line = readFileSync(file, "utf8")
    .split("\n")
    .find((l) => l.startsWith("VITE_API_BASE="));
  if (!line) throw new Error(`VITE_API_BASE is not set in the environment or in ${file}`);
  return line.slice("VITE_API_BASE=".length).trim().replace(/\/+$/, "");
}

/** Null when the API answers at all (any HTTP status), else the reason it could not be reached. */
export async function apiUnreachable(base: string): Promise<string | null> {
  try {
    await fetch(`${base}/health`, { signal: AbortSignal.timeout(3000) });
    return null;
  } catch (e) {
    return e instanceof Error ? `${e.name}: ${e.message}` : String(e);
  }
}

/** Open the seeded saved run from the Start screen, unfolding "N older saved runs" when it is folded away. */
export async function openSeededSampleFromStart(page: Page): Promise<void> {
  await page.goto("/");
  const link = page.locator(`a[href="#/sample/${SEEDED_SAMPLE_ID}"]`);
  await expect(link, "the seeded saved run is listed on the Start screen (run `npm run seed-demo`)").toHaveCount(1);
  if (!(await link.isVisible())) {
    await page.locator("details.saved-run-older", { has: link }).locator("summary").click();
  }
  await link.click();
  await expect(page).toHaveURL(new RegExp(`#/sample/${SEEDED_SAMPLE_ID}$`));
  await expect(page.locator(".clause-row").first()).toBeVisible();
}

/** The page itself never scrolls sideways (inner scroll containers such as the audit table may). */
export async function expectNoHorizontalOverflow(page: Page, where: string): Promise<void> {
  const { scrollWidth, clientWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(scrollWidth, `${where}: page is ${scrollWidth}px wide in a ${clientWidth}px viewport`).toBeLessThanOrEqual(clientWidth);
}
