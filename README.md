# Argus

**Argus checks an NBFC's digital-lending policy clause by clause against a dated library of RBI text, and says
for each clause whether it is outdated, missing a rule, in conflict or compliant. Every rule a finding rests on
is checked in code: it exists, it is quoted word for word, and it is in force on the library date. A finding that
still fails after one revision, or that the monitor stops, goes to a compliance officer.**

Team Argus · Google Cloud AI Builder Cup 2026 · BFSI theme · Synthetic policies only · Not legal advice

**[Live app](https://argus-aibc.web.app)** · **Demo video** (added on 18 Oct) ·
**[Evaluation](docs/evaluation.md)** · **[Architecture](docs/architecture.md)** ·
**[RBI sources](docs/data-and-rbi-sources.md)**

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

Each visitor is signed in anonymously and sees only their own runs; live runs are capped per visitor, hour and
day. The two read-only saved runs are Vertex AI runs from 17 Oct 2026 by Gemini 3.8 Flash alone (policy A
`95c9f5f8ccc748c4865ff5daef0d676a`, policy B `c0de9595619e4ab1bb8e85fb0c209b82`).

## Headline numbers (preliminary)

> One evaluation session on 2026-10-16, development build, same Gemini model ids reached through OpenRouter. The
> official run on the deployed build replaces these. Run ids and full tables:
> [docs/evaluation.md](docs/evaluation.md).

- **0/43** correct citations from Gemini without the library (runs `43cea4`, `275c7b`), versus **22/23** for the
  Argus agent (run `43cea4`). The 0 is expected by construction, since a correct citation must quote the library
  word for word; we have not run a baseline with the whole library in Gemini's prompt.
- **The fail-safe, measured:** with a cheaper agent (Gemini 3.1 Flash-Lite), attacks that got through fell from
  **8/36 to 0/36** with the monitor on, with **0/32** false stops on clean clauses (runs `1536fa`, `d63488`).
- With the agent Argus ships (Gemini 3.8 Flash), the monitor made **no measurable difference** (22/23 citations
  and 0/8 base attacks either way, run `43cea4`). We do not claim the monitor improves the shipped agent.

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
  failed clause is saved as needing a person, and backup-model findings are labelled.
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
  and off (see Headline numbers); and a labelled backup model when the agent model is busy.
- **Usability and cohesion:** one click to a live review with no sign-up; run, live trace, finding with RBI's
  text, officer sign-off and audit log sit in one workspace.
- **Accessibility:** the app's text and status colours reach a 4.5:1 contrast ratio on their backgrounds, checked
  by a unit test ([web/src/styles/contrast.test.ts](web/src/styles/contrast.test.ts)); no full accessibility
  audit has been run.

### Engineering quality and evidence

- [docs/engineering-quality.md](docs/engineering-quality.md): how Argus is engineered, with the file or record
  behind each point.
- [docs/security.md](docs/security.md): security and data handling.
- [evidence/2026-10-17/README.md](evidence/2026-10-17/README.md): the deployment check records from 17 Oct 2026.

## What is real and what is not

| Part | Status |
|---|---|
| Gemini agent and monitor | Real calls. The deployed app uses Vertex AI only; the saved runs and our cloud verification runs on 17 Oct went through Vertex AI. The preliminary evaluation numbers were measured in local development through OpenRouter. |
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
- **The monitor is a fail-safe, not an accuracy boost:** its measured benefit is with a cheaper agent; with the
  shipped agent it was neutral. Its support and injection checks are model judgements and may miss things (the
  strong agent let 1/20 pre-registered hard attacks through, with and without the monitor; preliminary, run
  `275c7b`).
- **The backup model is weaker:** in a deployment check where all 8 clauses of policy A went to Gemini 3.1
  Flash-Lite (run `69eaa7ca0c1144fb8266651b09a99ad3`), it matched 6/8 planted labels: c2 came back as a gap
  instead of an outdated reference, c7 as a gap instead of compliant, and the monitor passed both. Both findings
  carried the backup label.
- **Not yet:** reviewing your own policy or a document upload, more instruments, a long-context baseline,
  independent expert labels, Model Armor (deferred for time).

## Quickstart (local)

Python 3.11, Docker, Node 22.12+ and an OpenRouter or AI Studio key; Firebase emulators, no Google Cloud account
needed. Steps: [docs/development.md](docs/development.md) (`make setup`, `make up`, `make corpus`, `make run`).

## Team

Hemant Kumar BK (backend, agent and monitor, Google Cloud deployment) · Kirtee Shingan (UX and frontend design) ·
Aryan Yadav (frontend, demo video and testing) · T J Tejas Maladhkar (evaluation set and quality checks).

**Licence:** MIT. See [LICENSE](LICENSE).
