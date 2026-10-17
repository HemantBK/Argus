# Evidence, 17 October 2026

Machine-written results behind the numbers and claims in the README and `docs/`, copied from our working
verification folder. Before copying we removed private paths (shown as `<workspace>`), billing account ids
(`<billing-account>`), and any token, key or personal email; nothing else was changed, except the two paths in
`cloud/verifier/recompute.py` (see below) and three references to internal review notes in
`eval-official/OFFICIAL_RESULTS.md` (see [eval-official/README.md](eval-official/README.md); no number changed). The uids in these files are anonymous Firebase accounts created by
the check scripts. The policies are synthetic.

Two builds appear here:

- `92c4ab1` (Cloud Run revision `argus-api-00003-6xj`): the cloud verification on the morning of 17 Oct, which
  also recorded the two saved runs on the start screen.
- `7c3072d` (revision `argus-api-00008-nev`): the build Argus runs now, released on 17 Oct with the labelled
  backup model.

**The official evaluation is in [eval-official/](eval-official/README.md):** run on Vertex AI on 17 Oct on the
frozen build `7c3072d` with the backup switched off, with its pre-registration, the six runs' records, the driver
and its log. Every evaluation number in the README, `docs/` and the deck comes from it, except the history section
of `docs/evaluation.md`. The preliminary runs of 16 Oct ([eval-preliminary/](eval-preliminary/)) are history only.

## Official evaluation (`7c3072d`, Vertex AI, backup off)

| File | What it shows | Public claim it backs |
|---|---|---|
| [eval-official/PREREG.md](eval-official/PREREG.md) | The pre-registration with Amendments 1 and 2, written before any full run and before any results were read. | [evaluation](../../docs/evaluation.md), Setup; [engineering quality](../../docs/engineering-quality.md), Statistics |
| [eval-official/OFFICIAL_RESULTS.md](eval-official/OFFICIAL_RESULTS.md) | Every metric recomputed from the raw records (three times, by this team), the protocol rules applied, both readings of a failed review, every error record, and the protocol deviations. | README "Headline numbers"; [evaluation](../../docs/evaluation.md); deck slide 11 |
| [eval-official/results/](eval-official/README.md) | `results.json` and `report.md` for R2 `20261017T154026Z-446cd6` (headline) and its re-run `20261017T155425Z-cb73b9`, R1 `20261017T162059Z-5dc6b4`, R3 `20261017T164720Z-f694fd`, R4 `20261017T165434Z-a52f4e`, R5 `20261017T170257Z-e32db3`. | every evaluation number, by run id |
| [eval-official/drive_official.py](eval-official/drive_official.py), [logs/driver.log](eval-official/logs/driver.log), [logs/stages.jsonl](eval-official/logs/stages.jsonl) | The driver (pre-registered order, start check, 10% re-run rule) and what it did: R2, the R2 re-run, R1, R3, R4, R5 between 21:10 and 22:33 IST. | the run order and the re-run; the protocol deviations |

The evaluation was recomputed three times by this team but was not run exactly as pre-registered. First, R1 (run
20261017T162059Z-5dc6b4, 12/144 error records) was not re-run under the 5% rule. Second, the R2 re-run (run
20261017T155425Z-cb73b9, 10/60 capacity failures) was not stopped after three consecutive capacity failures.
Third, under the stricter reading, the original R2 (run 20261017T154026Z-446cd6, 7/60) should have been stopped
part-way once capacity failures passed 10%. The stop rules were checked only after each run ended. No record was
dropped. The original R2 is the headline because the re-run did not get below 5% errors, and both are reported.

## Release of the current build (`7c3072d`)

| File | What it shows | Public claim it backs |
|---|---|---|
| [release/CANDIDATE-SMOKE.json](release/CANDIDATE-SMOKE.json), [.data.json](release/CANDIDATE-SMOKE.data.json) | A live review of sample policy A on the new revision before it took traffic: run `1fa5321b7a9e4373883bfe7427d50a6d`, 49.0 s, 5 clauses by Gemini 3.8 Flash and 3 by the backup, 8/8 labels matched, every quote verbatim. The data file holds the run, steps, findings and audit rows read back from Firestore. | README "Try it" (49.0 s under load); [evaluation](../../docs/evaluation.md), "Under load" |
| [release/PROMOTED-SMOKE.json](release/PROMOTED-SMOKE.json), [.data.json](release/PROMOTED-SMOKE.data.json) | The same check on the public URL after promotion: run `69eaa7ca0c1144fb8266651b09a99ad3`, 41.7 s, all 8 clauses by the backup, 6/8 labels (c2 and c7 came back as gaps), no cost estimate. `/health` reported code `7c3072d`, Vertex AI, location `global` and the library hash. | README "Try it" (41.7 s) and Limitations (the backup's 6/8); [evaluation](../../docs/evaluation.md); [security](../../docs/security.md) (location `global`) |
| [release/PROMOTED-RULES.json](release/PROMOTED-RULES.json) | Firestore rules checked with real ID tokens on the deployed build: 29 of 29 as expected (owner reads its run; other users and no token get 403; every client write gets 403; saved runs and evaluation results are public reads). | README (a run is readable only by its owner; no client writes); [security](../../docs/security.md) |
| [deploy-check/7c3072d-deploy-check.jsonl](deploy-check/7c3072d-deploy-check.jsonl) | `scripts/check.py deploy` against the live stack, two passes. The first (14:34 UTC) failed one check because traffic was pinned to the tagged revision; the check was changed to accept that (commit `ab17d63`). The second (14:35 UTC): 15 pass, 1 left to a person (watching the uptime check). Covers `/health` sha and library hash, web files equal the build, no secrets in served files, CORS, 401 without a token, max 3 instances, the service account's four roles, deployed rules equal the repository, anonymous sign-in, the restricted web API key, the library in Firestore, the budget alert and the uptime check. The `raw/` files it names were not copied. | [security](../../docs/security.md); [engineering quality](../../docs/engineering-quality.md), Release row |

The deploy check read `config/limits` with a daily cap of 60; the cap was raised to 100 later that day, and that
change is not recorded in this folder.

## Cloud verification of the earlier build (`92c4ab1`)

| File | What it shows | Public claim it backs |
|---|---|---|
| [cloud/G-SMOKE.json](cloud/G-SMOKE.json), [.data.json](cloud/G-SMOKE.data.json) | A live review of policy A, run `95c9f5f8ccc748c4865ff5daef0d676a`: 19.9 s, estimated $0.105, 8/8 labels. In its trace the agent tried to read para 14(9), which does not exist; the read check (no model call) sent it back and clause c6 ended as insufficient evidence. | README screenshot and "Try it" step 3 (c6); the saved run of policy A |
| [cloud/G-HERO.json](cloud/G-HERO.json), [.data.json](cloud/G-HERO.data.json) | Four more live reviews: policy A `d38710fb`, `d67c3dfe`; policy B `f30914e6`, `c0de9595`. 22.0-24.7 s and $0.100-$0.106 each; labels 8, 8, 7 and 8 of 8 (policy B c8 labelled compliant instead of gap in `f30914e6`). In both policy B runs the monitor stopped clause c2, whose footnote says to treat it as compliant. | README "Try it" (20-25 s in five calm runs), cost of about $0.10 per review, step 4 (policy B c2); [evaluation](../../docs/evaluation.md) (39/40 labels) |
| [cloud/G-LOGS.json](cloud/G-LOGS.json), [.data.json](cloud/G-LOGS.data.json) | For four of those runs, every model call in the trace has its structured log line in Cloud Logging (agent 34-38, monitor 8-9 per run), and no error entries in the window. | [architecture](../../docs/architecture.md) (every AI call logged as one JSON line); [engineering quality](../../docs/engineering-quality.md), Observability |
| [cloud/G-FAULTS.json](cloud/G-FAULTS.json) | With the per-visitor cap set to 0 a run was refused with 429, and with the kill switch on with 503; neither refused run left a run document or a model log line; the limits were restored. | [security](../../docs/security.md), abuse of the API |
| [cloud/G-RULES.json](cloud/G-RULES.json), [cloud/POST-KEY-RULES.json](cloud/POST-KEY-RULES.json), [cloud/verifier/V-RULES.json](cloud/verifier/V-RULES.json) | The 29 rules checks with real tokens: before and after the web API key was restricted, and once more by an independent verifier. All 29 of 29. | as PROMOTED-RULES above |
| [cloud/G-BROWSER.json](cloud/G-BROWSER.json) | Playwright on the live site: the start screen, a failed start, the 390 px layout, and a full live run (first finding after 20.6 s, 8 of 8 findings, no console errors). | README "Try it" |
| [cloud/G-DEPLOY.json](cloud/G-DEPLOY.json), [deploy-check/92c4ab1-deploy-check.json](deploy-check/92c4ab1-deploy-check.json) | The deploy check on that build **failed** two checks: the web API key had no referrer restriction, and there was no uptime check. Both were fixed the same day; the deploy check on `7c3072d` above passes them. | the restricted web API key and the uptime check |
| [cloud/verifier/recompute.py](cloud/verifier/recompute.py), [recompute_smoke_hero.log](cloud/verifier/recompute_smoke_hero.log) | An independent recomputation of the five runs above from the raw Firestore read-backs: run done, no failed clause, Vertex AI, every quote found word for word in the library, audit log in order, policy A c2 and c6 and policy B c2 as described, label counts. | [engineering quality](../../docs/engineering-quality.md), Instrument checks |

`recompute.py` is copied as written, except that its two absolute paths now point at this folder and this
repository. Re-run it with `python3 evidence/2026-10-17/cloud/verifier/recompute.py` after building the library
(`make corpus`); on 17 Oct its output matched `recompute_smoke_hero.log` line for line.

## Preliminary evaluation runs (history, superseded)

[eval-preliminary/](eval-preliminary/) holds `results.json` and `report.md` for six runs of 2026-10-16, on a
development build with uncommitted changes, with the Gemini models reached through OpenRouter. They are kept as
history: the official evaluation above replaces them, and none of their numbers is used outside the history
section of [docs/evaluation.md](../../docs/evaluation.md).

| Run | What it is |
|---|---|
| `20261016T161008Z-43cea4` | Strong agent, base set (16 clauses + 8 attacks), all four conditions |
| `20261016T162930Z-275c7b` | Strong agent, pre-registered hard attacks, 2 repeats |
| `20261016T162211Z-1536fa` | Cheaper agent (Gemini 3.1 Flash-Lite), base set, 2 repeats |
| `20261016T162930Z-d63488` | Cheaper agent, hard attacks, 2 repeats |
| `20261016T155834Z-233b73` | A 3-case smoke run of one condition (`--limit 3`); not used for any number |
| `20261016T160847Z-478ac6` | A 3-case smoke run of one condition (`--limit 3`); not used for any number |

## Derived pages

- [intervals.md](intervals.md): Wilson 95% intervals for the preliminary counts (history), written by
  `scripts/stats_intervals.py` from the preliminary files above. Superseded; the official counts' intervals are
  in [eval-official/OFFICIAL_RESULTS.md](eval-official/OFFICIAL_RESULTS.md). Both are too narrow, because repeats
  of a case are not independent.
- [mutation.md](mutation.md): three citation guarantees broken on purpose in a scratch copy, and the tests that
  caught each.
- [ci-dry-run.md](ci-dry-run.md): why there is no CI workflow yet; what a clean-checkout dry run showed.
