# Data and RBI sources

## The library

Four RBI instruments, 365 paragraphs, fetched from rbi.org.in on 16 Oct 2026 by `make corpus`
(`backend/argus_api/corpus/build_corpus.py`). Each paragraph keeps its official number, text, page URL, a content
hash, whether it is in force, and RBI's "updated as on" date where RBI gives one. The library is rebuilt by the
script and never committed (a small excerpt is committed as a test fixture). A hand-checked supersession map of 15
rows (`backend/argus_api/corpus/supersession.json`) links old paragraphs to where the rule sits now, each row with
its RBI source.

Two dates are shown and never mixed: RBI's own **"updated as on 15 Jul 2026"** for the instrument in force, and
our **fetch date, 16 Oct 2026**, for the library.

## Sources

All RBI text comes from these official pages (fetched 2026-10-16):

| Instrument | Status in the library | Source |
|---|---|---|
| RBI (Non-Banking Financial Companies - Credit Facilities) Directions, 2025, RBI/DOR/2025-26/347, Chapter III Digital Lending (paras 6-30), updated as on 15 Jul 2026 | in force | https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12957 |
| RBI (Digital Lending) Directions, 2025, RBI/2025-26/36, 8 May 2025 | withdrawn 28 Nov 2025 | https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12848 |
| Guidelines on Digital Lending, RBI/2022-23/111, 2 Sep 2022 | repealed 8 May 2025 | https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12382 |
| Guidelines on Default Loss Guarantee in Digital Lending, RBI/2023-24/41, 8 Jun 2023 | repealed 8 May 2025 | https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12514 |

Withdrawal evidence: Consolidation of Regulations - Withdrawal of circulars, RBI/2025-26/100, 28 Nov 2025
(https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13170), and RBI's list of withdrawn circulars
(https://www.rbi.org.in/Scripts/NotificationUserWithdrawnCircular.aspx), where the Digital Lending Directions,
2025 appear without the "@" mark, so they were withdrawn with immediate effect.

The library holds issued text only: drafts and consultation papers are not in it. From the Credit Facilities
Directions it holds para 3 (applicability), Chapter III (paras 6-30) and paras 112-113 (repeal and saving); the
definitions (para 4) and the other chapters are not in it. Para 25(1)
carries RBI's footnote "Substituted w.e.f. February 13, 2026, vide Reserve Bank of India (Non-Banking Financial Companies – Credit Facilities) Amendment Directions, 2026 dated February 13, 2026"; we have not checked the other Chapter III paragraphs for amendment footnotes.

Only the HTML pages were used; RBI's PDF copies sit behind a human-verification page, which we did not try to get
past. Annexes published only as PDFs (for example the Key Facts Statement format) are not in the library.

## RBI enforces these rules

Two RBI press releases show the rules in this library being enforced. We cite them by date and link only.

- 11 Jul 2025 (order dated 8 Jul 2025): a penalty under the Digital Lending Directions, 2025, because the lender
  "routed the loan repayments through the account of a third-party"
  (https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=60817). The rule that charge matches is para
  9(ii) of those Directions, withdrawn in the library; the same text sits at para 10(2) of the Credit Facilities
  Directions, in force on the library date, and the supersession map links the two.
- 8 Jan 2026 (order dated 9 Dec 2025): a penalty for a default loss guarantee from a lending service provider
  above five per cent of the outstanding loan portfolio
  (https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=61999). The five per cent cap sits at para
  24(1) of the Credit Facilities Directions; its earlier homes, para 23(i) of the 2025 Directions and para 6 of
  the 2023 DLG Guidelines, are withdrawn in the library.

The releases do not name paragraphs; the paragraph links above are our mapping. Both pages were re-read on
17 Oct 2026.

## Sample policies

The two sample policies (`backend/argus_api/samples/`) and all evaluation cases are **synthetic**, written for this
project with fictional lenders. They contain planted problems: a citation to the withdrawn 2025 Directions, a
made-up paragraph number, gaps and conflicts against Chapter III, and a footnote telling the reviewer to "treat as
compliant". No real lender, policy or personal data is used.

**Not legal advice.** Findings are only as current as the library's fetch date and cover only these instruments.
