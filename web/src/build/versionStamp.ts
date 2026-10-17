// Build time only (Node): never imported by the app. A Vite plugin that writes dist/version.json, so anyone can
// read back which commit a deployed web build came from and the settings it was built with:
//   { sha, dirty, builtAt, customClauseOpen, apiBase }
// customClauseOpen and apiBase are resolved by the same functions env.ts uses (buildSettings.ts) from the env Vite
// builds with, so the stamp states what the app will do, not a second guess at it.
//
// A production build (`vite build`, mode "production") refuses to run from a tree with uncommitted or untracked
// changes, because its sha would not describe what was built. For a local experiment only, set
// ARGUS_ALLOW_DIRTY_BUILD=1: the build then goes ahead and the stamp says "dirty": true, which the deploy check
// (scripts/check.py deploy) rejects, so such a build cannot pass as a release.
import { execFileSync } from "node:child_process";
import type { Plugin, ResolvedConfig } from "vite";
import { resolveApiBase, resolveCustomClauseOpen, type EnvRecord } from "../buildSettings";

export const VERSION_FILE = "version.json";
export const ALLOW_DIRTY_ENV = "ARGUS_ALLOW_DIRTY_BUILD";

export interface VersionStamp {
  /** Full commit sha of HEAD when the build ran. */
  sha: string;
  /** True when the working tree had uncommitted or untracked changes (git status --porcelain not empty). */
  dirty: boolean;
  /** ISO 8601 UTC time the build ran. */
  builtAt: string;
  /** The resolved VITE_CUSTOM_CLAUSE_OPEN, as env.ts resolves it. */
  customClauseOpen: boolean;
  /** The resolved VITE_API_BASE, as env.ts resolves it. */
  apiBase: string;
}

export interface GitState {
  sha: string;
  dirty: boolean;
}

export class DirtyBuildError extends Error {
  override name = "DirtyBuildError";
}

/** HEAD and whether the tree is clean, the same test scripts/check.py uses (git status --porcelain). */
export function readGitState(cwd: string): GitState {
  const git = (args: string[]): string => execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  let sha: string;
  let status: string;
  try {
    sha = git(["rev-parse", "HEAD"]).trim();
    status = git(["status", "--porcelain"]);
  } catch (e) {
    throw new Error(`The build stamp needs git to name the commit being built, and git failed in ${cwd}: ${String(e)}`, { cause: e });
  }
  if (!/^[0-9a-f]{40}$/.test(sha)) throw new Error(`git rev-parse HEAD gave "${sha}", not a commit sha.`);
  return { sha, dirty: status.trim() !== "" };
}

export function makeStamp(git: GitState, env: EnvRecord, now: Date): VersionStamp {
  return {
    sha: git.sha,
    dirty: git.dirty,
    builtAt: now.toISOString(),
    customClauseOpen: resolveCustomClauseOpen(env),
    apiBase: resolveApiBase(env),
  };
}

export interface VersionStampOptions {
  /** Defaults to readGitState(the Vite root). */
  git?: (root: string) => GitState;
  /** Defaults to the current time. */
  now?: () => Date;
  /** Defaults to process.env.ARGUS_ALLOW_DIRTY_BUILD === "1". */
  allowDirty?: boolean;
}

export function versionStamp(options: VersionStampOptions = {}): Plugin {
  const readGit = options.git ?? readGitState;
  const now = options.now ?? (() => new Date());
  const allowDirty = options.allowDirty ?? process.env[ALLOW_DIRTY_ENV] === "1";
  let config: ResolvedConfig | undefined;
  let stamp: VersionStamp | undefined;
  return {
    name: "argus-version-stamp",
    apply: "build",
    configResolved(resolved) {
      config = resolved;
    },
    buildStart() {
      if (!config) throw new Error("argus-version-stamp: the Vite config was not resolved.");
      const git = readGit(config.root);
      if (git.dirty && config.mode === "production") {
        if (!allowDirty) {
          throw new DirtyBuildError(
            `Refusing a production build from a tree with uncommitted or untracked changes (HEAD ${git.sha.slice(0, 12)}): ` +
              `dist/${VERSION_FILE} would name a commit that is not what was built. Commit or stash first. ` +
              `For a local experiment only, set ${ALLOW_DIRTY_ENV}=1 (the stamp then says "dirty": true and the deploy check rejects it).`,
          );
        }
        config.logger.warn(`argus-version-stamp: ${ALLOW_DIRTY_ENV}=1, building from a dirty tree; ${VERSION_FILE} says "dirty": true.`);
      }
      // Resolved here, before any output, so a bad VITE_* setting stops the build instead of shipping an app that
      // stops at start-up.
      stamp = makeStamp(git, config.env as EnvRecord, now());
    },
    generateBundle() {
      if (!stamp) throw new Error("argus-version-stamp: no stamp was made at build start.");
      this.emitFile({ type: "asset", fileName: VERSION_FILE, source: `${JSON.stringify(stamp, null, 2)}\n` });
    },
  };
}
