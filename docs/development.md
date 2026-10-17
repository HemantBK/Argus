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
