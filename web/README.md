# Argus web app

Vite + React + TypeScript (strict), Firebase modular SDK, plain CSS with the UI_SPEC 4.2 tokens. No component
library, no CSS framework. The browser only reads Firestore (rules deny every client write) and calls the API
on Cloud Run directly with a Firebase ID token.

## Quickstart (local, against the Firebase emulators)

Needs Node 22.12 or later, the backend virtualenv (`make setup` in the repo root) and the emulators
(`make up`: Firestore on localhost:8081, Auth on localhost:9099, project `argus-local`).

```
cd web
npm ci
cp .env.example .env.local      # emulator settings; nothing in it is a secret
npm run seed-demo               # FAKE demo data into the emulator
npm run dev                     # http://localhost:5173
```

The browser signs in anonymously on load and prints its uid to the dev console. Live runs (`runs/`) are
readable only by their owner, so to see the seeded live runs: `npm run seed-demo -- --owner-uid <uid>`.

## Scripts

| Script | What it does |
|---|---|
| `dev` / `build` / `preview` | Vite dev server, production build (typechecks first), preview of the build |
| `typecheck` | `tsc --noEmit`, strict |
| `lint` | ESLint flat config, type-aware, zero warnings allowed |
| `test` | Vitest unit tests (selectors, converters, API client, token contrast) |
| `test:emulator` | Vitest against the running emulator: seeded data through the app's converters and selectors |
| `e2e` | Playwright in the installed Google Chrome (`ARGUS_E2E_CHANNEL=chromium` for Playwright's own build). Reseeds the emulator first; the live-run tests skip with a BLOCKED reason when `VITE_API_BASE` is unreachable. Hero screenshots land in `e2e/screenshots/` |
| `gen-types` | Regenerate `src/generated/schema.ts` from `backend/argus_api/schema.py` |
| `gen-types:check` | Fail if `src/generated/schema.ts` is out of date |
| `seed-demo` | Write the FAKE demo dataset to the emulator (refuses without `FIRESTORE_EMULATOR_HOST`) |

## Layout

- `src/generated/schema.ts`: generated wire types (stored JSON, ISO datetimes) and enum value lists. v0.1
  fields are optional so the app reads documents written before and after the schema bump.
- `src/data/convert.ts`: Firestore documents to app types (datetimes to `Date`); a document that breaks the
  contract throws a `DataError` naming the field.
- `src/data/hooks.ts`: `useRun`, `useSteps`, `useFindings`, `useAudit` (onSnapshot, ordered by `seq`),
  `useRunData`, `useSavedRuns`, `useEvalLatest`.
- `src/data/useStartRun.ts`: generates the run id, attaches the four listeners, waits for their first
  snapshot, then POSTs `/api/runs`.
- `src/data/api.ts`: `startRun`, `signoff`, typed `ApiError` with a message fit to show the officer.
- `src/data/selectors.ts`: pure view logic: summary counts, monitor badge, outdated chain, injection quote,
  clause rows.
- `src/start/samplePolicies.ts`: the sample policy files the API reviews (`backend/argus_api/samples/policy_*.json`),
  read at build time, so every sample has a Start card and a Run live button before any saved run of it exists. The
  dev server is allowed to read that one backend folder (`server.fs.allow` in `vite.config.ts`).
- `src/styles/tokens.css`, `src/styles/base.css`: tokens, typography, focus rings, reduced motion.

## Settings

| Variable | Meaning |
|---|---|
| `VITE_USE_EMULATORS` | `true` to use the local emulators |
| `VITE_FIREBASE_API_KEY`, `VITE_FIREBASE_AUTH_DOMAIN`, `VITE_FIREBASE_PROJECT_ID`, `VITE_FIREBASE_APP_ID` | Firebase web config (public by design) |
| `VITE_FIRESTORE_EMULATOR_HOST`, `VITE_AUTH_EMULATOR_URL` | emulator addresses (only with emulators) |
| `VITE_API_BASE` | base URL of the Argus API (Cloud Run), no trailing slash |

A missing setting stops the app with an on-screen error naming it.

Backend changes the web app needs are listed in [BACKEND_REQUESTS.md](BACKEND_REQUESTS.md).
