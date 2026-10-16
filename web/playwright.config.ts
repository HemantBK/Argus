import { defineConfig, devices } from "@playwright/test";

// Chromium only. The dev server runs against the local Firebase emulators (see .env.example); e2e/global-setup.ts
// reseeds them with `npm run seed-demo` before every run. The browser is the installed Google Chrome by default,
// so no browser download is needed; set ARGUS_E2E_CHANNEL=chromium to use Playwright's own build instead
// (`npx playwright install chromium`).
const channel = process.env.ARGUS_E2E_CHANNEL ?? "chrome";

export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: "list",
  use: { baseURL: "http://localhost:5173", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], channel, viewport: { width: 1280, height: 800 } } }],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    env: { VITE_USE_EMULATORS: "true" },
  },
});
