# Development

Needs Python 3.11, Docker, Node 22.12 or later, and an OpenRouter or Google AI Studio key. Local mode uses the
Firebase emulators, so no Google Cloud account is needed.

```bash
git clone https://github.com/HemantBK/Argus argus && cd argus
make setup                              # Python 3.11 virtualenv in backend/.venv
cp backend/.env.example backend/.env    # set OPENROUTER_API_KEY (or ARGUS_LLM_PROVIDER=aistudio + GEMINI_API_KEY)
make up                                 # Firebase Auth + Firestore emulators in Docker
make corpus                             # fetch the RBI pages and build backend/data/corpus
make load-corpus                        # write the library to the Firestore emulator
make limits CAP=60 HOURLY_CAP=20 USER_CAP=5 ENABLED=true
make run                                # API on http://localhost:8080
```

Web app, in a second terminal: `cd web && npm ci && cp .env.example .env.local && npm run dev`, then open
http://localhost:5173.

## Tests and checks

- Backend: `make test` (offline unit tests), `make lint` (ruff + mypy strict), `make test-emulator` (after `make up`).
- Web: `npm test`, `npm run lint`, `npm run typecheck`, `npm run e2e`.
- Rules: `make check` runs `scripts/check.py commit`; `make install-hooks` runs it before every commit. The
  `deploy` and `submission` stages check the live deployment and the submission links.

## Releasing

`scripts/release.sh` has three subcommands. Run them from the repo root with gcloud and firebase signed in. Each
one stops at the first failed check.

- `candidate`: runs the tests, deploys a new API revision with no traffic, and runs one live review of sample
  policy A on it.
- `promote`: moves all traffic to that revision and checks the public URL (traffic goes back if this fails),
  then releases the web app the same way `web` does.
- `web`: releases only the web app and the security rules, for a commit whose API code is the same as the
  serving API's. It needs a clean tree and builds the app. Before deploying, it checks the build's
  `version.json`: the commit is HEAD, the tree was clean, it calls the production API, and if it offers pasting
  a clause, `config/custom_limits` has `custom_enabled` true (otherwise nothing is deployed). Then it deploys
  and runs `check.py deploy --strict --expect-web-sha HEAD` and the rules check. If a check fails, it prints the
  `firebase hosting:clone` command that puts back the web version that was live before.

What the checks guard:

- The web build writes `version.json`: its commit, whether the tree was clean, the API it calls and whether
  pasting a clause is open. `D-WEB-SHA` fails when that file is missing, the build was not clean, it calls
  another API, its commit changes API code the serving API does not have, or it offers pasting while the API's
  switch (`config/custom_limits`) is off.
- `D-HEALTH` checks the API's commit, models and library hash; the other `D-` checks cover the served files,
  CORS, sign-in, rules, limits and the Cloud Run settings.
- `S-CLAIMS` (`check.py submission --claims-text <submission text>`) checks that the run caps, model names,
  library dates and evaluation run ids in README.md, `docs/` and the submission text match production.

## Modes

`ARGUS_MODE=local` uses the emulators and OpenRouter or AI Studio. `ARGUS_MODE=cloud` (the deployed app) uses real
Firestore, real Firebase Auth and Gemini on Vertex AI only; the API refuses to start in cloud mode with any other
provider or with emulator settings.

## Repository map

| Path | What |
|---|---|
| `backend/argus_api/agent/`, `monitor/`, `corpus/` | Agent loop, step monitor, RBI library and quote verifier |
| `backend/argus_api/runs.py`, `main.py` | Run API, limits, sign-off, Firestore writes |
| `backend/argus_api/eval/` | Evaluation harness and cases |
| `web/src/` | React app: start, review workspace, audit log, evaluation |
| `scripts/check.py`, `scripts/cloud_e2e.py` | Commit, deploy and submission checks; live tests against the deployed stack |
| `firestore.rules`, `firebase.json` | Security rules and Hosting config |

## Decision ids in comments

Some code comments cite a decision id such as `(D-33)`. These point to the team's private decision log, which is
not part of this repository; each comment states the decision it relies on, so the id is only a cross-reference.
