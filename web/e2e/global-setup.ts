// Reseed the emulator before the e2e run, so the tests read the FAKE demo dataset and not whatever other
// tools left in the shared emulator (for example a placeholder eval_results/latest, BACKEND_REQUESTS.md item 7).
// Set ARGUS_E2E_NO_SEED=1 to run against the emulator as it is.
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export default function globalSetup(): void {
  if (process.env.ARGUS_E2E_NO_SEED === "1") return;
  const web = fileURLToPath(new URL("..", import.meta.url));
  execFileSync("npm", ["run", "seed-demo"], { cwd: web, stdio: "inherit" });
}
