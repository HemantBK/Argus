// The live-run screens in a real browser against the real firestore.rules, with the Argus API replaced by a
// stand-in (page.route). The stand-in does what the API contract says the API does (UI_SPEC 16.4, schema.py):
// POST /api/runs creates runs/{run_id} owned by the caller's uid FIRST, then streams the steps, findings and audit
// rows into it, marks it done and answers; the sign-off POST writes the finding's signoff and one audit row. It
// writes with the emulator's admin token, as the API's Admin SDK does, so the browser still reads only through the
// rules. The content is a copy of the seeded FAKE saved run.
//
// This checks the web side of the live path (run listener before POST, stage-2 listeners, streaming, sign-off,
// audit row, the "unavailable" banner) without a running backend. e2e/live-spike.spec.ts and hero moment 5 in
// e2e/hero.spec.ts check the same path against the real API when one is reachable.
import { expect, test, type Page, type Request, type Route } from "@playwright/test";
import { SCREENSHOTS, SEEDED_SAMPLE_ID, apiBase, watchConsole } from "./helpers";

const FIRESTORE = process.env.FIRESTORE_EMULATOR_HOST ?? "localhost:8081";
const DOCS = `http://${FIRESTORE}/v1/projects/argus-local/databases/(default)/documents`;
const ADMIN = { Authorization: "Bearer owner", "Content-Type": "application/json" };
const ORIGIN = "http://localhost:5173";
const CORS = {
  "Access-Control-Allow-Origin": ORIGIN,
  "Access-Control-Allow-Headers": "authorization, content-type",
  "Access-Control-Allow-Methods": "GET, POST",
};

type Fields = Record<string, unknown>;
interface RestDoc {
  name: string;
  fields: Fields;
}

async function admin(method: string, path: string, body?: unknown): Promise<unknown> {
  const res = await fetch(`${DOCS}/${path}`, { method, headers: ADMIN, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await res.text();
  if (!res.ok) throw new Error(`Emulator ${method} ${path} failed: ${res.status} ${text}`);
  return text ? JSON.parse(text) : undefined;
}

async function listDocs(path: string): Promise<RestDoc[]> {
  const out = (await admin("GET", `${path}?pageSize=1000`)) as { documents?: RestDoc[] };
  return out.documents ?? [];
}

const docId = (d: RestDoc): string => d.name.slice(d.name.lastIndexOf("/") + 1);
const str = (v: string): unknown => ({ stringValue: v });
const ts = (d: Date): unknown => ({ timestampValue: d.toISOString() });

/** The uid in the Firebase ID token the browser sent (emulator tokens are unsigned JWTs). */
function uidOf(req: Request): string {
  const token = /^Bearer (\S+)$/.exec(req.headers()["authorization"] ?? "")?.[1];
  const payload = token?.split(".")[1];
  if (!payload) throw new Error("The request carried no Bearer ID token");
  const claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as { user_id?: string; sub?: string };
  const uid = claims.user_id ?? claims.sub;
  if (!uid) throw new Error("The ID token has no uid");
  return uid;
}

/** Stand-in for POST /api/runs: create the run first, then stream the seeded saved run into it. */
async function fakeRun(runId: string, uid: string): Promise<void> {
  const sample = (await admin("GET", `samples/${SEEDED_SAMPLE_ID}`)) as RestDoc;
  const run = `runs/${runId}`;
  await admin("PATCH", run, {
    fields: { ...sample.fields, run_id: str(runId), owner_uid: str(uid), saved: { booleanValue: false }, status: str("running"), finished_at: { nullValue: null } },
  });
  for (const name of ["steps", "findings", "audit"]) {
    for (const d of await listDocs(`samples/${SEEDED_SAMPLE_ID}/${name}`)) {
      await admin("PATCH", `${run}/${name}/${docId(d)}`, { fields: d.fields });
    }
  }
  await admin("PATCH", `${run}?updateMask.fieldPaths=status&updateMask.fieldPaths=finished_at`, {
    fields: { status: str("done"), finished_at: ts(new Date()) },
  });
}

/** Stand-in for the sign-off POST: the finding's signoff plus one audit row, as runs.sign_off writes them. */
async function fakeSignoff(runId: string, clauseId: string, uid: string, body: { decision: string; note: string }): Promise<void> {
  const at = new Date();
  const run = `runs/${runId}`;
  await admin("PATCH", `${run}/findings/${clauseId}?updateMask.fieldPaths=signoff`, {
    fields: {
      signoff: {
        mapValue: {
          fields: { decision: str(body.decision), by: str(uid), at: ts(at), note: str(body.note), edited_rationale: { nullValue: null } },
        },
      },
    },
  });
  const seqs = (await listDocs(`${run}/audit`)).map((d) => Number(docId(d)));
  const seq = Math.max(0, ...seqs) + 1;
  await admin("PATCH", `${run}/audit/${String(seq).padStart(4, "0")}`, {
    fields: {
      seq: { integerValue: String(seq) },
      at: ts(at),
      actor: str(`officer:${uid}`),
      event: str(`signoff_${body.decision}`),
      detail: str(body.note),
      clause_id: str(clauseId),
      model: { nullValue: null },
    },
  });
}

async function json(route: Route, status: number, body: unknown): Promise<void> {
  await route.fulfill({ status, headers: { ...CORS, "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

/** Route the API to the stand-in. Returns the order in which sign-in, listeners and the run POST happened. */
async function standInApi(page: Page): Promise<{ order: string[]; created: string[] }> {
  const base = apiBase();
  const order: string[] = [];
  const created: string[] = [];
  page.on("request", (req) => {
    const url = req.url();
    if (url.includes("identitytoolkit") && url.includes("signUp")) order.push("sign-in");
    else if (url.includes("google.firestore.v1.Firestore/Listen")) order.push("listen");
  });
  await page.route(`${base}/api/**`, async (route) => {
    const req = route.request();
    if (req.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: CORS });
      return;
    }
    const path = new URL(req.url()).pathname;
    const body = req.postDataJSON() as Record<string, string>;
    if (path === "/api/runs") {
      order.push("post");
      const runId = body.run_id ?? "";
      created.push(runId);
      await fakeRun(runId, uidOf(req));
      await json(route, 200, { run_id: runId, status: "done" });
      return;
    }
    const signoff = /^\/api\/runs\/([0-9a-f]{32})\/findings\/([^/]+)\/signoff$/.exec(path);
    if (signoff?.[1] && signoff[2]) {
      await fakeSignoff(signoff[1], decodeURIComponent(signoff[2]), uidOf(req), { decision: body.decision ?? "", note: body.note ?? "" });
      await json(route, 200, { ok: true });
      return;
    }
    await json(route, 404, { detail: `FAKE stand-in API has no route for ${req.method()} ${path}` });
  });
  return { order, created };
}

async function cleanUp(runIds: string[]): Promise<void> {
  for (const id of runIds) {
    for (const name of ["steps", "findings", "audit"]) {
      for (const d of await listDocs(`runs/${id}/${name}`)) await admin("DELETE", `runs/${id}/${name}/${docId(d)}`);
    }
    await admin("DELETE", `runs/${id}`);
  }
}

test("Run demo watches the run before POST, streams it, and the officer's Accept reaches the audit log", async ({ page }) => {
  const errors = watchConsole(page);
  const api = await standInApi(page);
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "Run demo on a sample NBFC policy" }).click();
    await expect(page).toHaveURL(/#\/run\/[0-9a-f]{32}$/);
    const runId = /#\/run\/([0-9a-f]{32})$/.exec(page.url())?.[1];

    await expect.poll(() => api.created, { message: "POST /api/runs was sent", timeout: 15_000 }).toEqual([runId]);
    const firstPost = api.order.indexOf("post");
    expect(api.order.slice(0, firstPost), `requests before the POST: ${api.order.join(", ")}`).toContain("listen");

    // The run streamed in through the listeners and nothing was refused.
    await expect(page.locator("header.summary")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".trace-summary")).not.toContainText(/^0 steps/, { timeout: 30_000 });
    await expect(page.getByRole("alert")).toHaveCount(0);

    const accept = page.locator(".signoff").getByRole("button", { name: "Accept" });
    await expect(accept).toBeEnabled({ timeout: 30_000 });
    await accept.click();
    await expect(page.locator(".clause-row.is-selected .row-decision")).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: `${SCREENSHOTS}5a-officer-accepted-stand-in.png` });
    await page.getByRole("navigation", { name: "Screens" }).getByRole("link", { name: "Audit log" }).click();
    await expect(page.getByRole("row").filter({ hasText: "Accepted finding" })).toBeVisible();
    await page.screenshot({ path: `${SCREENSHOTS}5b-audit-row-stand-in.png` });
    expect(errors).toEqual([]);
  } finally {
    await cleanUp(api.created);
  }
});

test("a refused start (503 from the API) shows the 'Live review is temporarily unavailable' banner", async ({ page }) => {
  const errors = watchConsole(page);
  await page.route(`${apiBase()}/api/runs`, async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: CORS });
      return;
    }
    await json(route, 503, { detail: "FAKE: live review is switched off (kill switch)" });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Run demo on a sample NBFC policy" }).click();
  await expect(page.getByText("Live review is temporarily unavailable.")).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${SCREENSHOTS}live-unavailable-stand-in.png` });
  // The browser logs the failed request itself; the app adds nothing else.
  expect(errors.filter((e) => !e.includes("503"))).toEqual([]);
});
