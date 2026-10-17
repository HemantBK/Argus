// "Try your own clause" (D-60) in a real browser against the Firebase emulators, with POST /api/runs answered
// by page.route (no API, local or deployed, is called). Checks the request the card sends, a refused clause
// (identifier found: no Try again, the clause comes back for editing), custom review switched off (503, Try
// again resends the same clause), and a run the stand-in creates, streamed onto the normal Review screen.
import { expect, test, type Page, type Request, type Route } from "@playwright/test";
import { SEEDED_SAMPLE_ID, apiBase, watchConsole } from "./helpers";

const FIRESTORE = process.env.FIRESTORE_EMULATOR_HOST ?? "localhost:8081";
const AUTH = process.env.FIREBASE_AUTH_EMULATOR_HOST ?? "localhost:9099";
const DOCS = `http://${FIRESTORE}/v1/projects/argus-local/databases/(default)/documents`;
const ADMIN = { Authorization: "Bearer owner", "Content-Type": "application/json" };
const ORIGIN = "http://localhost:5173";
const CORS = {
  "Access-Control-Allow-Origin": ORIGIN,
  "Access-Control-Allow-Headers": "authorization, content-type",
  "Access-Control-Allow-Methods": "GET, POST",
};
const ATTEST = "This text is synthetic or anonymised and contains no personal data";
const FAKE_CLAUSE = "FAKE clause: a borrower may cancel the FAKE digital loan within three days of disbursal without penalty.";
const HOUR_MS = 3_600_000;

interface CustomBody {
  run_id: string;
  policy_id: string;
  clause_text?: string;
  attest?: boolean;
}

type Fields = Record<string, unknown>;
interface RestDoc {
  name: string;
  fields: Fields;
}

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

function uidOf(req: Request): string {
  const token = /^Bearer (\S+)$/.exec(req.headers()["authorization"] ?? "")?.[1];
  const payload = token?.split(".")[1];
  if (!payload) throw new Error("The request carried no Bearer ID token");
  const claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as { user_id?: string; sub?: string };
  const uid = claims.user_id ?? claims.sub;
  if (!uid) throw new Error("The ID token has no uid");
  return uid;
}

/** Whether a stored document belongs to clause c1 (or to no clause): the one clause of a custom run. */
function ofFirstClause(d: RestDoc): boolean {
  const clause = d.fields.clause_id as { stringValue?: string } | undefined;
  return clause?.stringValue === undefined || clause.stringValue === "c1";
}

/**
 * Stand-in for POST /api/runs with a pasted clause, as the contract describes the API: one run "Your clause"
 * with the single clause c1, source custom, expires_at 48 h on the run and every document. Its review content is
 * the seeded FAKE saved run's clause c1.
 */
async function fakeCustomRun(body: CustomBody, uid: string): Promise<void> {
  const sample = (await admin("GET", `samples/${SEEDED_SAMPLE_ID}`)) as RestDoc;
  const run = `runs/${body.run_id}`;
  const expires = ts(new Date(Date.now() + 48 * HOUR_MS));
  const text = body.clause_text ?? "";
  const clause = { mapValue: { fields: { clause_id: str("c1"), title: str(text.slice(0, 60)), text: str(text) } } };
  await admin("PATCH", run, {
    fields: {
      ...sample.fields,
      run_id: str(body.run_id),
      owner_uid: str(uid),
      saved: { booleanValue: false },
      status: str("running"),
      finished_at: { nullValue: null },
      policy_id: str("custom"),
      policy_title: str("Your clause"),
      clauses: { arrayValue: { values: [clause] } },
      source: str("custom"),
      expires_at: expires,
    },
  });
  for (const name of ["steps", "findings", "audit"]) {
    for (const d of (await listDocs(`samples/${SEEDED_SAMPLE_ID}/${name}`)).filter(ofFirstClause)) {
      await admin("PATCH", `${run}/${name}/${docId(d)}`, { fields: { ...d.fields, expires_at: expires } });
    }
  }
  await admin("PATCH", `${run}?updateMask.fieldPaths=status&updateMask.fieldPaths=finished_at`, {
    fields: { status: str("done"), finished_at: ts(new Date()) },
  });
}

async function cleanUp(runIds: string[]): Promise<void> {
  for (const id of runIds) {
    for (const name of ["steps", "findings", "audit"]) {
      for (const d of await listDocs(`runs/${id}/${name}`)) await admin("DELETE", `runs/${id}/${name}/${docId(d)}`);
    }
    await admin("DELETE", `runs/${id}`);
  }
}

async function json(route: Route, status: number, body: unknown): Promise<void> {
  await route.fulfill({ status, headers: { ...CORS, "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

/** Answer POST /api/runs with `answer`; returns every body sent. */
async function stubRuns(page: Page, answer: (route: Route, body: CustomBody) => Promise<void>): Promise<CustomBody[]> {
  const posts: CustomBody[] = [];
  await page.route(`${apiBase()}/api/runs`, async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: CORS });
      return;
    }
    const body = route.request().postDataJSON() as CustomBody;
    posts.push(body);
    await answer(route, body);
  });
  return posts;
}

/** Paste the clause into the card, tick the attestation and press Review. */
async function reviewClause(page: Page, text: string): Promise<void> {
  await page.goto("/");
  const card = page.getByRole("region", { name: "Try your own clause" });
  const review = card.getByRole("button", { name: "Review" });
  await expect(review).toBeDisabled();
  await card.getByRole("textbox").fill(text);
  await card.getByRole("checkbox", { name: ATTEST }).check();
  await expect(review).toBeEnabled();
  await review.click();
}

test.beforeEach(async () => {
  const unreachable = await emulatorsUnreachable();
  test.skip(unreachable !== null, `Skipped: ${unreachable ?? ""}`);
});

test("the card starts from a sample clause and will not send a clause that is too short", async ({ page }) => {
  await page.goto("/");
  const card = page.getByRole("region", { name: "Try your own clause" });
  await expect(card.getByRole("textbox")).not.toHaveValue("");
  await card.getByRole("checkbox", { name: ATTEST }).check();
  await expect(card.getByRole("button", { name: "Review" })).toBeEnabled();
  await card.getByRole("textbox").fill("FAKE too short");
  await expect(card.getByText(/^Too short:/)).toBeVisible();
  await expect(card.getByRole("button", { name: "Review" })).toBeDisabled();
});

test("a clause refused for an identifier says so in plain English, offers no Try again, and comes back for editing", async ({ page }) => {
  const errors = watchConsole(page);
  const posts = await stubRuns(page, (route) =>
    json(route, 400, { detail: { code: "identifier_found", message: "FAKE server message", identifier_type: "PAN" } }),
  );
  await reviewClause(page, FAKE_CLAUSE);

  await expect.poll(() => posts.length, { message: "POST /api/runs was sent", timeout: 15_000 }).toBe(1);
  expect(posts[0]).toEqual({ run_id: expect.stringMatching(/^[0-9a-f]{32}$/) as unknown, policy_id: "custom", clause_text: FAKE_CLAUSE, attest: true });
  await expect(page).toHaveURL(new RegExp(`#/run/${posts[0]?.run_id ?? ""}$`));

  const banner = page.getByRole("alert");
  await expect(banner.locator("strong")).toHaveText("Your clause seems to contain a PAN.", { timeout: 5_000 });
  await expect(banner.getByRole("button", { name: "Try again" })).toHaveCount(0);
  await expect(page.getByText("Waiting for the run to start")).toHaveCount(0);

  await banner.getByRole("link", { name: "Edit your clause" }).click();
  const card = page.getByRole("region", { name: "Try your own clause" });
  await expect(card.getByRole("textbox")).toHaveValue(FAKE_CLAUSE);
  await expect(card.getByRole("checkbox", { name: ATTEST })).not.toBeChecked();
  expect(errors.filter((e) => !e.includes("400"))).toEqual([]);
});

test("custom review switched off (503) says so, and Try again resends the same clause as a new run", async ({ page }) => {
  // The API's own sentence at the deployed build (runs.py CUSTOM_OFF); the banner says it in its own words.
  const posts = await stubRuns(page, (route) => json(route, 503, { detail: "Custom clause review is switched off right now." }));
  await reviewClause(page, FAKE_CLAUSE);

  const banner = page.getByRole("alert");
  await expect(banner.locator("strong")).toHaveText("Custom clause review is not available right now.", { timeout: 15_000 });
  await expect(banner.locator(".banner-detail")).toHaveText(
    "Pasting your own clause is switched off right now. The sample policies and their saved runs are still on the start screen.",
  );
  await banner.getByRole("button", { name: "Try again" }).click();
  await expect.poll(() => posts.length, { message: "Try again sent a second POST /api/runs", timeout: 15_000 }).toBe(2);
  expect(posts[1]?.run_id).not.toBe(posts[0]?.run_id);
  expect(posts[1]).toMatchObject({ policy_id: "custom", clause_text: FAKE_CLAUSE, attest: true });
});

test("an accepted clause streams onto the normal Review screen as 'Your clause'", async ({ page }) => {
  // The stand-in copies its review content from the seeded FAKE saved run. Without it (an emulator run with
  // ARGUS_E2E_NO_SEED=1 that was never seeded) the copy fails inside the route handler, the POST never answers,
  // and the test would only time out waiting for the summary bar; so check the seed first and name the cause.
  const seeded = await fetch(`${DOCS}/samples/${SEEDED_SAMPLE_ID}`, { headers: ADMIN });
  expect(seeded.status, `samples/${SEEDED_SAMPLE_ID} is in the emulator (run \`npm run seed-demo\`, or drop ARGUS_E2E_NO_SEED=1)`).toBe(200);
  const errors = watchConsole(page);
  const created: string[] = [];
  await stubRuns(page, async (route, body) => {
    created.push(body.run_id);
    await fakeCustomRun(body, uidOf(route.request()));
    await json(route, 200, { run_id: body.run_id, status: "done" });
  });
  try {
    await reviewClause(page, FAKE_CLAUSE);
    await expect(page).toHaveURL(/#\/run\/[0-9a-f]{32}$/);
    await expect(page.locator("header.summary")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Your clause").first()).toBeVisible();
    await expect(page.locator(".clause-row")).toHaveCount(1);
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    await cleanUp(created);
  }
});
