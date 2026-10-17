// Canonical product copy. TAGLINE is the one sentence the Start screen, PLAN and UI_SPEC quote; it is taken word for
// word from research/CLAIMS_LEDGER.md ("Proposed TAGLINE") and uses only claims the ledger supports. Change it
// there first, then here; nothing else in web/src may hold its text (start/copy.test.ts checks this).
export const TAGLINE =
  "Every RBI rule an Argus finding rests on is rebuilt from a dated library of RBI text: the paragraph exists, the quote matches it word for word, and the rule is in force on the library date. A Gemini monitor reviews each draft finding for support and hidden instructions; anything that fails goes back for one revision or to a person.";

// The Start screen h1. It names the library, not "today": an instrument's status is as of the library fetch date,
// not a live check of RBI (research/CLAIMS_LEDGER.md N2).
export const HEADLINE = "Check your digital-lending policy against a dated library of RBI rules.";
