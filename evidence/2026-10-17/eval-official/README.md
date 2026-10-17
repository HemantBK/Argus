# Official evaluation, 17 October 2026

The records behind every evaluation number in the README, [docs/evaluation.md](../../../docs/evaluation.md) and
the deck. Google Cloud's Agent Platform (Vertex AI), location `global`; frozen build `7c3072d` (the build the app
runs), backup model switched off; every case run twice.

Copied from our working verification folder. Private paths are shown as `<workspace>`. In `OFFICIAL_RESULTS.md`, three
references to our internal review notes (the headings of sections 9 and 10 and the first sentence of section 10)
were reworded; no number was changed, and nothing else was changed.
The `report.md` files were written by the harness when each run ended, before any review, so they still carry
its "Unreviewed measurement" line, and their attack-success and false-stop rows use the harness reading (a
failed review counts as flagged). The headline in `OFFICIAL_RESULTS.md` and the docs uses the stricter reading,
which counts a failed review as an attack that got through or as a false stop; both are shown there.

| File | What it is |
|---|---|
| [PREREG.md](PREREG.md) | The pre-registration: matrix, metrics and rules, written before any full run, with Amendments 1 and 2 (both before any results were read). |
| [OFFICIAL_RESULTS.md](OFFICIAL_RESULTS.md) | The results recomputed from the raw records, with the protocol rules applied, every error record, Wilson 95% intervals (too narrow, because a case's two repeats are not independent trials) and the approved claim wording. Recomputed three times by this team; not an independent replication. |
| [drive_official.py](drive_official.py) | The driver that ran the stages in the pre-registered order, with the start check (breaker state and a burst of 8 calls) and the 10% capacity re-run rule. |
| [logs/driver.log](logs/driver.log), [logs/stages.jsonl](logs/stages.jsonl) | What the driver did and when (IST), and one line per stage with its run id and capacity-failure count. |
| `results/<run>/results.json`, `report.md` | Every record of each run: git sha, models, thinking levels, prompt versions, library and case-file hashes, metrics with k and n. |

| Run | Run id | What it is |
|---|---|---|
| R2 (headline) | [20261017T154026Z-446cd6](results/20261017T154026Z-446cd6/report.md) | Gemini 3.8 Flash agent, 10 pre-registered hard attacks; 7/60 capacity failures |
| R2 re-run (reported, not headline) | [20261017T155425Z-cb73b9](results/20261017T155425Z-cb73b9/report.md) | The same, one worker; 10/60 capacity failures |
| R1 | [20261017T162059Z-5dc6b4](results/20261017T162059Z-5dc6b4/report.md) | Gemini 3.8 Flash agent, 16 clauses + 8 attacks; 12/144 capacity failures |
| R3 | [20261017T164720Z-f694fd](results/20261017T164720Z-f694fd/report.md) | Cheaper agent (Gemini 3.1 Flash-Lite), base set |
| R4 | [20261017T165434Z-a52f4e](results/20261017T165434Z-a52f4e/report.md) | Cheaper agent, hard attacks |
| R5 | [20261017T170257Z-e32db3](results/20261017T170257Z-e32db3/report.md) | The monitor alone on 10 fixed drafts |

The evaluation was recomputed three times by this team but was not run exactly as pre-registered. First, R1 (run
20261017T162059Z-5dc6b4, 12/144 error records) was not re-run under the 5% rule. Second, the R2 re-run (run
20261017T155425Z-cb73b9, 10/60 capacity failures) was not stopped after three consecutive capacity failures.
Third, under the stricter reading, the original R2 (run 20261017T154026Z-446cd6, 7/60) should have been stopped
part-way once capacity failures passed 10%. The stop rules were checked only after each run ended. No record was
dropped. The original R2 is the headline because the re-run did not get below 5% errors, and both are reported.

`results.json` does not record the library's aggregate hash or the Vertex AI location; the driver sets the
location, and the library folder it used hashes to the value in PREREG. The cheaper agent's runs have no cost
estimate, because the code holds no price for Gemini 3.1 Flash-Lite. The official results were not published to
the app's Evaluation page.
