// A live start that fails must say so within seconds, never leave the page on "Waiting for the run to start".
// The dev server runs against the local Firebase emulators; POST /api/runs is intercepted with page.route, so
// no API (local or deployed) is called. Each failure is one the live site can meet: the request never reaching
// the API (aborted, as when a 500 arrives without CORS headers and fetch rejects), a raw 500, the run cap (429)
// and the kill switch (503).
import { expect, test, type Route } from "@playwright/test";
import { apiBase } from "./helpers";

const FIRESTORE = process.env.FIRESTORE_EMULATOR_HOST ?? "localhost:8081";
const AUTH = process.env.FIREBASE_AUTH_EMULATOR_HOST ?? "localhost:9099";
const ORIGIN = "http://localhost:5173";
const CORS = {
  "Access-Control-Allow-Origin": ORIGIN,
  "Access-Control-Allow-Headers": "authorization, content-type",
  "Access-Control-Allow-Methods": "GET, POST",
};
/** How long a failed start may take to show its banner. */
const BANNER_WITHIN_MS = 5_000;

/** Null when both emulators answer, else why not. */
async function emulatorsUnreachable(): Promise<string | null> {
  for (const host of [FIRESTORE, AUTH]) {
    try {
      await fetch(`http://${host}/`, { signal: AbortSignal.timeout(3000) });
    } catch (e) {
      return `the Firebase emulator at ${host} is unreachable (${e instanceof Error ? e.message : String(e)}); start it with 'make up'`;
    }
  }
  return null;
}

/**
 * `offer`: the saved-run link the banner must hold. A start that went wrong offers the saved run of the same policy;
 * live review unavailable (the cap, the kill switch) offers the saved run of every sample policy, each named.
 */
type Failure = { name: string; title: RegExp; offer: RegExp; answer: (route: Route) => Promise<void>; detail?: string; hidden?: string };
const SAME_POLICY = /^Open the saved run recorded at /;
const EVERY_POLICY = /^Open the saved run of .+, recorded at /;

const FAILURES: Failure[] = [
  { name: "the request is aborted (fetch rejects)", title: /Argus could not reach its review service/, offer: SAME_POLICY, answer: (r) => r.abort("failed") },
  {
    name: "a 500 without CORS headers",
    // A real browser rejects this fetch (no CORS header: "could not reach"); a page.route answer may reach the
    // page as the HTTP 500 itself ("could not be started"). Either way the start failed and must say so.
    title: /Argus could not reach its review service|The live run could not be started/,
    offer: SAME_POLICY,
    answer: (r) => r.fulfill({ status: 500, contentType: "text/plain", body: "Internal Server Error" }),
  },
  {
    name: "a 429 run cap",
    title: /Live review is temporarily unavailable/,
    offer: EVERY_POLICY,
    // The API's own sentence (backend runs.py admit_counted), said again in plain words without its saved-run offer.
    answer: (r) =>
      r.fulfill({
        status: 429,
        headers: CORS,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Today's limit of 100 live runs is used up. Open a saved run, or try again tomorrow (India time)." }),
      }),
    detail: "Today's limit of 100 live runs is used up. Try again tomorrow (India time).",
  },
  {
    name: "a 503 kill switch",
    title: /Live review is temporarily unavailable/,
    offer: EVERY_POLICY,
    answer: (r) =>
      r.fulfill({ status: 503, headers: CORS, contentType: "application/json", body: JSON.stringify({ detail: "Argus is paused: new runs are switched off. Open a saved run instead." }) }),
    detail: "New live runs are switched off right now.",
    hidden: "Argus is paused",
  },
  {
    name: "a 502 HTML error page from a proxy",
    title: /Live review is temporarily unavailable/,
    offer: EVERY_POLICY,
    answer: (r) => r.fulfill({ status: 502, headers: CORS, contentType: "text/html", body: "<html><body><h1>502 Bad Gateway</h1>FAKE_PROXY_CODE</body></html>" }),
    detail: "The review service did not answer properly. Try again shortly.",
    hidden: "FAKE_PROXY_CODE",
  },
];

for (const failure of FAILURES) {
  test(`a failed start (${failure.name}) shows a banner within ${BANNER_WITHIN_MS / 1000} s with Try again and the saved run`, async ({ page }) => {
    const unreachable = await emulatorsUnreachable();
    test.skip(unreachable !== null, `Skipped: ${unreachable ?? ""}`);

    const posts: string[] = [];
    await page.route(`${apiBase()}/api/runs`, async (route) => {
      if (route.request().method() === "OPTIONS") {
        await route.fulfill({ status: 204, headers: CORS });
        return;
      }
      posts.push((route.request().postDataJSON() as { run_id: string }).run_id);
      await failure.answer(route);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Run demo on a sample NBFC policy" }).click();
    await expect.poll(() => posts.length, { message: "POST /api/runs was sent", timeout: 15_000 }).toBe(1);

    const banner = page.getByRole("alert");
    await expect(banner.locator("strong")).toHaveText(failure.title, { timeout: BANNER_WITHIN_MS });
    await expect(page.getByText("Waiting for the run to start")).toHaveCount(0);
    await expect(page.locator(".topbar-run")).toHaveText("Live run not started");
    if (failure.detail !== undefined) await expect(banner.locator(".banner-detail")).toHaveText(failure.detail);
    if (failure.hidden !== undefined) await expect(banner).not.toContainText(failure.hidden);
    // The newest saved run of the demo policy (the seeded FAKE FinFast run, or a newer one already in the emulator),
    // or, when live review is unavailable, the newest saved run of every sample policy that has one.
    await expect(banner.getByRole("link", { name: failure.offer }).first()).toHaveAttribute("href", /^#\/sample\/[0-9a-f]{32}$/);

    // Try again starts a fresh run (a new run id), which fails the same way and says so again.
    await banner.getByRole("button", { name: "Try again" }).click();
    await expect.poll(() => posts.length, { message: "Try again sent a second POST /api/runs", timeout: 15_000 }).toBe(2);
    expect(posts[1]).not.toBe(posts[0]);
    await expect(page).toHaveURL(new RegExp(`#/run/${posts[1] ?? ""}$`));
    await expect(page.getByRole("alert").locator("strong")).toHaveText(failure.title, { timeout: BANNER_WITHIN_MS });
  });
}
