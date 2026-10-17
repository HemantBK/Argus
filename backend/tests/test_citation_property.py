"""Property: with the monitor on, no final finding carries a citation the library cannot back.

Every fixed bad draft of the eval set (argus_api/eval/cases/bad_drafts.json) is run through the real agent
loop and monitor with FakeLLM. The agent drafts the bad finding, and when the monitor asks for a revision it
drafts the same bad finding again (the worst case: it learns nothing). The monitor model's judgement is
scripted in several ways, including one that passes everything it is asked about, so only the code checks
stand between a bad citation and the finding. Whatever happens, each final finding's citation is None or
quotes its library paragraph word for word (corpus.quote_in_text), names a rule in force, and carries the
paragraph's content hash; a replaced_citation, when present, quotes its paragraph too.
"""

from collections.abc import Callable
from typing import Any

import pytest

from argus_api.agent import Clause, review_clause
from argus_api.corpus.store import Corpus, quote_in_text
from argus_api.eval.dataset import BAD_DRAFTS_FILE, CASES_DIR, BadDraft, load_bad_drafts
from argus_api.llm_fake import FakeLLM, turn
from argus_api.monitor.core import CITATION_CHECKS
from argus_api.schema import Citation, Finding
from tests.run_support import fixture_corpus, judgement, settings

BAD_DRAFTS = load_bad_drafts(CASES_DIR / BAD_DRAFTS_FILE)

# The monitor model's possible answers on a draft: content checks all pass, or one of them fails.
JUDGEMENTS: dict[str, Callable[[], dict[str, Any]]] = {
    "passes_everything": judgement,
    "unsupported": lambda: {**judgement(), "supported": False, "supported_detail": "Not supported."},
    "off_task": lambda: {**judgement(), "on_task": False, "on_task_detail": "Off task."},
}


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return fixture_corpus()


def _draft_reply(bad: BadDraft) -> dict[str, Any]:
    reply = bad.draft.model_dump(mode="json")
    del reply["clause_id"]  # the loop sets it from the clause
    return reply


def _backed(c: Citation, corpus: Corpus) -> None:
    p = corpus.paragraph(c.instrument_ref, c.para)
    assert p is not None, f"citation of {c.instrument_ref} para {c.para} is not in the library"
    assert quote_in_text(c.quote, p.text), f"quote is not verbatim in {c.instrument_ref} para {c.para}"
    assert c.content_hash == p.content_hash and c.in_force == p.in_force


def _assert_property(f: Finding, corpus: Corpus) -> None:
    if f.citation is not None:
        _backed(f.citation, corpus)
        assert f.citation.in_force, "the rule a finding rests on must be in force"
    if f.replaced_citation is not None:
        _backed(f.replaced_citation, corpus)


def test_the_bad_draft_set_covers_every_citation_check_and_clean_drafts() -> None:
    """The property is not vacuous: some drafts break each citation check, and some carry a sound citation."""
    faults = {b.expected_failed_check for b in BAD_DRAFTS}
    assert set(CITATION_CHECKS) <= faults
    assert any(b.expected_failed_check not in CITATION_CHECKS for b in BAD_DRAFTS)


@pytest.mark.parametrize("answer", sorted(JUDGEMENTS))
@pytest.mark.parametrize("bad", BAD_DRAFTS, ids=[b.case_id for b in BAD_DRAFTS])
def test_every_monitored_finding_has_no_citation_or_a_verbatim_one(
    corpus: Corpus, bad: BadDraft, answer: str
) -> None:
    reply = _draft_reply(bad)
    llm = FakeLLM(
        turns=[turn(text="Ready to draft.")],
        # draft, monitor, revised draft (the same bad one), monitor: enough for the one revision there is
        json_replies=[reply, JUDGEMENTS[answer](), reply, JUDGEMENTS[answer]()],
    )
    clause = Clause(id=bad.draft.clause_id, text=bad.clause_text)
    outcome = review_clause(llm, settings(), corpus, clause, lambda step: None)
    assert outcome.verdicts, "the monitor must have run"
    _assert_property(outcome.finding, corpus)
    if bad.expected_failed_check in CITATION_CHECKS:
        # The draft's citation is broken and the agent repeated it: the finding must carry none at all.
        assert outcome.finding.citation is None and outcome.finding.replaced_citation is None
