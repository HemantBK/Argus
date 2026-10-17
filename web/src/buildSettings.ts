// How the VITE_* settings resolve, as pure functions of an env record. env.ts applies them to import.meta.env when
// the app starts, and the build stamp (build/versionStamp.ts) applies them to the env Vite builds with, so
// dist/version.json states the same values the deployed app uses (one rule, two readers).

export class ConfigError extends Error {
  override name = "ConfigError";
}

export type EnvRecord = Readonly<Record<string, string | boolean | undefined>>;

function read(env: EnvRecord, name: string): string | undefined {
  const value = env[name];
  return typeof value === "string" ? value : undefined;
}

export function requiredSetting(env: EnvRecord, name: string): string {
  const value = read(env, name);
  if (value === undefined || value.trim() === "") {
    throw new ConfigError(`Missing setting ${name}. Copy web/.env.example to web/.env.local (local) or set it at build time.`);
  }
  return value.trim();
}

export function resolveUseEmulators(env: EnvRecord): boolean {
  return read(env, "VITE_USE_EMULATORS") === "true";
}

/** VITE_API_BASE, trimmed, without trailing slashes. */
export function resolveApiBase(env: EnvRecord): string {
  return requiredSetting(env, "VITE_API_BASE").replace(/\/+$/, "");
}

/**
 * Whether the Start screen offers "Try your own clause". VITE_CUSTOM_CLAUSE_OPEN "true" or "false"; when unset or
 * blank, open against the emulators (local development) and closed otherwise, so a deployed build never invites a
 * paste the API would refuse. Any other value is a ConfigError.
 */
export function resolveCustomClauseOpen(env: EnvRecord): boolean {
  const value = read(env, "VITE_CUSTOM_CLAUSE_OPEN")?.trim();
  if (value === undefined || value === "") return resolveUseEmulators(env);
  if (value === "true") return true;
  if (value === "false") return false;
  throw new ConfigError(`VITE_CUSTOM_CLAUSE_OPEN must be "true" or "false", not "${value}".`);
}
