# Backend requests from the web app

The web workflow owns only `web/`. These are changes it needs outside `web/`, in priority order. Each one
says what breaks today and how to check the fix.

## 1. Firestore rules for a run that does not exist yet: resolved in web/, keep `run_id` and `owner_uid` on runs

Resolved 2026-10-16 without a rules change. The web app no longer reads `runs/{id}` directly before the API creates
it (the current rules refuse that, "Null value error"). It watches a live run in two stages (web/src/data/hooks.ts):

1. before `POST /api/runs`, the query `runs where owner_uid == <caller uid> and run_id == <id>`, which the current
   `allow read: if signedIn() && resource.data.owner_uid == request.auth.uid` permits while the run is missing;
2. steps, findings and audit once that query returns the run.

What the web app relies on, please keep:
- every `runs/{id}` document has `run_id == id` and `owner_uid` = the verified uid (schema.py `Run` does today);
- the API creates `runs/{id}` before it writes anything under it (admission in runs.py does today);
- a query with equality filters on `owner_uid` and `run_id` needs no composite index in production Firestore
  (two equality filters are served by single-field indexes); if `firestore.indexes.json` ever disables
  single-field indexing on `runs`, add one for (`owner_uid`, `run_id`).

Checked by `npm run test:emulator` ("lets the owner watch a live run before the API creates it, and nobody else":
the owner's query is allowed before creation and sees the run appear; another user's query is empty and its direct
reads of the run and its steps are refused). The earlier proposed rule change is no longer needed.

## 2. A schema for `eval_results/latest`

The Evaluation page reads `eval_results/latest` (public), but `schema.py` has no model for it, so the app can
only treat it as untyped data (`useEvalLatest` in web/src/data/hooks.ts). Please add a pydantic model (for
example `EvalResults`: conditions x metrics, each metric with value or "not measured", n, run date; corpus
coverage: documents, as-on date, paragraph count, supersession rows; failure cases). `npm run gen-types` will
pick it up once it is added to `MODELS` in web/scripts/gen-types.mjs.

## 3. Rule-test fixtures in `samples/` that are not runs

`backend/tests_emulator/test_firebase.py` writes `samples/test-sample-<hex>` documents that are not `Run`
documents (no `run_id`, `status`, ...) into the shared emulator and leaves them there. The Start screen lists
`samples/` and reports each such document as "could not be read" (it never drops data silently). Please either
delete the fixtures at the end of the test or write `Run`-shaped fixtures.

## 4. CORS for local development

The browser calls the API directly (D-33). For local work the API needs
`ARGUS_ALLOWED_ORIGINS=http://localhost:5173` (the Vite dev server); for the deployed app, the Firebase
Hosting origins. Please add the local origin to the README or `.env.example` of the backend.

## 5. The shape the Evaluation page reads from `eval_results/latest` (detail for item 2)

Until `schema.py` has a model, `web/src/eval/evalResults.ts` (`parseEvalResults`) states what the page reads, and
refuses (shows an error, no numbers) any document that breaks it. Please write the evaluation in this shape, or
add a pydantic model with the same fields so `gen-types` can replace the hand-written type:

```
{
  "run_date": "2026-10-18",                                   // ISO date the evaluation ran
  "conditions": [{"id": "full_argus", "label": "Full Argus"}, ...],
  "metrics":    [{"id": "citation_accuracy", "label": "Citation accuracy", "unit": "ratio"}, ...],
                                                              // unit: ratio (0..1, shown as %) | seconds | usd | count
  "results":    [{"metric": "citation_accuracy", "condition": "full_argus",
                  "value": 0.94 | null, "n_label": "16 clause cases, 7 attacks" | null}],
                                                              // a value without n_label is refused
  "corpus":     {"documents": [{"instrument": "...", "instrument_ref": "RBI/..."}],
                 "as_on": "2026-07-15", "paragraphs": 0, "supersession_rows": 0} | null,
  "failure_cases": ["plain-English sentence", ...]
}
```

A metric or condition missing from `results` shows "not measured yet". With no document at all the page shows
the UI_SPEC 3.4 conditions and metrics, every cell "not measured yet", and the corpus as-on date of the newest
saved run.

## 6. Keep the sample policy files in their current shape (Start screen cards)

Resolved in `web/` without a backend change: the web build now reads the sample policy files the API itself loads,
`backend/argus_api/samples/policy_*.json` (`web/src/start/samplePolicies.ts`), so policy A and policy B each get a
Start card and a Run live button whether or not a saved run exists. What the web app needs from those files:
`policy_id` (the id `POST /api/runs` accepts), `title`, `synthetic` (boolean), `disclaimer`, and `clauses` (a
non-empty list). A file that breaks this is shown on the Start screen as an error, not dropped. Please keep these
fields, and keep every file under that name pattern runnable by `POST /api/runs`.

## 7. Rule tests leave a placeholder `eval_results/latest` in the shared emulator

`backend/tests_emulator/test_firebase.py` (line 104) writes `eval_results/latest = {"status": "not measured yet"}`
and leaves it. That is not an evaluation document (no `run_date`, `conditions`, ...), so the Evaluation page
correctly refuses it and shows "eval_results/latest..run_date: expected a non-empty string" instead of the
"not measured yet" table. Seen during the web e2e run on 2026-10-16: the document appeared at 15:46:48 UTC,
after `npm run seed-demo` had deleted it. Please delete it at the end of the test (the e2e setup now reseeds
before each run, which only hides the problem while no backend test runs at the same time). "Not measured yet"
is expressed by having no document, or by a document in the item 5 shape whose results have `value: null`.

## 8. A running local API for the real-API live checks

Status 2026-10-16: nothing listens on `localhost:8080`. Item 1 no longer blocks this (see above). The web side of
the live path is now checked without a backend by `e2e/live-stub.spec.ts`, which stands in for the API with
`page.route` and writes to the emulator with its admin token in the order the API does. The real-API checks
(`e2e/live-spike.spec.ts`, hero moment 5 in `e2e/hero.spec.ts`) still skip with a BLOCKED reason until an API is
reachable. Please document one command that starts the API locally against the emulators
(`FIRESTORE_EMULATOR_HOST=localhost:8081`, `FIREBASE_AUTH_EMULATOR_HOST=localhost:9099`, project `argus-local`,
`ARGUS_ALLOWED_ORIGINS=http://localhost:5173`). `config.Settings.from_env` in local mode requires a real
`aistudio` or `openrouter` key; a fake provider (for example `llm_fake.py`) selectable in local mode would let
these checks run without spending model calls.

## 9. The shared Firestore emulator ran out of Java heap (2026-10-16, about 16:23 UTC)

`/srv/firestore-debug.log` in `argus-firebase-1` shows `java.lang.OutOfMemoryError: Java heap space` (13 times by
16:30 UTC); after that the emulator stopped answering (`curl -m5 localhost:8081/` timed out, `npm run seed-demo`
failed with gRPC UNKNOWN, then "failed to connect"). The web workflow is told not to restart the container, so it
needs a restart from whoever owns it, and probably a larger heap (`JAVA_TOOL_OPTIONS=-Xmx...`) given the
concurrent backend contention tests and web e2e listeners.
