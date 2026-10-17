// Every VITE_* setting the app reads, checked once at start-up. A missing value is a loud error, never a guess.
// The rules live in buildSettings.ts, shared with the build stamp (dist/version.json) so both state the same values.
import { requiredSetting, resolveApiBase, resolveCustomClauseOpen, resolveUseEmulators, type EnvRecord } from "./buildSettings";

export { ConfigError } from "./buildSettings";

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
   * so a deployed build never invites a paste the API would refuse. The build writes the same resolved value to
   * dist/version.json (src/build/versionStamp.ts).
   */
  customClauseOpen: boolean;
}

let cached: AppEnv | undefined;

export function appEnv(): AppEnv {
  if (cached) return cached;
  const env = import.meta.env as EnvRecord;
  const useEmulators = resolveUseEmulators(env);
  cached = {
    useEmulators,
    apiBase: resolveApiBase(env),
    firebase: {
      apiKey: requiredSetting(env, "VITE_FIREBASE_API_KEY"),
      authDomain: requiredSetting(env, "VITE_FIREBASE_AUTH_DOMAIN"),
      projectId: requiredSetting(env, "VITE_FIREBASE_PROJECT_ID"),
      appId: requiredSetting(env, "VITE_FIREBASE_APP_ID"),
    },
    firestoreEmulatorHost: useEmulators ? requiredSetting(env, "VITE_FIRESTORE_EMULATOR_HOST") : "",
    authEmulatorUrl: useEmulators ? requiredSetting(env, "VITE_AUTH_EMULATOR_URL") : "",
    customClauseOpen: resolveCustomClauseOpen(env),
  };
  return cached;
}
