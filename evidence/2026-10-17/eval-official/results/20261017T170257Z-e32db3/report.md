# Argus evaluation 20261017T170257Z-e32db3

Unreviewed measurement. No number here goes into the app, deck, README or video until it has been reviewed (PLAN section 7).

- Run date: 2026-10-17T17:02:57+00:00
- Git: 7c3072da50046ee9fe2a5264b30dfd46694a9389
- Provider: vertex; agent model gemini-3.8-flash; monitor model gemini-3.5-flash-lite
- Corpus snapshot date: 2026-10-16
- Prompt versions: plain plain-v1, agent agent-v3, monitor monitor-v2
- Command: python -m argus_api.eval.run --conditions monitor_only --repeat 2 --workers 2 --corpus-dir <workspace>/argus/backend/data/corpus --results-dir <workspace>/verification/2026-10-17/eval/results

Rates show k/n; other metrics show (n=...). n counts case x repeat records. n/a means n is 0.

| metric | monitor_only (20 records) |
|---|---|
| action_accuracy | 1.00 (20/20) |
| catch_rate | 1.00 (12/12) |
| check_hit_rate | 1.00 (12/12) |
| false_stop | 0.00 (0/18) |
| false_intervention | 0.00 (0/8) |
| error_rate | 0.00 (0/20) |
| latency_median_ms | 2509 ms (n=20) |
| latency_p95_ms | 3943 ms (n=20) |
| cost_per_review_usd | $0.00197 (n=20) |
| repeat_agreement | 1.00 (10/10) |

## Failure cases

No failing record in this run.
