# Budget guard

Switches live reviews and pasted clauses off when the judging-period Gemini spend reaches the budget, so nobody
can drain the project's credit by opening new anonymous sessions. Saved runs keep working (no Gemini call).

- A Cloud Billing budget, **argus-judging-1000**: INR 1,000, project argus-aibc, cost counted before credits,
  18 Oct to 7 Nov 2026, thresholds at 50%, 90% and 100%, notifications to the Pub/Sub topic `argus-budget`.
- A push subscription `argus-budget-guard-push` calls the private Cloud Run service `argus-budget-guard`
  (this folder) with an OIDC token from the service account `argus-budget-push`, the service's only invoker.
- When a notification reports cost at or above the budget, `guard.py` sets `config/limits.enabled` and
  `config/custom_limits.custom_enabled` to false (service account `argus-budget-guard`, role datastore.user).
  It never switches anything on; a person does that with `argus_api.admin limits` / `custom`.
- Billing data reaches budgets a few hours late, so spend can pass the amount by a few hours of use; the API's
  daily and hourly run caps bound that.

Tests: `backend/tests/test_budget_guard.py`. Deploy:

```bash
gcloud run deploy argus-budget-guard --source ops/budget_guard --region asia-south1 --project argus-aibc \
  --service-account argus-budget-guard@argus-aibc.iam.gserviceaccount.com --no-allow-unauthenticated \
  --min-instances 0 --max-instances 1 --memory 256Mi
```
