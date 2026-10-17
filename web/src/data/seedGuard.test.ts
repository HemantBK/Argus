// seed_demo.py writes FAKE runs, so it must refuse to start unless FIRESTORE_EMULATOR_HOST is set, and refuse
// before it loads the backend or any Firestore library (so no client can exist). The script is run for real
// with poisoned `google` and `pydantic` packages first on PYTHONPATH: had it imported either before the guard,
// the poison would fail the run first.
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";

const PYTHON = fileURLToPath(new URL("../../../backend/.venv/bin/python", import.meta.url));
const SCRIPT = fileURLToPath(new URL("../../scripts/seed_demo.py", import.meta.url));

const poison = mkdtempSync(join(tmpdir(), "argus-seed-guard-"));
for (const pkg of ["google", "pydantic"]) {
  mkdirSync(join(poison, pkg));
  writeFileSync(join(poison, pkg, "__init__.py"), `raise ImportError("POISON: ${pkg} imported before the emulator guard")\n`);
}
afterAll(() => {
  rmSync(poison, { recursive: true, force: true });
});

function seed(env: Record<string, string>) {
  const base = Object.fromEntries(Object.entries(process.env).filter(([k]) => k !== "FIRESTORE_EMULATOR_HOST")) as Record<string, string>;
  return spawnSync(PYTHON, [SCRIPT], { encoding: "utf8", env: { ...base, PYTHONPATH: poison, ...env } });
}

describe("seed_demo.py emulator guard", () => {
  it.each<Record<string, string>>([{}, { FIRESTORE_EMULATOR_HOST: "" }, { FIRESTORE_EMULATOR_HOST: "  " }])("refuses before loading anything when the host is %j", (env) => {
    const out = seed(env);
    expect(out.status).toBe(1);
    expect(out.stderr).toMatch(/seed_demo refused: FIRESTORE_EMULATOR_HOST is not set/);
    expect(out.stderr).not.toMatch(/POISON/);
  });

  it("the poison is real: with the host set, the script gets past the guard and hits it", () => {
    const out = seed({ FIRESTORE_EMULATOR_HOST: "localhost:1" });
    expect(out.status).not.toBe(0);
    expect(out.stderr).toMatch(/POISON: pydantic imported before the emulator guard/);
  });
});
