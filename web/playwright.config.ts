import { defineConfig, devices } from "@playwright/test";

// Chromium only. The dev server runs against the local Firebase emulators (see .env.example); e2e/global-setup.ts
// reseeds them with `npm run seed-demo` before every run. The browser is the installed Google Chrome by default,
// so no browser download is needed; set ARGUS_E2E_CHANNEL=chromium to use Playwright's own build instead
// (`npx playwright install chromium`).
const channel = process.env.ARGUS_E2E_CHANNEL ?? "chrome";

// The "cloud" project (e2e/cloud.spec.ts) runs against the LIVE site and never by default: it exists only when
// selected with `--project=cloud` (or ARGUS_E2E_CLOUD=1), and then the emulator seed and the dev server are skipped.
const argv = process.argv;
const cloud =
  process.env.ARGUS_E2E_CLOUD === "1" ||
  argv.includes("--project=cloud") ||
  argv.some((a, i) => a === "--project" && argv[i + 1] === "cloud");
// Worker processes re-read this config without the CLI arguments; the environment carries the choice to them.
if (cloud) process.env.ARGUS_E2E_CLOUD = "1";
const CLOUD_BASE_URL = "https://argus-aibc.web.app";

export default defineConfig({
  testDir: "e2e",
  globalSetup: cloud ? undefined : "./e2e/global-setup.ts",
  fullyParallel: !cloud,
  workers: cloud ? 1 : undefined,
  forbidOnly: !!process.env.CI,
  reporter: "list",
  use: { baseURL: "http://localhost:5173", trace: "retain-on-failure" },
  projects: cloud
    ? [
        {
          name: "cloud",
          testMatch: /cloud\.spec\.ts$/,
          fullyParallel: false,
          retries: 0,
          use: { ...devices["Desktop Chrome"], channel, viewport: { width: 1280, height: 800 }, baseURL: CLOUD_BASE_URL },
        },
      ]
    : [
        {
          name: "chromium",
          testIgnore: /cloud\.spec\.ts$/,
          use: { ...devices["Desktop Chrome"], channel, viewport: { width: 1280, height: 800 } },
        },
      ],
  webServer: cloud
    ? undefined
    : {
        command: "npm run dev",
        url: "http://localhost:5173",
        reuseExistingServer: !process.env.CI,
        env: { VITE_USE_EMULATORS: "true" },
      },
});
