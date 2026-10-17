// The build stamp (versionStamp.ts): a real Vite build of a tiny page in a temp folder, so these tests check the
// version.json the build emits, its refusal of a dirty production build, and that customClauseOpen and apiBase
// equal what env.ts resolves from the same settings.
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build } from "vite";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { ALLOW_DIRTY_ENV, readGitState, VERSION_FILE, versionStamp, type GitState, type VersionStamp } from "./versionStamp";

const CLEAN: GitState = { sha: "a".repeat(40), dirty: false };
const DIRTY: GitState = { sha: "b".repeat(40), dirty: true };
const REFUSED = /DirtyBuildError: Refusing a production build from a tree with uncommitted or untracked changes/;
const NOW = new Date("2026-10-18T03:30:00.000Z");

const FIREBASE = {
  VITE_FIREBASE_API_KEY: "FAKE-key",
  VITE_FIREBASE_AUTH_DOMAIN: "fake.firebaseapp.com",
  VITE_FIREBASE_PROJECT_ID: "fake-project",
  VITE_FIREBASE_APP_ID: "1:0:web:0",
  VITE_FIRESTORE_EMULATOR_HOST: "localhost:8081",
  VITE_AUTH_EMULATOR_URL: "http://localhost:9099",
};

const roots: string[] = [];
let savedProcessEnv: Record<string, string | undefined> = {};

// VITE_* variables in the shell would override the .env file Vite reads; keep them out of these builds.
beforeAll(() => {
  savedProcessEnv = {};
  for (const key of Object.keys(process.env)) {
    if (key.startsWith("VITE_") || key === ALLOW_DIRTY_ENV) {
      savedProcessEnv[key] = process.env[key];
      Reflect.deleteProperty(process.env, key);
    }
  }
});
afterAll(() => {
  for (const [k, v] of Object.entries(savedProcessEnv)) if (v !== undefined) process.env[k] = v;
  for (const r of roots) rmSync(r, { recursive: true, force: true });
});
afterEach(() => {
  vi.unstubAllEnvs();
});

/** A temp folder with a one-script page and a .env file holding these settings. */
function fixture(settings: Record<string, string | undefined>): string {
  const root = mkdtempSync(join(tmpdir(), "argus-stamp-"));
  roots.push(root);
  writeFileSync(join(root, "index.html"), '<!doctype html><html><body><script type="module" src="/main.js"></script></body></html>\n');
  writeFileSync(join(root, "main.js"), "document.body.dataset.ok = '1';\n");
  const lines = Object.entries(settings)
    .filter((e): e is [string, string] => e[1] !== undefined)
    .map(([k, v]) => `${k}=${v}`);
  writeFileSync(join(root, ".env"), `${lines.join("\n")}\n`);
  return root;
}

async function stampFor(
  settings: Record<string, string | undefined>,
  { git = CLEAN, mode = "production", allowDirty = false }: { git?: GitState; mode?: string; allowDirty?: boolean } = {},
): Promise<VersionStamp> {
  const root = fixture(settings);
  const out = await build({
    root,
    mode,
    configFile: false,
    envDir: root,
    logLevel: "silent",
    plugins: [versionStamp({ git: () => git, now: () => NOW, allowDirty })],
    build: { write: false, outDir: join(root, "dist") },
  });
  const outputs = (Array.isArray(out) ? out : [out]).flatMap((o) => ("output" in o ? o.output : []));
  const asset = outputs.find((f) => f.fileName === VERSION_FILE);
  if (asset?.type !== "asset") throw new Error(`the build emitted no ${VERSION_FILE}`);
  return JSON.parse(String(asset.source)) as VersionStamp;
}

/** A fresh env.ts (it caches its first read) with these settings, as the app would see them. */
async function appEnvFor(settings: Record<string, string | undefined>) {
  vi.resetModules();
  for (const [k, v] of Object.entries(settings)) vi.stubEnv(k, v);
  const { appEnv } = await import("../env");
  return appEnv();
}

describe("dist/version.json", () => {
  it("states the commit, the tree state, the build time and the resolved settings, and nothing else", async () => {
    const stamp = await stampFor({ ...FIREBASE, VITE_API_BASE: " https://api.example.test/// ", VITE_USE_EMULATORS: "false" });
    expect(stamp).toEqual({
      sha: CLEAN.sha,
      dirty: false,
      builtAt: "2026-10-18T03:30:00.000Z",
      customClauseOpen: false,
      apiBase: "https://api.example.test",
    });
  });

  it("is written next to index.html in a real build output", async () => {
    const root = fixture({ ...FIREBASE, VITE_API_BASE: "https://api.example.test" });
    await build({
      root,
      configFile: false,
      envDir: root,
      logLevel: "silent",
      plugins: [versionStamp({ git: () => CLEAN, now: () => NOW })],
      build: { outDir: join(root, "dist") },
    });
    const { readFileSync } = await import("node:fs");
    const stamp = JSON.parse(readFileSync(join(root, "dist", VERSION_FILE), "utf8")) as VersionStamp;
    expect(stamp.sha).toBe(CLEAN.sha);
    expect(readFileSync(join(root, "dist", "index.html"), "utf8")).toMatch(/<script/);
  });

  it("stops the build when VITE_API_BASE is missing, instead of shipping an app that stops at start-up", async () => {
    await expect(stampFor({ ...FIREBASE })).rejects.toThrow("Missing setting VITE_API_BASE");
  });

  it("stops the build when VITE_CUSTOM_CLAUSE_OPEN is not true or false", async () => {
    await expect(stampFor({ ...FIREBASE, VITE_API_BASE: "https://api.example.test", VITE_CUSTOM_CLAUSE_OPEN: "yes" })).rejects.toThrow(
      'VITE_CUSTOM_CLAUSE_OPEN must be "true" or "false", not "yes".',
    );
  });
});

describe("dirty tree", () => {
  const settings = { ...FIREBASE, VITE_API_BASE: "https://api.example.test" };

  it("refuses a production build from a dirty tree", async () => {
    // Vite wraps a plugin's error; the DirtyBuildError is its cause.
    const error: unknown = await stampFor(settings, { git: DIRTY }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(Error);
    expect(String(error)).toMatch(REFUSED);
    expect(String(error)).toContain(`set ${ALLOW_DIRTY_ENV}=1`);
  });

  it(`builds a dirty tree only with ${ALLOW_DIRTY_ENV}=1, and says so in the stamp`, async () => {
    const stamp = await stampFor(settings, { git: DIRTY, allowDirty: true });
    expect(stamp.dirty).toBe(true);
    expect(stamp.sha).toBe(DIRTY.sha);
  });

  it(`reads ${ALLOW_DIRTY_ENV} from the environment, and only the value 1 allows it`, async () => {
    const root = fixture(settings);
    const run = () =>
      build({
        root,
        configFile: false,
        envDir: root,
        logLevel: "silent",
        plugins: [versionStamp({ git: () => DIRTY, now: () => NOW })],
        build: { write: false },
      });
    vi.stubEnv(ALLOW_DIRTY_ENV, "true");
    await expect(run()).rejects.toThrow(REFUSED);
    vi.stubEnv(ALLOW_DIRTY_ENV, "1");
    await expect(run()).resolves.toBeTruthy();
  });

  it("does not refuse a non-production build (for example --mode development), which still records dirty", async () => {
    const stamp = await stampFor(settings, { git: DIRTY, mode: "development" });
    expect(stamp.dirty).toBe(true);
  });
});

describe("readGitState", () => {
  it("names HEAD and reports untracked and modified files as dirty, like scripts/check.py", () => {
    const repo = mkdtempSync(join(tmpdir(), "argus-git-"));
    roots.push(repo);
    const git = (...args: string[]) => execFileSync("git", args, { cwd: repo, encoding: "utf8" });
    git("init", "-q");
    writeFileSync(join(repo, "a.txt"), "one\n");
    git("add", "a.txt");
    git("-c", "user.name=FAKE", "-c", "user.email=fake@example.test", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "FAKE");
    const head = git("rev-parse", "HEAD").trim();
    expect(readGitState(repo)).toEqual({ sha: head, dirty: false });
    writeFileSync(join(repo, "new.txt"), "untracked\n");
    expect(readGitState(repo)).toEqual({ sha: head, dirty: true });
    rmSync(join(repo, "new.txt"));
    writeFileSync(join(repo, "a.txt"), "changed\n");
    expect(readGitState(repo)).toEqual({ sha: head, dirty: true });
  });

  it("fails loudly outside a git repository", () => {
    const dir = mkdtempSync(join(tmpdir(), "argus-nogit-"));
    roots.push(dir);
    vi.stubEnv("GIT_CEILING_DIRECTORIES", tmpdir());
    expect(() => readGitState(dir)).toThrow(/needs git/);
  });
});

describe("the stamp and env.ts agree", () => {
  // Every case the VITE_CUSTOM_CLAUSE_OPEN rule distinguishes (env.test.ts), plus the production setting.
  const cases: [string, Record<string, string | undefined>][] = [
    ["unset, emulators", { VITE_USE_EMULATORS: "true", VITE_CUSTOM_CLAUSE_OPEN: undefined }],
    ["unset, deployed build", { VITE_USE_EMULATORS: "false", VITE_CUSTOM_CLAUSE_OPEN: undefined }],
    ["unset, no emulator setting", { VITE_USE_EMULATORS: undefined, VITE_CUSTOM_CLAUSE_OPEN: undefined }],
    ["blank", { VITE_USE_EMULATORS: "false", VITE_CUSTOM_CLAUSE_OPEN: "   " }],
    ["true over deployed", { VITE_USE_EMULATORS: "false", VITE_CUSTOM_CLAUSE_OPEN: "true" }],
    ["false over emulators", { VITE_USE_EMULATORS: "true", VITE_CUSTOM_CLAUSE_OPEN: "false" }],
    ["padded true", { VITE_USE_EMULATORS: "false", VITE_CUSTOM_CLAUSE_OPEN: " true " }],
  ];
  it.each(cases)("%s", async (_name, custom) => {
    const settings = { ...FIREBASE, VITE_API_BASE: "https://api.example.test/", ...custom };
    const stamp = await stampFor(settings);
    const app = await appEnvFor(settings);
    expect(stamp.customClauseOpen).toBe(app.customClauseOpen);
    expect(stamp.apiBase).toBe(app.apiBase);
  });
});
