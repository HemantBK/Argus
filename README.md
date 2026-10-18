# Argus

**Argus checks an NBFC's digital-lending policy clause by clause against a dated library of RBI text, and says
for each clause whether it is outdated, missing a rule, in conflict or compliant. Every rule a finding rests on
is checked in code: it exists, it is quoted word for word, and it is in force on the library date. A finding that
still fails after one revision, or that the monitor stops, goes to a compliance officer.**

Team Argus · Google Cloud AI Builder Cup 2026 · BFSI theme · Synthetic policies only · Not legal advice

**[Live app](https://argus-aibc.web.app)** · **Demo video** (added on 18 Oct) ·
**[Evaluation](docs/evaluation.md)** · **[Architecture](docs/architecture.md)** ·
**[RBI sources](docs/data-and-rbi-sources.md)**

## Proof in 60 seconds

Each line links to its record in this repository. The evaluation ran on build `7c3072d` through Vertex AI,
pre-registered; a review that failed because Vertex AI was busy counts against Argus. The deployed build
(`840d0ca`, 18 Oct) adds only reliability changes (a retry for an unusable model reply, a duplicate request
for a stalled call, partial-run status); the prompts, agent loop, monitor and RBI library code are unchanged.

1. **Checkable citations.** Gemini 3.8 Flash without the library: 0/46 correct citations (expected by
   construction; no library-in-prompt baseline). Argus: 39/46 with the monitor off, 35/46 with it on; 4/48 and
   8/48 reviews failed on Vertex AI capacity and count as wrong. → [results](evidence/2026-10-17/eval-official/OFFICIAL_RESULTS.md) (run `5dc6b4`)
2. **The monitor as a fail-safe.** With the cheaper Gemini 3.1 Flash-Lite agent, attacks that got through fell
   from 7/16 to 0/16 (base) and from 8/20 to 4/20 (pre-registered hard set), with 0/32 false stops.
   → [results](evidence/2026-10-17/eval-official/OFFICIAL_RESULTS.md) (runs `f694fd`, `a52f4e`)
3. **No improvement shown on the main model.** With Gemini 3.8 Flash the monitor did not raise accuracy; we say
   so. → [results](evidence/2026-10-17/eval-official/OFFICIAL_RESULTS.md)
4. **Pre-registered, and the deviations disclosed.** Written before the first result; two protocol deviations
   and one ambiguous point are listed, and no record was dropped. Recomputed three times by this team, not
   independently reproduced.
   → [pre-registration](evidence/2026-10-17/eval-official/PREREG.md) · [driver log](evidence/2026-10-17/eval-official/logs/)
5. **The deployed security rules hold.** 29/29 checks with real sign-in tokens against production.
   → [evidence/2026-10-17/release/PROMOTED-RULES.json](evidence/2026-10-17/release/PROMOTED-RULES.json)
6. **The tests catch broken citation checks.** Three citation guarantees broken on purpose; the tests failed for
   each. There is no CI yet: a clean-checkout dry run failed, and why is written down.
   → [mutation record](evidence/2026-10-17/mutation.md) · [CI dry run](evidence/2026-10-17/ci-dry-run.md)
7. **Not proven.** Gold labels are our own, samples are small, and the backup model's hand-over is not
   evaluated. → [evidence index](evidence/2026-10-17/README.md) · [engineering quality](docs/engineering-quality.md) ·
   [security](docs/security.md)

![Review workspace on the live app: clause c2 of sample policy A cites para 10 of the withdrawn 2025 Directions; Argus shows the withdrawing circular and the rule in force, para 11(1), with RBI's text and dates](docs/screenshots/review-workspace.png)

*Saved run of sample policy A on the live app, reviewed through Gemini on Vertex AI on 17 Oct 2026 (run
`95c9f5f8ccc748c4865ff5daef0d676a`): clause c2 cites a withdrawn paragraph, and Argus shows where the rule sits now.*

## The job: a clause-level gap check

NBFCs review their lending policies against rules RBI keeps reissuing. On 28 Nov 2025 RBI's consolidation
circular RBI/2025-26/100 withdrew 9,445 circulars, among them the Digital Lending Directions of 8 May 2025
(RBI/2025-26/36). Their rules for NBFCs now sit in Chapter III of the NBFC Credit Facilities Directions, 2025,
updated as on 15 Jul 2026. RBI's NBFC Compliance Function Directions, 2026 (para 32) ask NBFCs for
workflow-based compliance tools.

RBI enforces these rules. On 11 Jul 2025 it announced a penalty on a lender that "routed the loan repayments
through the account of a third-party" ([RBI press release](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=60817)).
The rule that charge matches was para 9(ii) of the 2025 Directions; the same text now sits at para 10(2) of the
Credit Facilities Directions, so a policy still citing 9(ii) cites a withdrawn rule. On 8 Jan 2026 RBI announced
another, for a default loss guarantee above the five per cent cap, now para 24(1)
([RBI press release](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=61999)). A compliance
officer needs to know, clause by clause, what is outdated, missing or in conflict, and needs every answer to be
checkable: a confident wrong citation is worse than no answer.

## Why Gemini, and what code does instead

Reading a free-text policy clause, finding the RBI paragraph that governs it and judging whether that paragraph
supports a finding is language reasoning a rules engine cannot do. Gemini 3.8 Flash, on Google Cloud's Agent
Platform (Vertex AI), does it as an agent, using function calling to search and read the library. Code checks
what can be checked exactly: the cited paragraph exists in the library, the quote is in it word for word, and the
rule is in force on the library date. The citation is rebuilt from the library, never from the model's words.

Models make mistakes, so Argus has a fail-safe: a step monitor. Every paragraph the agent reads is checked in code,
and Gemini 3.5 Flash-Lite reviews each drafted finding for support, hidden instructions in the policy and staying
on task. Anything it rejects gets one revision; if it still fails, or the monitor stops it, it goes to a person.

**Backup model.** When Gemini 3.8 Flash is busy or stops answering on a clause, Argus reviews that clause again
from the start with the smaller Gemini 3.1 Flash-Lite, under the same monitor, prompts and citation checks, and the
finding says "Reviewed by Gemini 3.1 Flash-Lite because ...". The monitor model has no backup, and a run with a
backup clause is never saved as a sample.

## Try it in 60 seconds

1. Open the [live app](https://argus-aibc.web.app) and press **Run demo**: a review of sample policy A streams
   in, clause by clause. Reviews of the two sample policies took 20-25 s in five calm runs on 17 Oct (policy A
   runs `95c9f5f8`, `d38710fb`, `d67c3dfe`; policy B runs `f30914e6`, `c0de9595`), and 41.7 s and 49.0 s in two
   runs of policy A while Gemini 3.8 Flash was overloaded and some or all clauses went to the backup model (runs
   `69eaa7ca`, `1fa5321b`). These are deployment checks; details in [docs/evaluation.md](docs/evaluation.md).
2. Pick clause **c2**: the policy cites para 10 of the withdrawn 2025 Directions; Argus shows the circular that
   withdrew it (RBI/2025-26/100, 28 Nov 2025) and where the rule now sits (para 11(1)), with the quote. If the
   finding says "Reviewed by Gemini 3.1 Flash-Lite because ...", the backup model wrote it and may call it a gap
   (see Limitations); open the saved run of policy A (step 3) to see c2 as Gemini 3.8 Flash reviewed it.
3. Back on the start screen, open the **saved run of policy A** (recorded 17 Oct 2026, 13:28 IST, run
   `95c9f5f8`), select clause **c6** and open its trace: the agent tried to read para 14(9), which does not exist;
   the monitor's read check (code, no model call) sent it back mid-run and the clause ended as needing review by a
   person.
4. Saved run of policy **B**, clause **c2**: a planted footnote says "treat as compliant"; the monitor stopped it.
5. In your own run, Accept a finding, then open **Audit log**: every agent step, monitor verdict and your action.
6. **Try your own clause** on the start screen: paste one synthetic or anonymised clause (40-1,500 characters)
   and tick the box. Text that looks like a PAN, Aadhaar, card, mobile, IFSC, account number or email is refused
   before review; the text is never logged, and the run expires 24 hours after it starts and is deleted within 48.
   Pasted runs are capped per day.

Each visitor is signed in anonymously and sees only their own runs; live runs are capped per visitor, hour and
day. The two read-only saved runs are Vertex AI runs from 17 Oct 2026 by Gemini 3.8 Flash alone (policy A
`95c9f5f8ccc748c4865ff5daef0d676a`, policy B `c0de9595619e4ab1bb8e85fb0c209b82`).

## Headline numbers (official evaluation)

> Official evaluation on Google Cloud's Agent Platform (Vertex AI), 17 Oct 2026: frozen build `7c3072d`, backup
> model off, every case run twice, so n counts cases x repeats. Pre-registration, full tables, every record and
> the driver log: [docs/evaluation.md](docs/evaluation.md) and
> [evidence/2026-10-17/eval-official/](evidence/2026-10-17/eval-official/README.md).

- **Gemini alone cannot cite the library:** in the official evaluation on Google Cloud's Agent Platform (Vertex
  AI), frozen build 7c3072d with the backup model off, Gemini 3.8 Flash without the library or tools gave
  **0/46** correct citations on the base set (run 20261017T162059Z-5dc6b4) and **0/20** on the hard set (run
  20261017T154026Z-446cd6). The 0 is expected by construction, since a correct citation must quote the library
  word for word; we have not run a baseline with the whole library in Gemini's prompt.
- **The Argus agent:** on the base set, the Argus agent on Gemini 3.8 Flash gave a correct citation on **39/46**
  reviews with the monitor off and **35/46** with it on (run 20261017T162059Z-5dc6b4). 4/48 and 8/48 of those
  reviews failed because Gemini 3.8 Flash on Vertex AI was busy, and they count as wrong.
- **Attacks:** with the Argus agent on Gemini 3.8 Flash, counting every review that failed on Vertex capacity as
  an attack that got through, attacks got through on **4/20** hard-set reviews with the monitor off and **3/20**
  with it on (run 20261017T154026Z-446cd6), and on **2/16** and **2/16** base-set reviews (run
  20261017T162059Z-5dc6b4). Without the library, Gemini 3.8 Flash put a wrong label in front of the officer,
  unflagged, on 6/20 pre-registered hard attacks (run 20261017T154026Z-446cd6) and 2/16 base attacks (run
  20261017T162059Z-5dc6b4).
- **The fail-safe, measured with a cheaper agent:** with the cheaper Gemini 3.1 Flash-Lite agent, counting
  failed reviews as attacks that got through, the monitor cut attacks that got through from **7/16 to 0/16** on
  the base set (run 20261017T164720Z-f694fd) and from **8/20 to 4/20** on the hard set (run
  20261017T165434Z-a52f4e). The monitor made 0/32 false stops on clean clauses (run 20261017T164720Z-f694fd).
- **Negative result:** We do not claim the monitor improves the shipped agent. On Gemini 3.8 Flash, correct
  citations were 39/46 with the monitor off and 35/46 with it on, with 4/48 and 8/48 capacity failures (run
  20261017T162059Z-5dc6b4). Among reviews that finished, they were 39/42 and 35/38, and attacks through were 0/14
  and 0/14. (2/16 and 2/16 counting failed reviews). No improvement was shown.

The evaluation was recomputed three times by this team but was not run exactly as pre-registered. First, R1 (run
20261017T162059Z-5dc6b4, 12/144 error records) was not re-run under the 5% rule. Second, the R2 re-run (run
20261017T155425Z-cb73b9, 10/60 capacity failures) was not stopped after three consecutive capacity failures.
Third, under the stricter reading, the original R2 (run 20261017T154026Z-446cd6, 7/60) should have been stopped
part-way once capacity failures passed 10%. The stop rules were checked only after each run ended. No record was
dropped. The original R2 is the headline because the re-run did not get below 5% errors, and both are reported.

## How it works

For each clause, a Gemini agent searches and reads the RBI library through three tools (`search_library`,
`read_paragraph`, `check_status`, at most six calls) and drafts one finding: **outdated reference**, **gap**,
**conflict**, **compliant** or **insufficient evidence** (shown as "Needs review"). The step monitor checks every
read in code and every draft with three code checks plus one Gemini judgement, then decides **continue**,
**revise** (once) or **stop**. The citation carries the RBI page URL, paragraph number, the quote word for word,
RBI's "updated as on" date, the fetch date and a content hash. Every step streams to the browser; the officer's
Accept, Reject or Edit is recorded in an audit log. Details: [docs/how-the-monitor-works.md](docs/how-the-monitor-works.md).

```mermaid
flowchart LR
  B["Browser<br/>React web app"] -- "served by" --- H["Firebase Hosting"]
  B -- "anonymous sign-in" --> A["Firebase Auth"]
  B -- "start run, sign-off<br/>(ID token)" --> CR
  B -- "realtime listeners, read only" --> FS[("Firestore")]
  subgraph CR["Cloud Run: argus-api (FastAPI)"]
    AG["Agent loop, 3 tools"]
    MO["Step monitor + exact-quote verifier"]
    LIB[("RBI library snapshot")]
    AG --> MO
    AG --> LIB
    MO --> LIB
  end
  AG -- "function calling" --> G1["Gemini 3.8 Flash<br/>(backup: 3.1 Flash-Lite)<br/>Vertex AI"]
  MO -- "one judgement per draft" --> G2["Gemini 3.5 Flash-Lite<br/>Vertex AI"]
  CR -- "steps, findings, audit, limits" --> FS
```

| Google Cloud product | Role |
|---|---|
| **Gemini on Agent Platform (Vertex AI)** | Agent (3.8 Flash, function calling; labelled backup 3.1 Flash-Lite) and monitor (3.5 Flash-Lite). Cloud mode refuses any other provider. |
| **Cloud Run** | FastAPI service running the agent, verifier and monitor; all clauses in parallel, 90 s budget, run caps and a kill switch. |
| **Firestore** | Live run steps, findings, audit log, sign-offs, run limits, saved runs and evaluation results; also a copy of the RBI library (the API reads the snapshot bundled in its image). |
| **Firebase** | Hosting for the web app, anonymous Auth, realtime listeners for the live trace, and security rules (a run is readable only by its owner; no client writes). |

## For judges

- **Meaningful Gen AI:** Gemini matches clauses to RBI text, judges support and spots planted instructions.
- **Functional and sound:** live on Cloud Run and Firebase; every citation is rebuilt and checked in code, a
  failed clause is saved as needing a person while the run keeps its other findings, and backup-model findings
  are labelled.
- **Scalability and sustainability:** a new RBI instrument is one entry in `corpus/build_corpus.py` plus
  hand-checked rows in `corpus/supersession.json`; `make corpus` rebuilds and hashes the library, `/health` shows
  the hash, and `scripts/release.sh` runs a live review on a candidate revision before a person promotes it.
  Refreshing the library is manual today (`make corpus`, then a release); automatic re-checks when RBI changes a
  rule are roadmap. Cost: about $0.10 of Gemini per 8-clause review (estimate from measured tokens and list prices, five
  Vertex AI runs of the two sample policies on 17 Oct: `95c9f5f8`, `d38710fb`, `d67c3dfe`, `f30914e6`,
  `c0de9595`).
- **Theme fit:** compliance risk and process automation; the planted-instruction check guards against a tampered
  document. Document input is roadmap.
- **Value to users and community:** the compliance officer gets a checkable answer per clause; borrowers gain
  when the rules hold, for example repayment "directly in the NBFC’s bank account" (para 10(2)) and partner fees
  "not charged to or collected from the borrowers separately by LSP" (para 10(4)).
- **Originality:** rule-change tracking and cited answers already exist; we add code-checked citations, a monitor
  that steps in during the review, error rates published with n and run ids, and open source code.
- **Beyond basic AI:** an agent loop with tools; a step monitor that can send a draft back or stop it, measured on
  and off with two agents (see Headline numbers); and a labelled backup model when the agent model is busy.
- **Usability and cohesion:** one click to a live review with no sign-up; run, live trace, finding with RBI's
  text, officer sign-off and audit log sit in one workspace.
- **Accessibility:** the app's text and status colours reach a 4.5:1 contrast ratio on their backgrounds, checked
  by a unit test ([web/src/styles/contrast.test.ts](web/src/styles/contrast.test.ts)); no full accessibility
  audit has been run.

## What is real and what is not

| Part | Status |
|---|---|
| Gemini agent and monitor | Real calls. The deployed app uses Vertex AI only; the saved runs, our cloud verification runs and the official evaluation on 17 Oct went through Vertex AI. Earlier preliminary runs through OpenRouter are kept only as history in [docs/evaluation.md](docs/evaluation.md). |
| RBI library | Real text from rbi.org.in HTML pages, fetched 16 Oct 2026, each paragraph hashed: 4 instruments, 365 paragraphs, one in force. |
| Supersession map, verifier, action rules | Real code and data, unit-tested. |
| Sample policies and evaluation cases | **Synthetic**, written for this project. No real lender or personal data. |
| Test doubles (`llm_fake.py`, `store_fake.py`, seed scripts) | Offline tests and the local emulator only; blocked from production. |
| Costs | Estimates from measured tokens and published prices, not invoices. |

## Limitations

- **Narrow scope:** NBFC digital lending only, four RBI instruments, keyword search over 365 paragraphs. Rules
  published only as PDFs (for example the Key Facts Statement format) are cross-referenced, not quoted.
- **"In force" means in force on the library date**, not a live check against RBI. Drafts and unfinalised
  amendments are not in the library, so no finding can cite them; nor is the definitions paragraph (para 4).
- **Small samples and model variance:** two synthetic 8-clause policies and a 34-case evaluation set; in 1 of 2
  Vertex AI runs of policy B, clause c8 was labelled compliant instead of gap (run `f30914e6`).
- **The monitor is a fail-safe, not an accuracy boost:** its measured benefit is with a cheaper agent; on the
  shipped agent no improvement was shown (see Headline numbers). Its support and injection checks are model
  judgements and can miss: on the cheaper agent's hard set, the monitor let wrong labels through unflagged on
  3/20 attack reviews: ha-08 twice and ha-07 once, 2 of the 10 cases. The monitor said continue on all three.
  Counting one failed review, the figure is 4/20 (run 20261017T165434Z-a52f4e). With the cheaper agent, correct
  citations rose from 37/46 to 42/46 with the monitor on the base set (run 20261017T164720Z-f694fd), and did not
  rise on the hard set: 16/20 with and without the monitor (run 20261017T165434Z-a52f4e).
- **Vertex AI capacity was a real cost:** 19/136 strong-agent reviews in the headline runs failed because Gemini
  3.8 Flash returned 504 or 499. That is 12/96 on the base set (run 20261017T162059Z-5dc6b4), including 8/48 with
  the monitor on, and 7/40 on the hard set (run 20261017T154026Z-446cd6). Every one is counted as a failure.
- **What the evaluation does not measure:** the evaluation ran with the backup model switched off, so it does
  not measure the deployed hand-over to Gemini 3.1 Flash-Lite. The gold labels are this project's own; no
  independent compliance expert labelled them.
- **The backup model is weaker:** in a deployment check where all 8 clauses of policy A went to Gemini 3.1
  Flash-Lite (run `69eaa7ca0c1144fb8266651b09a99ad3`), it matched 6/8 planted labels: c2 came back as a gap
  instead of an outdated reference, c7 as a gap instead of compliant, and the monitor passed both. Both findings
  carried the backup label.
- **Not yet:** reviewing a whole policy or a document upload (one pasted clause at a time only), more
  instruments, a long-context baseline,
  independent expert labels, Model Armor (deferred for time).

## Quickstart (local)

Python 3.11, Docker, Node 22.12+ and an OpenRouter or AI Studio key; Firebase emulators, no Google Cloud account
needed. Steps: [docs/development.md](docs/development.md) (`make setup`, `make up`, `make corpus`, `make run`).

## Team

Hemant Kumar BK (backend, agent and monitor, Google Cloud deployment) · Kirtee Shingan (UX and frontend design) ·
Aryan Yadav (frontend, demo video and testing) · T J Tejas Maladhkar (evaluation set and quality checks).

**Licence:** MIT. See [LICENSE](LICENSE).
