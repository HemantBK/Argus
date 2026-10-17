/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { versionStamp } from "./src/build/versionStamp";

export default defineConfig({
  // versionStamp writes dist/version.json ({ sha, dirty, builtAt, customClauseOpen, apiBase }) and refuses a
  // production build from a dirty tree; ARGUS_ALLOW_DIRTY_BUILD=1 allows one for local experiments only, stamped
  // "dirty": true (src/build/versionStamp.ts).
  plugins: [react(), versionStamp()],
  // The Start screen bundles the sample policy files the API reviews (src/start/samplePolicies.ts), so the dev
  // server must be allowed to read that one backend folder.
  server: { port: 5173, strictPort: true, fs: { allow: [".", "../backend/argus_api/samples"] } },
  build: { sourcemap: true },
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    exclude: ["src/**/*.emulator.test.ts", "node_modules/**"],
    environment: "node",
  },
});
