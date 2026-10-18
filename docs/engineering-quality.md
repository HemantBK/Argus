# Engineering quality

What we do for each part of engineering quality, where to check it, and what is still missing. Code paths are
relative to `backend/argus_api/` unless they start with a top-level folder. The deployed API runs code
`7c3072d` (Cloud Run revision `argus-api-00008-nev`); this repository's `backend/argus_api` is the same.
Evidence files are in [evidence/2026-10-17/](../evidence/2026-10-17/README.md).

| Dimension | What we do | Evidence | Honest gap |
|---|---|---|---|
| Tool use | Gemini 3.8 Flash uses function calling over three read-only library tools (`search_library`, `read_paragraph`, `check_status`), at most 6 tool calls per clause, then one structured draft. A citation is built only from a paragraph in the library. | `agent/loop.py`, `agent/tools.py` | We have not compared against Gemini with the whole library pasted into its prompt. |
| Hard guarantees | Every rule a finding rests on is checked in code: the paragraph exists, the quote matches it word for word, the instrument is in force on the library date. If a check fails, the finding has no citation and goes to a person. | `monitor/checks.py`, `corpus/store.py` (`quote_in_text`), `agent/loop.py` (`finding`, `citation`), `backend/tests/test_citation_property.py`, [mutation check](../evidence/2026-10-17/mutation.md) | "In force" is as of the library's fetch date, not a live check against RBI. |
| Model judgements | A Gemini 3.5 Flash-Lite monitor judges support, staying on task and planted instructions (regex prescan or Gemini; either one fails the check). One revision, then a person. | `monitor/core.py`, [how the monitor works](how-the-monitor-works.md) | These are model judgements and can be wrong. In the official evaluation the monitor helped the cheaper agent (attacks that got through, counting failed reviews, 7/16 to 0/16 on the base set, run `20261017T164720Z-f694fd`) but still let 3/20 hard attacks through (4/20 counting one failed review, run `20261017T165434Z-a52f4e`); on the shipped agent no improvement was shown ([evaluation](evaluation.md)). |
| Guardrails | 90 s budget per run; 30 s per model attempt; every call ends by the run deadline. A kill switch and caps per day, hour and visitor sit in Firestore (`config/limits`) and are counted in the same transaction that creates the run. Request bodies over 64 KiB are refused. | `runs.py`, `llm.py`, `main.py`, `backend/tests_emulator/test_counter_contention.py`, [cap and kill-switch check](../evidence/2026-10-17/cloud/G-FAULTS.json) | Visitors sign in anonymously, so a new browser profile is a new visitor: the per-visitor cap is a convenience limit, and the hourly and daily caps and the kill switch are the hard bound. Pasting your own clause is built (with an identifier prescan) but switched off in production; the evidence folder has no read-back of that production switch. |
| Backup model | When Gemini 3.8 Flash is busy or out of time on a clause, the clause is reviewed again from the start by Gemini 3.1 Flash-Lite with the same monitor and checks, and labelled. A circuit breaker (3 busy give-ups in 60 s) sends clauses straight to the backup for 120 s; `/health` shows its state. | `runs.py`, `breaker.py`, `schema.py` (`Finding.agent_model`, `fallback_reason`), [release checks](../evidence/2026-10-17/release/) | The backup is weaker: 6/8 labels in its one full run (`69eaa7ca`, a deployment check). The monitor model has no backup. |
| Complexity | Per clause: at most 8 agent calls and 2 monitor calls before retries and the backup; clauses run in parallel. | [Complexity and cost](#complexity-and-cost) | The bounds are counted from the code, not measured under load. |
| Reproducibility | Model ids and thinking levels are pinned; cloud mode refuses overrides. `/health` reports the git sha and a hash of the library. Each evaluation result records its git sha, prompt versions, model ids, library hashes and case-file hashes. | `config.py`, `scripts/corpus_hash.py`, [official evaluation results](../evidence/2026-10-17/eval-official/README.md) (`7c3072d`, `"dirty": false` in all 6 runs) | Gemini's outputs are not deterministic. `results.json` does not record the library's aggregate hash or the Vertex AI location; both were checked from the driver and the library folder. The preliminary runs (history) were on a development build with uncommitted changes (`"dirty": true`). |
| Provenance | Each citation carries the RBI URL, paragraph, quote, fetch date, RBI's "updated as on" date and a content hash; each finding names the model that wrote it. | `schema.py` (`Citation`, `Finding`), [data and RBI sources](data-and-rbi-sources.md) | The two saved runs were recorded before the backup existed, so their findings carry no model name; both were written by Gemini 3.8 Flash alone. |
| Interoperability | REST API with Firebase ID tokens; OpenAPI description generated from the code; TypeScript types generated from the Pydantic schema. | [docs/api/](api/README.md), `web/scripts/gen-types.mjs` | No export endpoint and no GRC integration; findings are JSON. |
| Observability | One JSON log line per model call (model, prompt hash, tokens, latency, cost estimate, run id); retries logged as warnings. An uptime check on `/health` with an alert policy, and a budget alert. | `logs.py`, [log check](../evidence/2026-10-17/cloud/G-LOGS.json): in 4 Vertex AI runs every model call in the trace had its log line (agent 34-38, monitor 8-9 per run) and no error entries | No alert on latency or error rate; the breaker state is visible only on `/health`. |
| Separation | Agent, monitor, library and model client import no Firestore or Firebase library; storage goes through one interface (`docstore.py`) with an in-memory fake for tests, which the app code never imports. | `agent/`, `monitor/`, `corpus/`, `llm.py`, `docstore.py`, `store_fake.py` | `runs.py` is one 1,041-line module for the whole run lifecycle. |
| Statistics | Every published evaluation rate carries its n and run id. The official evaluation (17 Oct, Vertex AI, frozen build `7c3072d`, backup off, 2 repeats) was pre-registered with its metrics and rules, and amended twice before any results were read; the hard attack set was pre-registered in commit `5cfa251` before any model ran on it. Failed reviews are never dropped: 19/136 strong-agent reviews in the headline runs (runs `20261017T162059Z-5dc6b4` and `20261017T154026Z-446cd6`) failed on Vertex AI capacity (504 or 499) and count as failures. The results were recomputed three times by this team from the raw records, with no mismatch. Wilson 95% intervals for the official counts are in the results file. | [PREREG.md](../evidence/2026-10-17/eval-official/PREREG.md), [OFFICIAL_RESULTS.md](../evidence/2026-10-17/eval-official/OFFICIAL_RESULTS.md), `scripts/stats_intervals.py` | **Not run exactly as pre-registered:** R1 (run `20261017T162059Z-5dc6b4`, 12/144 error records) was not re-run under the 5% rule; the R2 re-run (run `20261017T155425Z-cb73b9`) was not stopped after three consecutive capacity failures; under the stricter reading the original R2 (run `20261017T154026Z-446cd6`, 7/60) should have been stopped part-way. The stop rules were checked only after each run ended. Small n (8 base attacks, 10 hard). Repeats are not independent, so the intervals are too narrow. The gold labels are ours. The recomputations are by this team, not an independent replication. [intervals.md](../evidence/2026-10-17/intervals.md) covers only the superseded preliminary runs. |
| Instrument checks | An independent script recomputes the deployment checks from the raw Firestore read-backs; a fixed set of 10 drafts (6 bad, 4 good) tests the monitor alone; three planted bugs are each caught by the tests. | [recompute.py](../evidence/2026-10-17/cloud/verifier/recompute.py) (re-run on 17 Oct from this folder: same output), `eval/cases/bad_drafts.json`, [mutation check](../evidence/2026-10-17/mutation.md) | The published recompute covers the deployment checks. The evaluation was recomputed three times from the raw records, but those scripts are not in the repository; the evidence holds every record, so the counts can be recomputed. Three hand-picked mutations, not a mutation-testing tool. |
| Tests | Offline pytest (882 pass and 3 skipped at commit `7a4a5d1`, with the library built), emulator tests for rules and concurrent caps, vitest (340 pass at `7a4a5d1`), Playwright end-to-end, and a deterministic checker (`scripts/check.py`) run by a pre-commit hook. | `backend/tests/`, `backend/tests_emulator/`, `web/src/**/*.test.ts*`, `web/e2e/`, [CI dry run](../evidence/2026-10-17/ci-dry-run.md) | **No CI.** A dry run on a clean checkout failed: 20 backend tests need the RBI library, which is not committed and is built by fetching rbi.org.in; and on Node 22.12 seven web test files cannot start. |
| Release | A new revision is deployed with no traffic, checked with a live review, and promoted only by a person; then the rules are checked with real tokens. A deployment checker verifies the live stack against the repository. | `scripts/release.sh`, [deploy check](../evidence/2026-10-17/deploy-check/7c3072d-deploy-check.jsonl) (second pass: 15 pass, 1 left to a person) | The first pass on 17 Oct failed one check (traffic pinned to the tagged revision); the check was changed to accept that, then passed. |
| Dependencies | Exact versions in `requirements*.txt` and `package.json`, with a lockfile. | `backend/requirements.txt`, `web/package-lock.json` | No `pip-audit` or `npm audit` run is recorded. `web/package.json` asks for Node `>=22.12.0`, lower than its dependencies need. |
| Cost | About $0.10 of Gemini per 8-clause review, an estimate from measured tokens and list prices. | `llm.py` (`PRICES_USD_PER_MTOK`), [smoke and hero runs](../evidence/2026-10-17/cloud/G-HERO.json) | List prices, no invoice. The backup model is not priced, so its runs show no cost. |
| Security | Synthetic data only; anonymous sign-in; the browser only reads (its own runs, and the public saved runs and evaluation results); the web API key is restricted; no secrets in the repository. | [security](security.md), `firestore.rules`, [rules check](../evidence/2026-10-17/release/PROMOTED-RULES.json) | Gemini is called with location `global`, so requests may be served outside India. |
| Accessibility | Status colours are tested for contrast (at least 4.5:1). | `web/src/styles/contrast.test.ts` | No screen-reader or keyboard audit is recorded. |
| Docs and honesty | README, the pages in `docs/`, this page; misses and negative results are published (the backup's 6/8 in deployment check `69eaa7ca`; no improvement shown from the monitor on the shipped agent; the 3/20 hard attacks the monitor let through with the cheaper agent, 4/20 counting a failed review, run `20261017T165434Z-a52f4e`; the protocol deviations of the official evaluation). | [README](../README.md), [evaluation](evaluation.md) | The labels and the evaluation set are this project's own. |

## Complexity and cost

Read from `agent/loop.py`, `agent/tools.py`, `corpus/store.py`, `llm.py` and `runs.py` at the deployed code.

### Model calls per clause

Logical calls, counted from the code:

| Step | Model | Calls |
|---|---|---|
| Research turns (chat with tools; each turn may make several tool calls, 6 tool calls in all at most) | agent | 1 to 6 |
| Draft (structured output) | agent | 1 |
| Revision, only if the monitor says revise | agent | 0 or 1 |
| Monitor on each draft (`loop.py` checks there is exactly one call) | monitor | 1 or 2 |
| Check on each paragraph read | none (code only) | 0 |

So a clause makes **at most 8 agent calls and 2 monitor calls (10)**. This is an estimate before retries and the
backup; the real number of requests can be higher:

- **JSON re-ask:** a structured reply (draft, revision, monitor) that is not valid JSON for its schema is asked
  once more (`llm.JSON_ATTEMPTS = 2`): at most 6 + 2 x 4 = 14 requests.
- **Retries:** each request is tried up to 5 times on HTTP 429 and up to 2 times on a 5xx or a timeout
  (`THROTTLED_ATTEMPTS`, `MAX_ATTEMPTS`): at most 70 HTTP attempts.
- **Hedges:** an attempt with no reply after 8 s (a chat turn) or 10 s (a draft or monitor call) sends the same
  request once more, inside the attempt's own timeout, and the first good reply wins (`CHAT_HEDGE_AFTER_S`,
  `REQUEST_HEDGE_AFTER_S`): at most 2 HTTP requests per attempt, so 140, and never more than 8 hedged attempts in
  flight in the process at once (`HEDGE_MAX_IN_FLIGHT`), each counted until both its copies have ended, the
  ignored loser included, so hedging adds at most 8 open requests to the provider. A run's tokens and cost count
  only the winning reply; the abandoned copy may still be billed, and each hedge is logged (`llm_hedge`), so the
  bill can exceed the recorded cost by the hedges' share.
- **Backup:** if the agent model gives up busy or out of time and at least 30 s of the run are left, the clause
  is reviewed once more from the start on the backup model: the bounds double, to 20 logical calls, 28 requests,
  140 attempts and 280 HTTP requests.

The deadlines stop far short of these bounds: the agent model gets 35 s per clause until its first draft
(`runs.PRIMARY_PHASE_S`), no retry starts without 2 s left, and the whole run ends at 90 s. In four calm Vertex
AI runs of 8 clauses on 17 Oct (runs `d38710fb`, `d67c3dfe`, `f30914e6`, `c0de9595`) the agent made 34-38
calls per run and the monitor 8-9 ([G-LOGS.json](../evidence/2026-10-17/cloud/G-LOGS.json)).

### Library search

`search_library` is BM25 over all N paragraphs (N = 365 today), with no embeddings. The index (term counts and
document frequencies) is built once per library, in O(total words). Each search scans every paragraph, so it is
**O(N x query terms)**, plus sorting the paragraphs that score above zero, and returns the top 8; there is no
inverted index. That is fine for 365 paragraphs and grows linearly with the library. `read_paragraph` and
`check_status` are dictionary lookups, O(1). The quote check is a substring search over one paragraph.

### Time

All clauses of a run are reviewed at once (10 workers for at most 10 clauses), so **a review takes as long as its
slowest clause, capped at 90 s** (`runs.RUN_BUDGET_S`). A clause still running at 90 s is saved as failed and
sent to a person. Deployment checks on 17 Oct: 20-25 s in five calm runs; 41.7 s and 49.0 s in two runs while
Gemini 3.8 Flash was overloaded and some or all clauses went to the backup (runs listed in
[evaluation](evaluation.md)).

### Cost

Costs are estimates from measured tokens and **list prices, not an invoice**. The price table in `llm.py` holds
Gemini 3.8 Flash at $0.75 / $3.75 and Gemini 3.5 Flash-Lite at $0.30 / $2.50 per million input / output tokens.
These are the list prices on the global endpoint shown on Google's Agent Platform (Vertex AI) pricing page when
we read it on 17 Oct 2026; the Gemini 3.8 Flash price is an introductory price until 31 Dec 2026. The five calm
Vertex AI runs on 17 Oct cost an estimated $0.100 to $0.106 each ([G-SMOKE.json](../evidence/2026-10-17/cloud/G-SMOKE.json), [G-HERO.json](../evidence/2026-10-17/cloud/G-HERO.json)).
**The backup model is not priced**, so a run with a backup clause shows no cost estimate, and a run on the backup
is not counted in these figures.
