"""Paste-a-clause: check a clause a user pasted and turn it into a one-clause Policy that runs like a sample.

POST /api/runs {run_id, policy_id: "custom", clause_text, attest: true} passes through `check_clause`, which
returns the normalised text or raises ClauseRejected (HTTP 400). The checks, in this order:

  not_attested      attest is not exactly true (the user did not confirm the text is synthetic or anonymised)
  too_short         fewer than MIN_CHARS characters after normalisation
  too_long          more than MAX_CHARS characters after normalisation
  identifier_found  the text looks like it holds a personal or account identifier (IDENTIFIERS)

Normalisation: Unicode NFC; \\r\\n and \\r become \\n; every other control character (category Cc) except tab
and newline is removed, and so is every invisible format character (category Cf, such as a zero-width space,
which could otherwise split a PAN or an account number past the prescan); then leading and trailing white
space is stripped. Lengths are counted in characters (code points) of that result, and that result is what
the agent reviews.

The identifier prescan is a guard against obvious personal data, not a promise that none gets through: it
looks for email addresses, Indian mobile numbers, PAN, 12-digit Aadhaar-like numbers, 16-digit card numbers,
IFSC codes and 9-18 digit account-number runs. A grouped number (digits separated by single spaces or
hyphens) is judged as a whole: "4242 4242 4242 424" is not an Aadhaar number followed by three digits.

Errors carry a code, a plain-English message and (identifier_found only) the identifier type. The clause text
is never echoed back and never logged: ClauseRejected's message names the problem only.
"""

import re
import unicodedata
from datetime import timedelta
from typing import Any

from argus_api.agent import Clause
from argus_api.runs import CUSTOM_POLICY_ID, CUSTOM_TTL, Policy, Rejected

MIN_CHARS = 40
MAX_CHARS = 1500
CUSTOM_CLAUSE_ID = "c1"
CUSTOM_POLICY_TITLE = "Your clause"
CUSTOM_DISCLAIMER = (
    "Text pasted by the user, attested as synthetic or anonymised; kept in their private run, which expires "
    f"{CUSTOM_TTL // timedelta(hours=1)} hours after it starts and is then deleted automatically; "
    "not legal advice."
)

TOO_SHORT = "too_short"
TOO_LONG = "too_long"
NOT_ATTESTED = "not_attested"
IDENTIFIER_FOUND = "identifier_found"

_KEPT_CONTROLS = frozenset("\t\n")

# Digit-group boundaries: not inside a longer number, whether its digits touch or are grouped by a single
# space or hyphen.
_NUM_START = r"(?<!\d)(?<!\d[\s-])"
_NUM_END = r"(?!\d)(?![\s-]\d)"
_SEP = r"[\s-]?"
_WORD_START = r"(?<![A-Za-z0-9])"
_WORD_END = r"(?![A-Za-z0-9])"

# (identifier type as shown to the user, pattern). Checked in this order, so a 16-digit card number is named
# a card number, not an account number; account-number runs come last as the catch-all for long numbers.
IDENTIFIERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email address",
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    ),
    (
        "card number",
        re.compile(_NUM_START + r"\d{4}" + (_SEP + r"\d{4}") * 3 + _NUM_END),
    ),
    (
        "mobile number",
        re.compile(
            r"(?:(?<![\d+])(?:(?:\+|00)?91[\s-]?|0)|"
            + _NUM_START
            + r")"
            + r"[6-9]\d{4}"
            + _SEP
            + r"\d{5}"
            + _NUM_END
        ),
    ),
    (
        "Aadhaar number",
        re.compile(_NUM_START + r"\d{4}" + _SEP + r"\d{4}" + _SEP + r"\d{4}" + _NUM_END),
    ),
    ("PAN", re.compile(_WORD_START + r"[A-Za-z]{5}\d{4}[A-Za-z]" + _WORD_END)),
    ("IFSC code", re.compile(_WORD_START + r"[A-Za-z]{4}0[A-Za-z0-9]{6}" + _WORD_END)),
    ("account number", re.compile(r"(?<!\d)\d{9,18}(?!\d)")),
)


class ClauseRejected(Rejected):
    """A pasted clause refused before anything was written. str() is the plain-English message."""

    status_code = 400

    def __init__(self, code: str, message: str, identifier_type: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.identifier_type = identifier_type

    def detail(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.identifier_type is not None:
            d["identifier_type"] = self.identifier_type
        return d


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    kept = (ch for ch in text if ch in _KEPT_CONTROLS or unicodedata.category(ch) not in ("Cc", "Cf"))
    return "".join(kept).strip()


def find_identifier(text: str) -> str | None:
    """The type of the first identifier IDENTIFIERS finds in `text`, or None."""
    for kind, pattern in IDENTIFIERS:
        if pattern.search(text):
            return kind
    return None


def check_clause(text: str | None, attest: bool | None) -> str:
    """The normalised clause, or ClauseRejected naming the first problem (module docstring, in order)."""
    if attest is not True:
        raise ClauseRejected(
            NOT_ATTESTED,
            "Please confirm the clause is synthetic or anonymised text before Argus reviews it.",
        )
    clause = normalise(text or "")
    if len(clause) < MIN_CHARS:
        raise ClauseRejected(
            TOO_SHORT,
            f"The clause is too short: paste at least {MIN_CHARS} characters of one policy clause.",
        )
    if len(clause) > MAX_CHARS:
        raise ClauseRejected(
            TOO_LONG,
            f"The clause is too long: paste one clause of at most {MAX_CHARS} characters.",
        )
    kind = find_identifier(clause)
    if kind is not None:
        raise ClauseRejected(
            IDENTIFIER_FOUND,
            f"The clause seems to contain a {kind}. Remove personal and account details and paste "
            "synthetic or anonymised text only.",
            identifier_type=kind,
        )
    return clause


def custom_policy(clause: str) -> Policy:
    """The one-clause Policy a checked clause is reviewed as."""
    return Policy(
        policy_id=CUSTOM_POLICY_ID,
        title=CUSTOM_POLICY_TITLE,
        synthetic=False,
        disclaimer=CUSTOM_DISCLAIMER,
        clauses=[Clause(id=CUSTOM_CLAUSE_ID, text=clause)],
        source="custom",
    )
