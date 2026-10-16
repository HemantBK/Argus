import { defineConfig } from "vitest/config";

// Tests that read the local Firestore emulator (seeded with `npm run seed-demo`). Not part of `npm test`.
export default defineConfig({
  test: { include: ["src/**/*.emulator.test.ts"], environment: "node", testTimeout: 15000 },
});
