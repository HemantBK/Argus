// Live-run spike (UI_SPEC 16.4): anonymous sign-in, then the run listener (an owner query), and only then
// POST /api/runs {run_id, policy_id} with the Firebase ID token, after which the listeners stream the run.
// Needs the real API at VITE_API_BASE; skipped with a BLOCKED reason when it cannot be reached.
import { expect, test, type Request } from "@playwright/test";
import { apiBase, apiUnreachable, watchConsole } from "./helpers";

test("Run demo signs in, attaches listeners, then POSTs /api/runs and streams the run", async ({ page }) => {
  const base = apiBase();
  const unreachable = await apiUnreachable(base);
  test.skip(
    unreachable !== null,
    `BLOCKED: the Argus API at VITE_API_BASE=${base} is unreachable (${unreachable ?? ""}). Start the backend ` +
      "(with ARGUS_ALLOWED_ORIGINS=http://localhost:5173, BACKEND_REQUESTS.md item 4) and rerun.",
  );
  const errors = watchConsole(page);
  const order: string[] = [];
  let post: Request | null = null;
  page.on("request", (req) => {
    const url = req.url();
    if (url.includes("identitytoolkit") && url.includes("signUp")) order.push("sign-in");
    else if (url.includes("google.firestore.v1.Firestore/Listen")) order.push("listen");
    else if (req.method() === "POST" && url === `${base}/api/runs`) {
      order.push("post");
      post = req;
    }
  });

  await page.goto("/");
  await expect(page.getByRole("button", { name: "Run demo on a sample NBFC policy" })).toBeEnabled();
  await page.getByRole("button", { name: "Run demo on a sample NBFC policy" }).click();
  await expect(page).toHaveURL(/#\/run\/[0-9a-f]{32}$/);
  const runId = /#\/run\/([0-9a-f]{32})$/.exec(page.url())?.[1];

  await expect.poll(() => post !== null, { message: "POST /api/runs was sent", timeout: 15_000 }).toBe(true);
  const sent = post as unknown as Request;
  expect(sent.headers()["authorization"]).toMatch(/^Bearer \S+$/);
  expect(sent.postDataJSON()).toEqual({ run_id: runId, policy_id: "policy_a" });
  const firstPost = order.indexOf("post");
  expect(order.slice(0, firstPost), `requests before the POST: ${order.join(", ")}`).toContain("listen");
  if (order.includes("sign-in")) expect(order.indexOf("sign-in")).toBeLessThan(firstPost);

  // The listeners stream the run: the summary bar shows it as live and the trace records steps.
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator("header.summary")).toContainText(/Live run/, { timeout: 30_000 });
  await expect(page.locator(".trace-summary")).not.toContainText(/^0 steps/, { timeout: 60_000 });
  expect(errors).toEqual([]);
});
