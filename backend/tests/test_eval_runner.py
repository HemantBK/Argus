"""The eval runner offline: scripted LLM, fake reviewers, fixture corpus and fixture cases."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from argus_api.agent import AgentStep, ReviewOutcome, review_clause
from argus_api.config import PINNED_MODELS, Settings
from argus_api.corpus.store import Corpus
from argus_api.eval.dataset import DraftSpec, load_bad_drafts, load_cases
from argus_api.eval.plain import PLAIN_RESPONSE_SCHEMA
from argus_api.eval.records import CitationOut
from argus_api.eval.runner import (
    MONITOR_SWITCH,
    EvalError,
    MonitorSwitchMissing,
    assess_citation,
    monitor_switch_supported,
    run_condition,
)
from argus_api.eval.settings import EvalSettings
from argus_api.llm_fake import FakeLLM
from argus_api.monitor import DraftFinding
from argus_api.monitor.prompts import MONITOR_RESPONSE_SCHEMA
from argus_api.schema import (
    Check,
    CheckName,
    Citation,
    Finding,
    FindingLabel,
    MonitorAction,
    MonitorStatus,
    StepKind,
    Verdict,
)

FIXTURES = Path(__file__).parent / "fixtures" / "eval"
CORPUS_DIR = Path(__file__).parent / "fixtures" / "corpus"
AGENT_MODEL, MONITOR_MODEL = PINNED_MODELS["openrouter"]


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return Corpus.load(CORPUS_DIR)


def es() -> EvalSettings:
    return EvalSettings.from_settings(
        Settings(
            mode="local",
            provider="openrouter",
            project="test",
            location=None,
            api_key="unused-by-fake",
            agent_model=AGENT_MODEL,
            monitor_model=MONITOR_MODEL,
        )
    )


def cases() -> list[Any]:
    return load_cases(FIXTURES / "clauses.json") + load_cases(FIXTURES / "attacks.json")


def test_draft_spec_matches_monitor_draft_finding() -> None:
    assert DraftSpec.model_fields.keys() == DraftFinding.model_fields.keys()


def test_plain_schema_requires_every_property() -> None:
    assert set(PLAIN_RESPONSE_SCHEMA["required"]) == set(PLAIN_RESPONSE_SCHEMA["properties"])


# ---- monitor switch ----------------------------------------------------------------------------------------


def test_agent_no_monitor_fails_loudly_without_the_switch(corpus: Corpus) -> None:
    calls: list[Any] = []

    def reviewer(
        llm: Any, settings: Any, corpus: Any, clause: Any, emit: Any, *, today: Any, run_id: Any
    ) -> Any:
        calls.append(clause)
        raise AssertionError("must not be called")

    assert not monitor_switch_supported(reviewer)
    with pytest.raises(MonitorSwitchMissing, match=MONITOR_SWITCH):
        run_condition(
            "agent_no_monitor",
            llm=FakeLLM(),
            es=es(),
            corpus=corpus,
            cases=cases(),
            run_id="r",
            reviewer=reviewer,
        )
    assert calls == []


def test_real_review_clause_is_checked_the_same_way(corpus: Corpus) -> None:
    if monitor_switch_supported(review_clause):
        pytest.skip("review_clause has the monitor switch (REQUESTS.md R1 landed)")
    with pytest.raises(MonitorSwitchMissing):
        run_condition("agent_no_monitor", llm=FakeLLM(), es=es(), corpus=corpus, cases=cases(), run_id="r")


def _citation(corpus: Corpus, ref: str, para: str, quote: str) -> Citation:
    p = corpus.paragraph(ref, para)
    assert p is not None
    inst = corpus.instrument(ref)
    return Citation(
        instrument=inst.instrument,
        instrument_ref=ref,
        para=para,
        url=p.url,
        quote=quote,
        snapshot_date=p.snapshot_date.isoformat(),
        content_hash=p.content_hash,
        in_force=inst.in_force,
    )


def _step(seq: int, kind: StepKind, action: MonitorAction | None = None) -> AgentStep:
    verdict = None
    if action is not None:
        verdict = Verdict(
            action=action,
            checks=[Check(name=n, passed=action == MonitorAction.CONTINUE, detail="d") for n in CheckName],
            reason="r",
        )
    return AgentStep(
        seq=seq, clause_id="c", kind=kind, summary="s", verdict=verdict, at=datetime.now(UTC), model=None
    )


def make_reviewer(corpus: Corpus, actions: list[MonitorAction | None], seen: dict[str, Any]) -> Any:
    """A reviewer that takes the switch, echoes the case's gold finding and emits the given verdicts."""
    gold = {c.case_id: c for c in cases()}

    def reviewer(
        llm: Any,
        settings: Any,
        corpus_: Corpus,
        clause: Any,
        emit: Any,
        *,
        today: Any = None,
        run_id: Any = None,
        monitor_enabled: bool = True,
    ) -> ReviewOutcome:
        seen[clause.id] = {"monitor_enabled": monitor_enabled, "today": today}
        emit(_step(1, StepKind.PLAN))
        for i, a in enumerate(actions, start=2):
            emit(_step(i, StepKind.MONITOR if a is not None else StepKind.READ, a))
        g = gold[clause.id]
        cit = _citation(corpus, g.gold_citation.instrument_ref, g.gold_citation.para, g.gold_citation.quote)
        finding = Finding(
            clause_id=clause.id,
            clause_text=clause.text,
            label=g.gold_label,
            rationale="echo",
            citation=cit,
            monitor_status=MonitorStatus.PASSED,
        )
        return ReviewOutcome(
            finding=finding,
            verdicts=[],
            revised=False,
            stopped=False,
            tool_calls=0,
            input_tokens=100,
            output_tokens=10,
            cost_usd_estimate=0.001,
            latency_ms=5,
        )

    return reviewer


def test_agent_no_monitor_passes_the_switch_off(corpus: Corpus) -> None:
    seen: dict[str, Any] = {}
    recs = run_condition(
        "agent_no_monitor",
        llm=FakeLLM(),
        es=es(),
        corpus=corpus,
        cases=cases(),
        run_id="r",
        reviewer=make_reviewer(corpus, [None], seen),
    )
    assert {cid: v["monitor_enabled"] for cid, v in seen.items()} == {c.case_id: False for c in cases()}
    assert all(r.label_correct and not r.monitor_active and r.monitor_actions == [] for r in recs)
    assert all(r.citation_assessment is not None and r.citation_assessment.accurate for r in recs)


def test_agent_no_monitor_with_verdicts_fails_loudly(corpus: Corpus) -> None:
    with pytest.raises(EvalError, match="did not turn the monitor off"):
        run_condition(
            "agent_no_monitor",
            llm=FakeLLM(),
            es=es(),
            corpus=corpus,
            cases=cases(),
            run_id="r",
            reviewer=make_reviewer(corpus, [MonitorAction.CONTINUE], {}),
        )


def test_full_argus_records_first_intervention(corpus: Corpus) -> None:
    actions = [MonitorAction.CONTINUE, None, MonitorAction.REVISE, MonitorAction.STOP]
    seen: dict[str, Any] = {}
    recs = run_condition(
        "full_argus",
        llm=FakeLLM(),
        es=es(),
        corpus=corpus,
        cases=cases(),
        run_id="r",
        reviewer=make_reviewer(corpus, actions, seen),
        repeat=2,
    )
    assert len(recs) == 2 * len(cases())
    assert all(v["monitor_enabled"] is True for v in seen.values())
    # step 1 is the plan step; the actions follow from step 2
    first = next(i for i, a in enumerate(actions, start=2) if a in (MonitorAction.REVISE, MonitorAction.STOP))
    for r in recs:
        assert r.first_intervention_step == first
        assert r.first_intervention_action == MonitorAction.REVISE
        assert r.monitor_actions == [a for a in actions if a is not None]
        assert r.stopped


def test_full_argus_without_any_verdict_fails_loudly(corpus: Corpus) -> None:
    with pytest.raises(EvalError, match="monitor did not run"):
        run_condition(
            "full_argus",
            llm=FakeLLM(),
            es=es(),
            corpus=corpus,
            cases=cases(),
            run_id="r",
            reviewer=make_reviewer(corpus, [None], {}),
        )


# ---- plain_gemini ------------------------------------------------------------------------------------------


def plain_reply(case: Any, **change: Any) -> dict[str, Any]:
    g = case.gold_citation
    reply = {
        "label": case.gold_label.value,
        "rationale": "r",
        "citation_instrument_ref": g.instrument_ref,
        "citation_para": g.para,
        "citation_quote": g.quote,
        "policy_cited_instrument_ref": case.policy_cited_instrument_ref,
        "policy_cited_para": case.policy_cited_para,
    }
    reply.update(change)
    return reply


def test_plain_gemini_one_call_per_case_no_tools(corpus: Corpus) -> None:
    cs = cases()
    replies = [
        plain_reply(cs[0]),
        plain_reply(cs[1], citation_para="99"),
        plain_reply(cs[2], citation_quote=None),
    ]
    replies += [plain_reply(c, label=FindingLabel.COMPLIANT.value) for c in cs[3:]]
    llm = FakeLLM(json_replies=replies)
    recs = run_condition("plain_gemini", llm=llm, es=es(), corpus=corpus, cases=cs, run_id="r")
    assert [c.method for c in llm.calls] == ["complete_json"] * len(cs)
    assert all(c.model == AGENT_MODEL and c.schema == PLAIN_RESPONSE_SCHEMA for c in llm.calls)
    assert all(cs[i].clause_text in llm.calls[i].prompt for i in range(len(cs)))  # type: ignore[operator]
    assert recs[0].citation_assessment is not None and recs[0].citation_assessment.accurate
    assert recs[1].citation_assessment is not None and not recs[1].citation_assessment.exists
    assert recs[2].predicted_citation is None and recs[2].citation_assessment is None
    assert [r.label_correct for r in recs] == [True, True, True] + [
        c.gold_label == FindingLabel.COMPLIANT for c in cs[3:]
    ]
    stale = recs[1]
    assert stale.gold_stale and stale.predicted_replaced is not None
    assert stale.predicted_replaced.instrument_ref == cs[1].policy_cited_instrument_ref


def test_plain_gemini_llm_failure_is_recorded(corpus: Corpus) -> None:
    from argus_api.llm import LLMError

    llm = FakeLLM(json_replies=[LLMError("provider down")])
    [r] = run_condition("plain_gemini", llm=llm, es=es(), corpus=corpus, cases=cases()[:1], run_id="r")
    assert r.error is not None and "provider down" in r.error and r.predicted_label is None


def test_assess_citation(corpus: Corpus) -> None:
    g = cases()[0].gold_citation
    assert g is not None
    withdrawn = CitationOut(instrument_ref="RBI/2025-26/36", para="9(iv)", quote="RE shall ensure")
    a = assess_citation(corpus, withdrawn, g)
    assert a is not None and a.exists and not a.in_force and a.quote_verified and not a.same_para_as_gold
    unknown = CitationOut(instrument_ref="RBI/0000/0", para="1", quote="x")
    a = assess_citation(corpus, unknown, g)
    assert a is not None and not (a.exists or a.in_force or a.quote_verified)
    assert assess_citation(corpus, None, g) is None


# ---- monitor_only ------------------------------------------------------------------------------------------


def judgement(**change: Any) -> dict[str, Any]:
    j = {
        "supported": True,
        "supported_detail": "ok",
        "injection_present": False,
        "injection_quote": "",
        "agent_followed_injection": False,
        "injection_detail": "",
        "on_task": True,
        "on_task_detail": "ok",
    }
    j.update(change)
    assert set(j) == set(MONITOR_RESPONSE_SCHEMA["required"])
    return j


def test_monitor_only_calls_monitor_finding_directly(corpus: Corpus) -> None:
    drafts = load_bad_drafts(FIXTURES / "bad_drafts.json")
    llm = FakeLLM(json_replies=[judgement() for _ in drafts])
    recs = run_condition("monitor_only", llm=llm, es=es(), corpus=corpus, bad_drafts=drafts, run_id="r")
    assert [c.method for c in llm.calls] == ["complete_json"] * len(drafts)
    assert all(c.model == MONITOR_MODEL for c in llm.calls)
    for d, r in zip(drafts, recs, strict=True):
        assert r.error is None and r.monitor_actions == [r.final_action]
        if d.expected_failed_check is not None:
            # every fixture fault is in the citation, which the deterministic checks catch
            assert d.expected_failed_check in r.failed_checks
            assert r.final_action == MonitorAction.REVISE and r.first_intervention_step == 1
        else:
            assert r.failed_checks == [] and r.final_action == MonitorAction.CONTINUE


def test_conditions_need_their_inputs(corpus: Corpus) -> None:
    with pytest.raises(EvalError, match="bad drafts"):
        run_condition("monitor_only", llm=FakeLLM(), es=es(), corpus=corpus, run_id="r")
    with pytest.raises(EvalError, match="clause or attack"):
        run_condition("plain_gemini", llm=FakeLLM(), es=es(), corpus=corpus, run_id="r")
