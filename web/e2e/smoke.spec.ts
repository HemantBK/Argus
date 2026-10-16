// Smoke test against the dev server and the local emulators, seeded with `npm run seed-demo`.
import { expect, test } from "@playwright/test";

test("start page signs in, lists the seeded saved run and shows no errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText(/Not legal advice/).first()).toBeVisible();
  // Card names come from whichever saved runs the emulator holds, so check the labels, not a title.
  await expect(page.getByText("Synthetic", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /Open saved run \(recorded/ }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Run demo on a sample NBFC policy" })).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "Could not sign in" })).toHaveCount(0);
  expect(errors).toEqual([]);
});
