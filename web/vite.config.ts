/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
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
