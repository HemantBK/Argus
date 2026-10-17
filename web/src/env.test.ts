// VITE_CUSTOM_CLAUSE_OPEN (env.ts): unset means open only against the emulators (local development) and closed in
// any other build, so a deployed build never invites a paste the API would refuse; "true"/"false" decide it; any
// other value stops the app at start-up.
import { afterEach, describe, expect, it, vi } from "vitest";

const BASE = {
  VITE_API_BASE: "http://api.test",
  VITE_FIREBASE_API_KEY: "FAKE-key",
  VITE_FIREBASE_AUTH_DOMAIN: "fake.firebaseapp.com",
  VITE_FIREBASE_PROJECT_ID: "fake-project",
  VITE_FIREBASE_APP_ID: "1:0:web:0",
  VITE_FIRESTORE_EMULATOR_HOST: "localhost:8081",
  VITE_AUTH_EMULATOR_URL: "http://localhost:9099",
};

/** A fresh env.ts (it caches its first read) with these settings. */
async function load(settings: Record<string, string | undefined>) {
  vi.resetModules();
  for (const [k, v] of Object.entries({ ...BASE, ...settings })) vi.stubEnv(k, v);
  return import("./env");
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("VITE_CUSTOM_CLAUSE_OPEN", () => {
  it("unset: open against the emulators", async () => {
    const { appEnv } = await load({ VITE_USE_EMULATORS: "true", VITE_CUSTOM_CLAUSE_OPEN: undefined });
    expect(import.meta.env.VITE_CUSTOM_CLAUSE_OPEN).toBeUndefined();
    expect(appEnv().customClauseOpen).toBe(true);
  });
  it("unset: closed in a build that does not use the emulators (a deployed build)", async () => {
    const { appEnv } = await load({ VITE_USE_EMULATORS: "false", VITE_CUSTOM_CLAUSE_OPEN: undefined });
    expect(appEnv().customClauseOpen).toBe(false);
  });
  it("blank counts as unset", async () => {
    const { appEnv } = await load({ VITE_USE_EMULATORS: "false", VITE_CUSTOM_CLAUSE_OPEN: "   " });
    expect(appEnv().customClauseOpen).toBe(false);
  });
  it.each([
    ["true", "false", true],
    ["false", "true", false],
    [" true ", "false", true],
  ])("%j decides it whatever the emulator setting (VITE_USE_EMULATORS=%s)", async (value, emulators, open) => {
    const { appEnv } = await load({ VITE_USE_EMULATORS: emulators, VITE_CUSTOM_CLAUSE_OPEN: value });
    expect(appEnv().customClauseOpen).toBe(open);
  });
  it.each(["TRUE", "yes", "1", "off"])("%j stops the app with a ConfigError", async (value) => {
    const { appEnv, ConfigError } = await load({ VITE_USE_EMULATORS: "false", VITE_CUSTOM_CLAUSE_OPEN: value });
    expect(() => appEnv()).toThrow(ConfigError);
    expect(() => appEnv()).toThrow(`VITE_CUSTOM_CLAUSE_OPEN must be "true" or "false", not "${value}".`);
  });
});
