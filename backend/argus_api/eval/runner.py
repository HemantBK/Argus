"""Run the evaluation conditions and record one CaseRecord per case, condition and repeat.

  plain_gemini      plain.review_plain: one structured call, no library, no tools, no monitor
  agent_no_monitor  agent.review_clause(..., monitor_enabled=False): same agent, prompts and tools, no monitor
  full_argus        agent.review_clause(..., monitor_enabled=True when the switch exists)
  monitor_only      monitor.monitor_finding on each fixed bad draft, called directly

The monitor-off switch is requested from the agent's owners in REQUESTS.md. agent_no_monitor refuses to run
(MonitorSwitchMissing) when review_clause has no monitor_enabled parameter, and both agent conditions check
the trace afterwards: a monitor verdict in an agent_no_monitor trace, or none in a full_argus trace, is an
EvalError. The wrong condition is never run under the right name.

Every case runs inside llm.call_scope with the app's run budget (runs.RUN_BUDGET_S), and with `today` set
to the corpus snapshot date, so repeated evaluations see the same prompt. A review that fails the way the app
expects reviews to fail (ClauseReviewError from the agent, LLMError or MonitorError from a model call) is
recorded with its error and counted by error_rate; anything else is a bug and propagates.
"""

import inspect
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import partial
from typing import Any

from argus_api.corpus.store import Corpus
from argus_api.eval.dataset import BadDraft, EvalCase, GoldCitation
from argus_api.eval.plain import review_plain
from argus_api.eval.records import (
    CASE_CONDITIONS,
    CaseRecord,
    CitationAssessment,
    CitationOut,
    Condition,
    ReferenceOut,
    StepRecord,
    first_intervention,
)
from argus_api.eval.settings import EvalSettings
from argus_api.llm import LLM, Completion, LLMError, Message, ToolSpec, Turn, call_scope, estimate_cost_usd
from argus_api.schema import FindingLabel

MONITOR_SWITCH = "monitor_enabled"
Reviewer = Callable[..., Any]


class EvalError(RuntimeError):
    pass


class MonitorSwitchMissing(EvalError):
    pass


def corpus_as_on(corpus: Corpus) -> date:
    return max(inst.snapshot_date for inst in corpus.instruments.values())


def run_budget_s() -> float:
    from argus_api.runs import RUN_BUDGET_S

    return RUN_BUDGET_S


def assess_citation(
    corpus: Corpus, citation: CitationOut | None, gold: GoldCitation | None
) -> CitationAssessment | None:
    """The predicted citation against the corpus and the gold. None when either is missing."""
    if citation is None or gold is None:
        return None
    known = citation.instrument_ref in corpus.instruments
    exists = known and corpus.paragraph(citation.instrument_ref, citation.para) is not None
    verified = (
        exists
        and bool(citation.quote.strip())
        and corpus.verify_quote(citation.instrument_ref, citation.para, citation.quote).matches
    )
    return CitationAssessment(
        exists=exists,
        in_force=known and corpus.in_force(citation.instrument_ref),
        quote_verified=verified,
        same_para_as_gold=(citation.instrument_ref, citation.para) == (gold.instrument_ref, gold.para),
    )


def _gold_fields(case: EvalCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "kind": case.kind,
        "attack_type": case.attack_type,
        "gold_label": case.gold_label,
        "has_gold_citation": case.gold_citation is not None,
        "gold_stale": case.gold_replaced_citation is not None,
        "gold_replaced_instrument_ref": (
            case.gold_replaced_citation.instrument_ref if case.gold_replaced_citation else None
        ),
        "expected_monitor_action": case.expected_monitor_action,
    }


# ---- plain_gemini ----------------------------------------------------------------------------------------


def run_plain_case(
    llm: LLM, es: EvalSettings, corpus: Corpus, case: EvalCase, repeat: int, run_id: str
) -> CaseRecord:
    base = {"condition": "plain_gemini", "repeat": repeat, "models": [es.agent_model], **_gold_fields(case)}
    started = time.perf_counter()
    try:
        with call_scope(started + run_budget_s(), run_id=run_id, clause_id=case.case_id):
            result = review_plain(llm, es.agent_model, case.case_id, case.clause_text, corpus_as_on(corpus))
    except LLMError as e:
        return CaseRecord(
            **base, error=f"LLMError: {e}", latency_ms=round((time.perf_counter() - started) * 1000)
        )
    reply = result.reply
    ref, para, quote = reply["citation_instrument_ref"], reply["citation_para"], reply["citation_quote"]
    citation = CitationOut(instrument_ref=ref, para=para, quote=quote) if ref and para and quote else None
    replaced_ref = reply["policy_cited_instrument_ref"]
    return CaseRecord(
        **base,
        predicted_label=FindingLabel(reply["label"]),
        predicted_citation=citation,
        predicted_replaced=(
            ReferenceOut(instrument_ref=replaced_ref, para=reply["policy_cited_para"])
            if replaced_ref
            else None
        ),
        citation_assessment=assess_citation(corpus, citation, case.gold_citation),
        rationale=reply["rationale"],
        monitor_active=False,
        latency_ms=result.latency_ms,
        input_tokens=result.completion.input_tokens,
        output_tokens=result.completion.output_tokens,
        cost_usd=result.cost_usd,
    )


# ---- agent conditions ------------------------------------------------------------------------------------


def monitor_switch_supported(reviewer: Reviewer) -> bool:
    param = inspect.signature(reviewer).parameters.get(MONITOR_SWITCH)
    return param is not None and param.kind in (
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


def check_reviewer(condition: Condition, reviewer: Reviewer) -> None:
    """Raise before any model call when the reviewer cannot run `condition` as named."""
    if condition == "agent_no_monitor" and not monitor_switch_supported(reviewer):
        raise MonitorSwitchMissing(
            f"agent_no_monitor needs {getattr(reviewer, '__module__', '?')}."
            f"{getattr(reviewer, '__qualname__', reviewer)}(..., {MONITOR_SWITCH}: bool), which it does not "
            "accept; see argus_api/eval/REQUESTS.md. Refusing to run the agent with its monitor on under the "
            "name agent_no_monitor."
        )


def _sum_known(values: list[int | None]) -> int | None:
    return None if not values or any(v is None for v in values) else sum(v for v in values if v is not None)


def _sum_cost(values: list[float | None]) -> float | None:
    return None if not values or any(v is None for v in values) else sum(v for v in values if v is not None)


def run_agent_case(
    llm: LLM,
    es: EvalSettings,
    corpus: Corpus,
    case: EvalCase,
    repeat: int,
    run_id: str,
    condition: Condition,
    reviewer: Reviewer,
) -> CaseRecord:
    from argus_api.agent import Clause, ClauseReviewError

    if condition not in ("agent_no_monitor", "full_argus"):
        raise EvalError(f"run_agent_case cannot run {condition}")
    check_reviewer(condition, reviewer)
    monitor_on = condition == "full_argus"
    kwargs: dict[str, Any] = {"today": corpus_as_on(corpus), "run_id": run_id}
    if monitor_switch_supported(reviewer):
        kwargs[MONITOR_SWITCH] = monitor_on
    emitted: list[Any] = []
    started = time.perf_counter()
    outcome = None
    error = None
    try:
        with call_scope(started + run_budget_s(), run_id=run_id, clause_id=case.case_id):
            outcome = reviewer(
                llm,
                es.settings,
                corpus,
                Clause(id=case.case_id, text=case.clause_text),
                emitted.append,
                **kwargs,
            )
    except ClauseReviewError as e:
        error = f"ClauseReviewError: {e}"
    latency = round((time.perf_counter() - started) * 1000)

    steps = [
        StepRecord(
            index=i, kind=str(s.kind.value), action=s.verdict.action if s.verdict is not None else None
        )
        for i, s in enumerate(emitted, start=1)
    ]
    actions = [s.action for s in steps if s.action is not None]
    if not monitor_on and actions:
        raise EvalError(
            f"agent_no_monitor: case {case.case_id} produced {len(actions)} monitor verdicts with "
            f"{MONITOR_SWITCH}=False; the switch did not turn the monitor off"
        )
    if monitor_on and error is None and not actions:
        raise EvalError(
            f"full_argus: case {case.case_id} finished with no monitor verdict; the monitor did not run"
        )
    first_step, first_action = first_intervention(steps)
    models = sorted({s.model for s in emitted if getattr(s, "model", None)})
    base: dict[str, Any] = {
        "condition": condition,
        "repeat": repeat,
        **_gold_fields(case),
        "monitor_active": monitor_on,
        "steps": steps,
        "monitor_actions": actions,
        "first_intervention_step": first_step,
        "first_intervention_action": first_action,
        "models": models,
    }
    if outcome is None:
        return CaseRecord(
            **base,
            error=error,
            latency_ms=latency,
            input_tokens=_sum_known([s.input_tokens for s in emitted if s.model]),
            output_tokens=_sum_known([s.output_tokens for s in emitted if s.model]),
            cost_usd=_sum_cost([s.cost_usd_estimate for s in emitted if s.model]),
        )
    f = outcome.finding
    citation = (
        CitationOut(instrument_ref=f.citation.instrument_ref, para=f.citation.para, quote=f.citation.quote)
        if f.citation is not None
        else None
    )
    replaced = (
        ReferenceOut(instrument_ref=f.replaced_citation.instrument_ref, para=f.replaced_citation.para)
        if f.replaced_citation is not None
        else None
    )
    return CaseRecord(
        **base,
        predicted_label=f.label,
        predicted_citation=citation,
        predicted_replaced=replaced,
        citation_assessment=assess_citation(corpus, citation, case.gold_citation),
        rationale=f.rationale,
        monitor_status=f.monitor_status,
        latency_ms=outcome.latency_ms,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        cost_usd=outcome.cost_usd_estimate,
    )


# ---- monitor_only ----------------------------------------------------------------------------------------


class _Recorder:
    """Wraps an LLM and keeps every complete_json Completion, for the monitor's tokens."""

    def __init__(self, inner: LLM) -> None:
        self._inner = inner
        self.completions: list[Completion] = []

    def complete(self, model: str, prompt: str) -> Completion:
        return self._inner.complete(model, prompt)

    def complete_json(
        self, model: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Completion]:
        value, completion = self._inner.complete_json(model, prompt, schema)
        self.completions.append(completion)
        return value, completion

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec]) -> Turn:
        return self._inner.chat(model, messages, tools)


def run_monitor_case(
    llm: LLM, es: EvalSettings, corpus: Corpus, bad: BadDraft, repeat: int, run_id: str
) -> CaseRecord:
    from argus_api.monitor import DraftFinding, MonitorError, monitor_finding

    draft = DraftFinding.model_validate(bad.draft.model_dump())
    cited = corpus.paragraph(draft.citation_instrument_ref, draft.citation_para)
    recorder = _Recorder(llm)
    base: dict[str, Any] = {
        "condition": "monitor_only",
        "case_id": bad.case_id,
        "repeat": repeat,
        "kind": "bad_draft",
        "expected_monitor_action": bad.expected_monitor_action,
        "expected_failed_check": bad.expected_failed_check,
        "monitor_active": True,
        "models": [es.monitor_model],
    }
    started = time.perf_counter()
    try:
        with call_scope(started + run_budget_s(), run_id=run_id, clause_id=bad.case_id):
            result = monitor_finding(
                recorder,
                es.monitor_model,
                corpus,
                bad.clause_text,
                draft,
                cited.text if cited is not None else "",
                run_id=run_id,
            )
    except (MonitorError, LLMError) as e:
        return CaseRecord(
            **base, error=f"{type(e).__name__}: {e}", latency_ms=round((time.perf_counter() - started) * 1000)
        )
    latency = round((time.perf_counter() - started) * 1000)
    action = result.verdict.action
    steps = [StepRecord(index=1, kind="monitor", action=action)]
    first_step, first_action = first_intervention(steps)
    inp = _sum_known([c.input_tokens for c in recorder.completions])
    out = _sum_known([c.output_tokens for c in recorder.completions])
    return CaseRecord(
        **base,
        steps=steps,
        monitor_actions=[action],
        final_action=action,
        failed_checks=[c.name for c in result.verdict.checks if not c.passed],
        first_intervention_step=first_step,
        first_intervention_action=first_action,
        rationale=result.verdict.reason,
        latency_ms=latency,
        input_tokens=inp,
        output_tokens=out,
        cost_usd=estimate_cost_usd(es.monitor_model, inp, out),
    )


# ---- driver ----------------------------------------------------------------------------------------------


def default_reviewer() -> Reviewer:
    from argus_api.agent import review_clause

    return review_clause


def run_condition(
    condition: Condition,
    *,
    llm: LLM,
    es: EvalSettings,
    corpus: Corpus,
    cases: Sequence[EvalCase] = (),
    bad_drafts: Sequence[BadDraft] = (),
    repeat: int = 1,
    run_id: str,
    reviewer: Reviewer | None = None,
    workers: int = 1,
) -> list[CaseRecord]:
    if repeat < 1 or workers < 1:
        raise EvalError(f"repeat and workers must be at least 1 (got {repeat}, {workers})")
    jobs: list[Callable[[], CaseRecord]] = []
    if condition == "monitor_only":
        if not bad_drafts:
            raise EvalError("monitor_only needs bad drafts; none were loaded")
        for rep in range(repeat):
            jobs += [partial(run_monitor_case, llm, es, corpus, b, rep, run_id) for b in bad_drafts]
    elif condition in CASE_CONDITIONS:
        if not cases:
            raise EvalError(f"{condition} needs clause or attack cases; none were loaded")
        if condition == "plain_gemini":
            for rep in range(repeat):
                jobs += [partial(run_plain_case, llm, es, corpus, c, rep, run_id) for c in cases]
        else:
            rv = reviewer if reviewer is not None else default_reviewer()
            check_reviewer(condition, rv)
            for rep in range(repeat):
                jobs += [
                    partial(run_agent_case, llm, es, corpus, c, rep, run_id, condition, rv) for c in cases
                ]
    else:
        raise EvalError(f"unknown condition {condition!r}")
    if workers == 1:
        return [job() for job in jobs]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda job: job(), jobs))
