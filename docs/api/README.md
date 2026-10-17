# Argus API reference

[openapi.json](openapi.json) is the OpenAPI 3.1 description of the Argus API. It was generated offline from
the FastAPI app object at commit `7a4a5d1`, whose `backend/argus_api` is the same as the deployed build
`7c3072d`. No server was started and no production service was called:

```bash
# from a clone, with the backend venv installed (make setup)
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "
import json
from argus_api.main import app
print(json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False))
" > ../docs/api/openapi.json
```

Importing the app needs no settings and no library: both load when the server starts, not at import.

## Endpoints

| Method and path | Auth | What it does |
|---|---|---|
| `GET /health` | none | What the server runs: git sha, mode, provider, pinned models, Gemini location, library hash, and the agent model's circuit breaker state. Calls no other service. |
| `POST /api/runs` | Firebase ID token | Reviews a sample policy (`{run_id, policy_id}`) and returns the finished run; the browser follows the steps through Firestore listeners while it runs. |
| `POST /api/runs/{run_id}/findings/{clause_id}/signoff` | Firebase ID token, run owner only | Records the officer's accept, reject or edit, with a note, in the audit log. |
| `POST /api/smoke` | Firebase ID token | One Gemini call through the same caps as a run; used by the deployment checks. |

`POST /api/runs` also accepts a pasted clause (`policy_id: "custom"`, `clause_text`, `attest: true`). That
path is built and tested but switched off in production, where it answers 503.

## Errors the generated file does not list

FastAPI lists only 200 and 422. The code also answers:

| Status | When | Where in the code |
|---|---|---|
| 400 | a pasted clause that fails its checks (too short, too long, not attested, looks like it holds an identifier) | `custom.py` |
| 401 | no `Authorization: Bearer <Firebase ID token>`, or an invalid token | `auth.py` |
| 403, 404, 409 | sign-off on a run the caller does not own; an unknown policy, run or clause; a duplicate `run_id` or a conflicting sign-off | `runs.py` (`Rejected` and its subclasses) |
| 422 (from the code) | a request the API refuses as invalid, for example `clause_text` sent with a sample policy | `runs.py` (`Invalid`), `main.py` |
| 413 | a request body over 64 KiB, refused before it is read in full | `main.py` (`_BodyLimit`) |
| 429 | a daily, hourly or per-visitor run cap is used up | `runs.py` (`admit_counted`) |
| 502 | `/api/smoke` only: the model call failed | `main.py` |
| 503 | runs are paused (kill switch), the limits document is missing, pasting is switched off, or Firestore is busy or unavailable | `runs.py`, `main.py` |

Every refusal carries a plain-English `detail`. Validation errors (422) name the field and the problem
without echoing the submitted value.

## Response shapes

The run, finding, step and audit documents are defined as Pydantic models in
[backend/argus_api/schema.py](../../backend/argus_api/schema.py). The web app's TypeScript types are generated
from them (`web/scripts/gen-types.mjs` writes `web/src/generated/schema.ts`; `npm run gen-types:check` in
`web/` reports a stale file). Findings are JSON with the citation's URL, paragraph, quote, dates and content hash; feeding them
into a GRC tool is roadmap, not built.
