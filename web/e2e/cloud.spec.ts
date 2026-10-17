// Cloud verification (VERIFICATION_PLAN.md "Cloud verification", step 4): Playwright against the LIVE site
// https://argus-aibc.web.app and its Cloud Run API. Runs only with `npx playwright test --project=cloud`
// (playwright.config.ts adds the project, and skips the emulator seed and the dev server, only then).
//
//   C1  the Start screen loads with no console errors and shows the tagline and two sample cards
//   C2  Run demo -> first finding within 60 s -> run finishes -> Accept -> the Audit log has the officer row
//       LIVE: one Vertex run. Skipped unless ARGUS_CLOUD_LIVE=1, so running the project never spends by accident.
//   C3  page.route aborts POST /api/runs -> the failed-start banner with Try again within 5 s (no run consumed)
//   C5  at 390 px wide the Start screen has no horizontal overflow
//
// Each test writes ../verification/<date>/cloud/<ID>.json as {gate, command, git_sha, revision, started_utc,
// exit_code, pass, details}; pass is the Playwright test status ("passed"), never a judgement. Screenshots and
// the raw console/network log of each test go to the same folder.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { TAGLINE, HEADLINE } from "../src/start/copy";

const WEB = fileURLToPath(new URL("..", import.meta.url));
const REPO = fileURLToPath(new URL("../..", import.meta.url));
const DATE = process.env.ARGUS_CLOUD_DATE ?? "2026-10-17";
const OUT = process.env.ARGUS_CLOUD_OUT ?? fileURLToPath(new URL(`../../../verification/${DATE}/cloud/`, import.meta.url));
const SHOTS = `${OUT}screenshots/`;
const BANNER_WITHIN_MS = 5_000;
const FIRST_FINDING_WITHIN_MS = 60_000;
const RUN_DONE_WITHIN_MS = 300_000;

/** VITE_API_BASE from the production build env (web/.env.production.local), which the live bundle was built with. */
function prodApiBase(): string {
  const fromEnv = process.env.ARGUS_CLOUD_API_BASE;
  if (fromEnv && fromEnv.trim() !== "") return fromEnv.trim().replace(/\/+$/, "");
  const line = readFileSync(`${WEB}.env.production.local`, "utf8")
    .split("\n")
    .find((l) => l.startsWith("VITE_API_BASE="));
  if (!line) throw new Error("VITE_API_BASE is not set in web/.env.production.local");
  return line.slice("VITE_API_BASE=".length).trim().replace(/\/+$/, "");
}
const API = prodApiBase();

function tryRun(cmd: string, args: string[], cwd: string): string {
  try {
    return execFileSync(cmd, args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 60_000 }).trim();
  } catch {
    return "";
  }
}

/** git HEAD, the serving Cloud Run revision and /health, read once per worker (read-only calls, no Vertex). */
let stack: { git_sha: string; revision: string; health: unknown } | null = null;
async function stackInfo(): Promise<{ git_sha: string; revision: string; health: unknown }> {
  if (stack) return stack;
  const gcloud = process.env.GCLOUD ?? `${process.env.HOME ?? ""}/google-cloud-sdk/bin/gcloud`;
  const revision = tryRun(
    gcloud,
    ["run", "services", "describe", "argus-api", "--region", "asia-south1", "--project", "argus-aibc", "--format=value(status.latestReadyRevisionName)"],
    REPO,
  );
  let health: unknown;
  try {
    const res = await fetch(`${API}/health`, { signal: AbortSignal.timeout(30_000) });
    health = await res.json();
  } catch (e) {
    health = { error: e instanceof Error ? e.message : String(e) };
  }
  stack = { git_sha: tryRun("git", ["rev-parse", "HEAD"], REPO), revision, health };
  return stack;
}

/** Console errors, page errors and failed requests; the raw log is saved next to the evidence. */
function watch(page: Page): { errors: string[]; log: string[] } {
  const errors: string[] = [];
  const log: string[] = [];
  page.on("console", (msg) => {
    log.push(`[console.${msg.type()}] ${msg.text()} (${msg.location().url})`);
    if (msg.type() === "error") errors.push(`console: ${msg.text()} (${msg.location().url})`);
  });
  page.on("pageerror", (e) => {
    log.push(`[pageerror] ${e.message}`);
    errors.push(`pageerror: ${e.message}`);
  });
  page.on("requestfailed", (r) => log.push(`[requestfailed] ${r.method()} ${r.url()} ${r.failure()?.errorText ?? ""}`));
  page.on("response", (r) => {
    if (r.status() >= 400) log.push(`[response ${r.status()}] ${r.request().method()} ${r.url()}`);
  });
  return { errors, log };
}

const startedUtc = new Map<string, string>();
const details = new Map<string, Record<string, unknown>>();
const logs = new Map<string, string[]>();

function gateOf(info: TestInfo): string {
  return /^(C\d+)\b/.exec(info.title)?.[1] ?? info.title;
}

test.beforeAll(() => {
  mkdirSync(SHOTS, { recursive: true });
});

// Playwright requires a destructuring pattern for the fixtures argument, even when none is used.
// eslint-disable-next-line no-empty-pattern
test.beforeEach(({}, info) => {
  startedUtc.set(gateOf(info), new Date().toISOString());
});

// eslint-disable-next-line no-empty-pattern
test.afterEach(async ({}, info) => {
  const gate = gateOf(info);
  if (info.status === "skipped") return; // a skipped gate (C2 without ARGUS_CLOUD_LIVE=1) writes no evidence
  const s = await stackInfo();
  const pass = info.status === "passed";
  const evidence = {
    gate,
    command: `cd ${WEB} && npx playwright test --project=cloud -g "${gate}\\b"`,
    git_sha: s.git_sha,
    revision: s.revision,
    started_utc: startedUtc.get(gate) ?? null,
    exit_code: pass ? 0 : 1,
    pass,
    details: {
      title: info.title,
      status: info.status,
      expected_status: info.expectedStatus,
      duration_ms: info.duration,
      base_url: info.project.use.baseURL,
      api_base: API,
      health: s.health,
      health_git_sha_matches_head: (s.health as { git_sha?: string } | null)?.git_sha === s.git_sha,
      errors: info.errors.map((e) => (e.message ?? "").slice(0, 2000)),
      ...(details.get(gate) ?? {}),
      raw_log: `${gate}.log`,
    },
  };
  writeFileSync(`${OUT}${gate}.json`, `${JSON.stringify(evidence, null, 2)}\n`);
  writeFileSync(`${OUT}${gate}.log`, `${(logs.get(gate) ?? []).join("\n")}\n`);
});

test("C1 start screen loads with no console errors, the tagline and two sample cards", async ({ page }) => {
  const { errors, log } = watch(page);
  logs.set("C1", log);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(HEADLINE);
  await expect(page.locator(".start-sub")).toHaveText(TAGLINE);
  const cards = page.locator("li.sample-card:not(.skeleton-card)");
  await expect(cards).toHaveCount(2, { timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Run demo on a sample NBFC policy" })).toBeEnabled({ timeout: 20_000 });
  await expect(page.getByRole("alert").filter({ hasText: "Could not sign in" })).toHaveCount(0);
  // Firestore listeners keep a channel open, so the network never idles: wait for the saved runs to load and
  // give late listener errors a moment to surface.
  await expect(page.getByText("Loading saved runs…")).toHaveCount(0, { timeout: 20_000 });
  await page.waitForTimeout(2_000);
  const cardTitles = await cards.locator("h3, h2").allInnerTexts();
  details.set("C1", { sample_cards: await cards.count(), card_titles: cardTitles, console_errors: errors });
  await page.screenshot({ path: `${SHOTS}C1-start-1280.png`, fullPage: true });
  expect(errors).toEqual([]);
});

test("C2 Run demo streams a live run, Accept a finding, the Audit log shows the officer row (LIVE run)", async ({ page }) => {
  test.skip(process.env.ARGUS_CLOUD_LIVE !== "1", "LIVE Vertex run: set ARGUS_CLOUD_LIVE=1 to spend one run on it");
  test.setTimeout(RUN_DONE_WITHIN_MS + 120_000);
  const { errors, log } = watch(page);
  logs.set("C2", log);
  const d: Record<string, unknown> = {};
  details.set("C2", d);

  await page.goto("/");
  const run = page.getByRole("button", { name: "Run demo on a sample NBFC policy" });
  await expect(run).toBeEnabled({ timeout: 20_000 });
  const clicked = Date.now();
  await run.click();
  await expect(page).toHaveURL(/#\/run\/[0-9a-f]{32}$/);
  d.run_id = /#\/run\/([0-9a-f]{32})$/.exec(page.url())?.[1] ?? null;
  await expect(page.getByRole("alert")).toHaveCount(0);

  // A finding is a clause row carrying a status chip.
  await expect(page.locator(".clause-row .chip").first()).toBeVisible({ timeout: FIRST_FINDING_WITHIN_MS });
  d.first_finding_ms = Date.now() - clicked;
  d.first_finding_limit_ms = FIRST_FINDING_WITHIN_MS;
  await page.screenshot({ path: `${SHOTS}C2a-first-finding.png` });

  // Sign-off is enabled once the live run has finished.
  const accept = page.locator(".signoff").getByRole("button", { name: "Accept" });
  await expect(accept).toBeEnabled({ timeout: RUN_DONE_WITHIN_MS });
  d.run_done_ms = Date.now() - clicked;
  d.findings = await page.locator(".clause-row .chip").count();
  d.clauses = await page.locator(".clause-row").count();
  d.failed_clauses_text = await page.locator(".summary-counts").innerText();
  await page.screenshot({ path: `${SHOTS}C2b-run-finished.png` });
  await accept.click();
  await expect(page.locator(".clause-row.is-selected .row-decision")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: `${SHOTS}C2c-officer-accepted.png` });

  await page.getByRole("navigation", { name: "Screens" }).getByRole("link", { name: "Audit log" }).click();
  const row = page.getByRole("row").filter({ hasText: "Accepted finding" });
  await expect(row).toBeVisible({ timeout: 30_000 });
  d.audit_row = await row.first().innerText();
  await page.screenshot({ path: `${SHOTS}C2d-audit-row.png`, fullPage: true });
  d.console_errors = errors;
  expect(errors).toEqual([]);
});

test("C3 an aborted POST /api/runs shows the failed-start banner with Try again within 5 s", async ({ page }) => {
  const { log } = watch(page);
  logs.set("C3", log);
  const posts: string[] = [];
  const leaked: string[] = [];
  // Abort every POST to the run endpoint, so no live run is consumed; anything else passes through.
  await page.route(`${API}/api/runs`, async (route) => {
    if (route.request().method() === "POST") {
      posts.push((route.request().postDataJSON() as { run_id?: string } | null)?.run_id ?? "");
      await route.abort("failed");
      return;
    }
    await route.continue();
  });
  page.on("requestfinished", (r) => {
    if (r.method() === "POST" && r.url().startsWith(`${API}/api/runs`)) leaked.push(r.url());
  });

  await page.goto("/");
  const run = page.getByRole("button", { name: "Run demo on a sample NBFC policy" });
  await expect(run).toBeEnabled({ timeout: 20_000 });
  await run.click();
  await expect.poll(() => posts.length, { message: "POST /api/runs was sent", timeout: 15_000 }).toBe(1);
  const aborted = Date.now();

  const banner = page.getByRole("alert");
  await expect(banner.locator("strong")).toHaveText(/Argus could not reach its review service/, { timeout: BANNER_WITHIN_MS });
  const bannerMs = Date.now() - aborted;
  await expect(banner.getByRole("button", { name: "Try again" })).toBeVisible();
  await expect(page.getByText("Waiting for the run to start")).toHaveCount(0);
  await page.screenshot({ path: `${SHOTS}C3-failed-start-banner.png` });

  // Try again sends a fresh run id, which is aborted too and says so again.
  await banner.getByRole("button", { name: "Try again" }).click();
  await expect.poll(() => posts.length, { message: "Try again sent a second POST /api/runs", timeout: 15_000 }).toBe(2);
  await expect(page.getByRole("alert").locator("strong")).toHaveText(/Argus could not reach its review service/, { timeout: BANNER_WITHIN_MS });

  details.set("C3", {
    banner_ms: bannerMs,
    banner_limit_ms: BANNER_WITHIN_MS,
    banner_text: await page.getByRole("alert").innerText(),
    posts_aborted: posts.length,
    distinct_run_ids: new Set(posts).size,
    posts_reaching_api: leaked.length,
  });
  expect(posts[1]).not.toBe(posts[0]);
  expect(leaked).toEqual([]);
  expect(bannerMs).toBeLessThanOrEqual(BANNER_WITHIN_MS);
});

test("C5 the start screen has no horizontal overflow at 390 px", async ({ page }) => {
  const { errors, log } = watch(page);
  logs.set("C5", log);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator("li.sample-card:not(.skeleton-card)").first()).toBeVisible({ timeout: 20_000 });
  // Firestore listeners keep a channel open, so the network never idles: wait for the saved runs to load and
  // give late listener errors a moment to surface.
  await expect(page.getByText("Loading saved runs…")).toHaveCount(0, { timeout: 20_000 });
  await page.waitForTimeout(2_000);
  const m = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const wide = Array.from(document.querySelectorAll<HTMLElement>("body *"))
      .filter((el) => el.getBoundingClientRect().right > vw + 0.5 && getComputedStyle(el).position !== "fixed")
      .slice(0, 10)
      .map((el) => `${el.tagName.toLowerCase()}.${el.className} right=${Math.round(el.getBoundingClientRect().right)}`);
    return { scrollWidth: document.documentElement.scrollWidth, clientWidth: vw, bodyScrollWidth: document.body.scrollWidth, wide };
  });
  details.set("C5", { viewport: 390, ...m, console_errors: errors });
  await page.screenshot({ path: `${SHOTS}C5-start-390.png`, fullPage: true });
  expect(m.scrollWidth, `page is ${m.scrollWidth}px wide in a ${m.clientWidth}px viewport`).toBeLessThanOrEqual(m.clientWidth);
  expect(m.bodyScrollWidth).toBeLessThanOrEqual(m.clientWidth);
});
