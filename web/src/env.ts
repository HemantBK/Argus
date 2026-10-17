// Every VITE_* setting the app reads, checked once at start-up. A missing value is a loud error, never a guess.

export class ConfigError extends Error {
  override name = "ConfigError";
}

function required(name: string): string {
  const value = (import.meta.env as Record<string, string | undefined>)[name];
  if (value === undefined || value.trim() === "") {
    throw new ConfigError(`Missing setting ${name}. Copy web/.env.example to web/.env.local (local) or set it at build time.`);
  }
  return value.trim();
}

export interface AppEnv {
  useEmulators: boolean;
  apiBase: string;
  firebase: { apiKey: string; authDomain: string; projectId: string; appId: string };
  firestoreEmulatorHost: string; // "host:port"
  authEmulatorUrl: string; // "http://host:port"
  /**
   * Whether the Start screen offers "Try your own clause". The API's own switch (config/custom_limits) is not
   * readable from the browser, so the build says it: VITE_CUSTOM_CLAUSE_OPEN "true" or "false"; when unset, open
   * against the emulators (local development, where the local API's switch is set by hand) and closed otherwise,
   * so a deployed build never invites a paste the API would refuse.
   */
  customClauseOpen: boolean;
}

function customClauseOpen(useEmulators: boolean): boolean {
  const value = (import.meta.env as Record<string, string | undefined>).VITE_CUSTOM_CLAUSE_OPEN?.trim();
  if (value === undefined || value === "") return useEmulators;
  if (value === "true") return true;
  if (value === "false") return false;
  throw new ConfigError(`VITE_CUSTOM_CLAUSE_OPEN must be "true" or "false", not "${value}".`);
}

let cached: AppEnv | undefined;

export function appEnv(): AppEnv {
  if (cached) return cached;
  const useEmulators = import.meta.env.VITE_USE_EMULATORS === "true";
  cached = {
    useEmulators,
    apiBase: required("VITE_API_BASE").replace(/\/+$/, ""),
    firebase: {
      apiKey: required("VITE_FIREBASE_API_KEY"),
      authDomain: required("VITE_FIREBASE_AUTH_DOMAIN"),
      projectId: required("VITE_FIREBASE_PROJECT_ID"),
      appId: required("VITE_FIREBASE_APP_ID"),
    },
    firestoreEmulatorHost: useEmulators ? required("VITE_FIRESTORE_EMULATOR_HOST") : "",
    authEmulatorUrl: useEmulators ? required("VITE_AUTH_EMULATOR_URL") : "",
    customClauseOpen: customClauseOpen(useEmulators),
  };
  return cached;
}
