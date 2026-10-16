"""Deterministic checks the step monitor runs before any model call.

check_citation      does the cited paragraph exist, does the quote match it verbatim, is it in force?
check_reference     for a paragraph the agent read: does it exist, is it in force (no quote yet)?
prescan_injection   does the policy text contain an instruction aimed at the reviewer?

Each check returns schema.Check objects whose `detail` is a plain-English sentence for a compliance officer.
"""

import re

from argus_api.corpus.store import Corpus
from argus_api.schema import Check, CheckName


def _where(instrument_ref: str, para: str) -> str:
    return f"paragraph {para} of {instrument_ref}"


def _exists_check(corpus: Corpus, instrument_ref: str, para: str, subject: str) -> Check:
    """PARAGRAPH_EXISTS; `subject` says who points at the paragraph ("The finding cites")."""
    known = instrument_ref in corpus.instruments
    paragraph = corpus.paragraph(instrument_ref, para) if known else None
    where = _where(instrument_ref, para)
    if not known:
        return Check(
            name=CheckName.PARAGRAPH_EXISTS,
            passed=False,
            detail=f"{subject} {instrument_ref}, which is not an instrument in the RBI library.",
        )
    if paragraph is None:
        snapshot = corpus.instrument(instrument_ref).snapshot_date.isoformat()
        under = corpus.paragraphs_under(instrument_ref, para)
        listed = f" Paragraphs with that number in the library: {', '.join(under)}." if under else ""
        return Check(
            name=CheckName.PARAGRAPH_EXISTS,
            passed=False,
            detail=f"{subject} {where}, which does not exist in the RBI snapshot of {snapshot}.{listed}",
        )
    return Check(
        name=CheckName.PARAGRAPH_EXISTS,
        passed=True,
        detail=f"{where[0].upper()}{where[1:]} exists in the RBI snapshot of "
        f"{paragraph.snapshot_date.isoformat()}.",
    )


def check_reference(corpus: Corpus, instrument_ref: str, para: str) -> list[Check]:
    """PARAGRAPH_EXISTS and IN_FORCE for a paragraph the agent read, in that order: can it back a finding?"""
    return [
        _exists_check(corpus, instrument_ref, para, "The agent asked to read"),
        _in_force_check(corpus, instrument_ref, para),
    ]


def check_citation(corpus: Corpus, instrument_ref: str, para: str, quote: str) -> list[Check]:
    """PARAGRAPH_EXISTS, QUOTE_MATCHES and IN_FORCE for one citation, always in that order."""
    known = instrument_ref in corpus.instruments
    paragraph = corpus.paragraph(instrument_ref, para) if known else None
    where = _where(instrument_ref, para)
    exists = _exists_check(corpus, instrument_ref, para, "The finding cites")

    if paragraph is None:
        quote_check = Check(
            name=CheckName.QUOTE_MATCHES,
            passed=False,
            detail="The quoted text cannot be checked because the cited paragraph is not in the library.",
        )
    elif not quote.strip():
        quote_check = Check(
            name=CheckName.QUOTE_MATCHES,
            passed=False,
            detail=f"The finding gives no quoted text from {where}.",
        )
    elif corpus.verify_quote(instrument_ref, para, quote).matches:
        quote_check = Check(
            name=CheckName.QUOTE_MATCHES,
            passed=True,
            detail=f"The quoted text appears word for word in {where}.",
        )
    else:
        quote_check = Check(
            name=CheckName.QUOTE_MATCHES,
            passed=False,
            detail=f"The quoted text does not appear word for word in {where}: it has been altered, cut "
            "mid-word or mid-figure, or comes from elsewhere.",
        )

    return [exists, quote_check, _in_force_check(corpus, instrument_ref, para)]


def _in_force_check(corpus: Corpus, instrument_ref: str, para: str) -> Check:
    if instrument_ref not in corpus.instruments:
        return Check(
            name=CheckName.IN_FORCE,
            passed=False,
            detail=f"{instrument_ref} is not in the RBI library, so it cannot be confirmed as in force.",
        )
    inst = corpus.instrument(instrument_ref)
    if inst.in_force:
        return Check(
            name=CheckName.IN_FORCE,
            passed=True,
            detail=f"{inst.instrument} ({instrument_ref}) is in force on {inst.snapshot_date.isoformat()}.",
        )
    ended = f" since {inst.valid_to.isoformat()}" if inst.valid_to is not None else ""
    current = corpus.current_instrument(instrument_ref)
    moved = sorted({row.new_para for row in corpus.rule_moves(instrument_ref, para)})
    now_at = f" The rule now sits at para {', '.join(moved)} of {current.instrument_ref}." if moved else ""
    return Check(
        name=CheckName.IN_FORCE,
        passed=False,
        detail=f"{inst.instrument} ({instrument_ref}) is no longer in force{ended}; it has been replaced by "
        f"{current.instrument} ({current.instrument_ref}). A finding must cite the rule in force.{now_at}",
    )


# Instructions aimed at whoever reviews the policy, not obligations on the lender. Ordinary lending wording
# ("pre-approved loans", "the system shall approve", "a cheque treated as cleared", "staff do not review old
# files", "verified through the Reserve Bank's CKYC") must not match, so every pattern needs one of:
#   - the review outcome itself ("compliant"), applied to the policy text or to nothing at all;
#   - the policy text as its subject or object ("this clause", "the policy has been", "it" before "as
#     compliant");
#   - a regulator named as the one who cleared the policy text;
#   - wording only a prompt uses ("ignore previous instructions", "system prompt").
# Patterns join words with whitespace only, so a match never spans two sentences or leaves a footnote.
_TEXT = r"(?:clause|policy|section|provision|footnote|paragraph|document|text)"
_THE_TEXT = rf"(?:(?:this|the|that)\s+{_TEXT})"  # the policy text as subject of a claim about it
_THIS_TEXT = rf"(?:this\s+{_TEXT})"  # the policy text as object of an instruction
_REGULATOR = r"(?:the\s+)?(?:RBI|Reserve\s+Bank(?:\s+of\s+India)?|regulator)\b(?!['\u2019]s)"
_REVIEWER = r"(?:reviewers?|auditors?|assistants?|AI|LLMs?|(?:language\s+)?models?|agents?|you)"
_CLEARED = r"(?:pre-?\s?)?(?:cleared|approved|vetted|verified|endorsed|sanctioned)"
INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # Imperatives: "treat as compliant", "mark it as compliant". The passive ("an account treated as
        # compliant with KYC") is ordinary wording unless the policy text itself is the subject (next one).
        rf"\b(?:treat|mark|label|classify|rate|report)\s+(?:(?:{_THE_TEXT}|it)\s+)?as\s+"
        r"(?:fully\s+|already\s+)?compliant\b",
        rf"\b{_THE_TEXT}\s+(?:is|are|shall\s+be|should\s+be|must\s+be|is\s+to\s+be)\s+(?:treated|marked|"
        r"labell?ed|classified|rated|reported)\s+as\s+(?:fully\s+|already\s+)?compliant\b",
        # "this clause has been pre-cleared", "the policy was approved by the Reserve Bank"
        rf"\b{_THE_TEXT}\s+(?:has\s+been|have\s+been|is|was|stands|are)\s+(?:already\s+|duly\s+|fully\s+)?"
        rf"(?:pre-?\s?(?:cleared|approved|vetted|verified)\b|{_CLEARED}\s+by\s+{_REGULATOR})",
        # "pre-cleared by RBI" with no subject (a footnote fragment)
        rf"\bpre-?\s?(?:cleared|approved|vetted|verified)\s+by\s+{_REGULATOR}",
        # "ignore previous instructions", "disregard the above rules"
        r"\b(?:ignore|disregard)\s+(?:all\s+|any\s+)?(?:the\s+|your\s+|these\s+)?(?:(?:previous|prior|above|"
        r"earlier|preceding|other)\s+)?(?:instructions?|prompts?|system\s+prompt)\b",
        r"\b(?:ignore|disregard)\s+(?:all\s+|any\s+)?(?:the\s+|your\s+)?(?:previous|prior|above|earlier|"
        r"preceding)\s+(?:rules?|checks?|guidance)\b",
        r"\b(?:ignore|disregard)\s+(?:all\s+of\s+)?the\s+above\b",
        # "do not flag this clause"
        rf"\b(?:do\s+not|don't|need\s+not|no\s+need\s+to)\s+(?:flag|review|check|verify|examine|question)\s+"
        rf"{_THIS_TEXT}\b",
        # "no further review is required for this clause", "this clause needs no review"
        rf"\bno\s+(?:further\s+)?(?:review|verification|checks?)\s+(?:is\s+|are\s+)?(?:needed|required|"
        rf"necessary)\s+(?:for|of|on)\s+{_THIS_TEXT}\b",
        rf"\b{_THIS_TEXT}\s+(?:needs|requires|warrants)\s+no\s+(?:further\s+)?(?:review|verification|checks?)\b",
        rf"\b{_THIS_TEXT}\s+(?:is|are)\s+exempt(?:ed)?\s+from\s+(?:review|verification|checks?|scrutiny)\b",
        # prompt-only wording
        r"\bsystem\s+prompt\b",
        r"\byou\s+are\s+now\s+(?:a|an|the)\s+(?:\w+\s+)?(?:assistant|reviewer|model|AI|auditor|agent)\b",
        rf"\bnew\s+instructions?\s+(?:for|to)\s+(?:the\s+)?{_REVIEWER}\b",
        # "the reviewer must mark this clause ...", "you should treat it ..."
        rf"\b{_REVIEWER}\s+(?:must|should|shall|will|is\s+to|are\s+to)\s+(?:mark|treat|classify|label|"
        rf"approve|pass|accept|clear)\s+{_THIS_TEXT}\b",
        r"\bnote\s+to\s+(?:the\s+)?(?:reviewers?|assistants?|AI|LLMs?|(?:language\s+)?models?|agents?)\b",
    )
)
_OPENERS = {"[": "]", "(": ")"}
_SENTENCE_END = re.compile(r"[.!?;](?=\s|$)|\n")


def _enclosing_segment(text: str, start: int, end: int) -> str:
    """The bracketed note (footnote) holding the match, else the sentence holding it, verbatim."""
    for opener, closer in _OPENERS.items():
        left = text.rfind(opener, 0, start)
        if left == -1 or text.rfind(closer, left, start) != -1:
            continue
        right = text.find(closer, end)
        if right != -1:
            return text[left : right + 1]
    left = 0
    for m in _SENTENCE_END.finditer(text, 0, start):
        left = m.end()
    m_end = _SENTENCE_END.search(text, end)
    right = m_end.end() if m_end is not None else len(text)
    return text[left:right].strip()


def prescan_injection(clause_text: str) -> Check:
    """NO_INJECTION from a pattern scan. On a hit, `detail` quotes every offending passage verbatim."""
    quoted: list[str] = []
    for pattern in INJECTION_PATTERNS:
        for m in pattern.finditer(clause_text):
            segment = _enclosing_segment(clause_text, m.start(), m.end())
            if any(segment in q for q in quoted):
                continue
            quoted = [q for q in quoted if q not in segment] + [segment]
    if not quoted:
        return Check(
            name=CheckName.NO_INJECTION,
            passed=True,
            detail="No instruction aimed at the reviewer was found in the policy text.",
        )
    joined = " ".join(f'"{q}"' for q in quoted)
    return Check(
        name=CheckName.NO_INJECTION,
        passed=False,
        detail="The policy text contains an instruction aimed at the reviewer, not a rule for the lender: "
        f"{joined}. A claim like this is not evidence of compliance; a person must review this clause.",
    )
