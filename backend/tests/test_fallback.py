"""The fallback agent model (runs.py): a clause whose agent model gives up busy is reviewed again by the
fallback model, labelled, inside the unchanged run budget; nothing else ever triggers it. Whole runs through
the real agent loop and monitor over scripted FakeLLM conversations, and a stalling provider on a fake
clock."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import openai
import pytest
from openai.types.chat import ChatCompletion

from argus_api import llm as llm_module
from argus_api import runs
from argus_api.agent import AgentStep, Clause, ClauseReviewError, review_clause
from argus_api.breaker import OPEN_AFTER, OPEN_S, Breaker
from argus_api.config import FALLBACK_MODELS, Settings, model_name
from argus_api.corpus.store import Corpus, CorpusError
from argus_api.eval.settings import EvalSettings
from argus_api.llm import (
    CHAT_TIMEOUT_S,
    REQUEST_TIMEOUT_S,
    THROTTLED_ATTEMPTS,
    Completion,
    LLMError,
    Message,
    OpenRouterLLM,
    ToolSpec,
    Turn,
)
from argus_api.llm_fake import FakeLLM, turn
from argus_api.monitor import MonitorError
from argus_api.schema import AuditEvent, Finding, Limits, Run, RunStatus, RunSummary, StepKind
from argus_api.store_fake import MemoryStore
from tests.run_support import (
    AGENT_MODEL,
    MONITOR_MODEL,
    ClauseRoutedLLM,
    draft_reply,
    fixture_corpus,
    gold_clause_llm,
    judgement,
    policy_llm,
    settings,
    vertex_settings,
)

OWNER = "uid-owner"
FALLBACK = FALLBACK_MODELS["openrouter"]
POLICY = "policy_a"
HAND_OFF = f"{model_name(AGENT_MODEL)} is busy; handing this clause to {model_name(FALLBACK)}."


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return fixture_corpus()


@pytest.fixture()
def store() -> MemoryStore:
    s = MemoryStore()
    s.set(
        runs.LIMITS_PATH,
        Limits(daily_run_cap=9, hourly_run_cap=9, per_user_daily_run_cap=9, enabled=True).model_dump(),
    )
    return s


@pytest.fixture(autouse=True)
def no_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("argus_api.llm._jitter", lambda: 1.0)


def with_fallback(s: Settings | None = None, fallback: str = FALLBACK) -> Settings:
    return Settings(**{**(s or settings()).__dict__, "fallback_model": fallback})


def new_run_id() -> str:
    import uuid

    return uuid.uuid4().hex


def busy(model: str = AGENT_MODEL, kind: Any = "throttled") -> LLMError:
    """A give-up after a 429 on every try, as llm._with_retries raises it."""
    return LLMError(
        f"OpenRouter {model} failed after {THROTTLED_ATTEMPTS} tries: 429",
        kind=kind,
        model=model,
        attempts=THROTTLED_ATTEMPTS,
        failures=(kind,) * THROTTLED_ATTEMPTS,
    )


def run_policy(
    store: MemoryStore,
    corpus: Corpus,
    llm: Any,
    s: Settings | None = None,
    policy: runs.Policy | None = None,
    **kw: Any,
) -> Run:
    kw.setdefault("breaker", Breaker(AGENT_MODEL))
    runner = runs.Runner(store, llm, s or with_fallback(), corpus, **kw)
    return runner.run_review(new_run_id(), OWNER, policy or runs.load_policy(POLICY))


def findings(store: MemoryStore, run_id: str) -> dict[str, Finding]:
    return {i: Finding.model_validate(d) for i, d in store.list_docs(f"{runs.run_path(run_id)}/findings")}


def steps_of(store: MemoryStore, run_id: str, clause_id: str) -> list[AgentStep]:
    steps = [AgentStep.model_validate(d) for _, d in store.list_docs(f"{runs.run_path(run_id)}/steps")]
    return sorted((s for s in steps if s.clause_id == clause_id), key=lambda s: s.seq)


def audit(store: MemoryStore, run_id: str) -> list[AuditEvent]:
    return [AuditEvent.model_validate(d) for _, d in store.list_docs(f"{runs.run_path(run_id)}/audit")]


def models_called(llm: FakeLLM) -> list[tuple[str, str]]:
    return [(c.method, c.model) for c in llm.calls]


def gold_after(first: BaseException, clause_id: str) -> FakeLLM:
    """The agent model's first call fails with `first`; then the clause's gold conversation."""
    return FakeLLM(
        turns=[first, turn(text="Ready to draft.")],
        json_replies=[draft_reply(POLICY, clause_id), judgement()],
    )


# ---- a 429 storm: the clause is handed over, labelled, and counted -------------------------------------


def test_a_busy_agent_model_hands_the_clause_to_the_fallback(store: MemoryStore, corpus: Corpus) -> None:
    cid = "c3"
    script = gold_after(busy(), cid)
    llm = policy_llm(POLICY, [(cid, script)])
    run = run_policy(store, corpus, llm)

    assert run.status == RunStatus.DONE and run.error is None
    assert run.agent_model == AGENT_MODEL  # the run keeps its preferred model
    assert run.summary.fallback_clauses == 1 and run.summary.failed == 0
    assert models_called(script) == [
        ("chat", AGENT_MODEL),
        ("chat", FALLBACK),
        ("complete_json", FALLBACK),
        ("complete_json", MONITOR_MODEL),  # the same monitor model
    ]
    found = findings(store, run.run_id)
    f = found[cid]
    assert f.error is None and f.citation is not None  # the same citation checks built it
    assert f.agent_model == FALLBACK
    assert f.fallback_reason == f"Gemini 3.8 Flash was busy (HTTP 429) after {THROTTLED_ATTEMPTS} tries"
    others = [found[c] for c in found if c != cid]
    assert all(o.agent_model == AGENT_MODEL and o.fallback_reason is None for o in others)

    steps = steps_of(store, run.run_id, cid)
    hand = [i for i, s in enumerate(steps) if s.summary == HAND_OFF]
    assert len(hand) == 1 and steps[hand[0]].kind == StepKind.FINAL
    assert not any("failed" in s.summary for s in steps)
    assert {s.model for s in steps[hand[0] + 1 :] if s.kind != StepKind.MONITOR and s.model} == {FALLBACK}
    assert steps[-1].kind == StepKind.FINAL and steps[-1].summary.startswith(
        f"Final finding for clause {cid}"
    )

    rows = [a for a in audit(store, run.run_id) if a.event == "finding_recorded"]
    mine = next(a for a in rows if a.clause_id == cid)
    assert mine.model == FALLBACK and f.fallback_reason is not None and f.fallback_reason in mine.detail
    assert {a.model for a in rows if a.clause_id != cid} == {AGENT_MODEL}
    assert any(a.event == "step_final" and a.detail == HAND_OFF for a in audit(store, run.run_id))


def test_a_run_with_a_fallback_clause_is_never_a_saved_sample(store: MemoryStore, corpus: Corpus) -> None:
    llm = policy_llm(POLICY, [("c1", gold_after(busy(vertex_settings().agent_model), "c1"))])
    run = run_policy(store, corpus, llm, with_fallback(vertex_settings(), FALLBACK_MODELS["vertex"]))
    assert run.status == RunStatus.DONE and run.summary.fallback_clauses == 1
    assert run.agent_model == vertex_settings().agent_model and run.provider == runs.SAMPLE_PROVIDER
    reason = runs.sample_refusal(run)
    assert reason is not None and "fallback model" in reason
    with pytest.raises(runs.NotASample, match="fallback model"):
        runs.save_sample(store, run.run_id)
    assert store.get(runs.run_path(run.run_id, runs.SAMPLES)) is None
    assert runs.sample_refusal(run.model_copy(update={"summary": RunSummary(clauses=8)})) is None


# ---- a 504 stall on the agent model, on a fake clock ---------------------------------------------------

REQ = httpx2.Request("POST", "https://openrouter.test/chat/completions")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Provider:
    """An OpenAI-compatible client whose every request takes `took(timeout)` seconds and then fails."""

    def __init__(self, clock: Clock, took: Callable[[float], float], error: Callable[[], Exception]) -> None:
        self.clock, self.took, self.error = clock, took, error
        self.timeouts: list[float] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> Any:
        self.timeouts.append(kwargs["timeout"])
        self.clock.now += self.took(kwargs["timeout"])
        raise self.error()


class SlowPrimary:
    """The agent model goes through the real OpenRouter adapter (retries, attempt timeouts, call_scope) on a
    provider that never answers; every other model is the scripted FakeLLM."""

    def __init__(self, provider: _Provider, script: FakeLLM) -> None:
        self.provider = provider
        self.real = OpenRouterLLM(None, client=provider, sleep=provider.clock.sleep, clock=provider.clock)  # type: ignore[arg-type]
        self.script = script

    def complete(self, model: str, prompt: str) -> Completion:
        return (self.real if model == AGENT_MODEL else self.script).complete(model, prompt)

    def complete_json(
        self, model: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Completion]:
        return (self.real if model == AGENT_MODEL else self.script).complete_json(model, prompt, schema)

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec]) -> Turn:
        return (self.real if model == AGENT_MODEL else self.script).chat(model, messages, tools)


def one_clause(clause_id: str = "c1") -> runs.Policy:
    policy = runs.load_policy(POLICY)
    return policy.model_copy(update={"clauses": [c for c in policy.clauses if c.id == clause_id]})


@pytest.mark.parametrize(
    ("took", "error", "timeouts", "reason"),
    [
        # A 504-like stall: every try runs to its timeout. Try 1 gets the chat timeout (the provider's
        # failure), try 2 what is left of the agent model's share after the backoff, and our share cuts it,
        # so the reason names the share, not a second provider failure; handed over at PRIMARY_PHASE_S.
        (
            lambda timeout: timeout,
            lambda: openai.APITimeoutError(request=REQ),
            [CHAT_TIMEOUT_S, runs.PRIMARY_PHASE_S - CHAT_TIMEOUT_S - 1.0],
            "Gemini 3.8 Flash did not answer (timeouts or HTTP 5xx) and no retry fitted in its "
            f"{runs.PRIMARY_PHASE_S:g} s share of the run budget",
        ),
        # A 429 that took most of the share: no retry fits; the agent model ran out of time, a 429 behind it.
        (
            lambda timeout: runs.PRIMARY_PHASE_S - 2.0,
            lambda: openai.RateLimitError("busy", response=httpx2.Response(429, request=REQ), body=None),
            [CHAT_TIMEOUT_S],
            "Gemini 3.8 Flash was busy (HTTP 429) and no retry fitted in its "
            f"{runs.PRIMARY_PHASE_S:g} s share of the run budget",
        ),
    ],
    ids=["504_stall", "slow_429"],
)
def test_a_stalled_agent_model_is_cut_at_its_share_and_the_fallback_finishes_in_budget(
    store: MemoryStore,
    corpus: Corpus,
    took: Callable[[float], float],
    error: Callable[[], Exception],
    timeouts: list[float],
    reason: str,
) -> None:
    clock = Clock()
    provider = _Provider(clock, took, error)
    script = gold_clause_llm(POLICY, "c1")
    start = clock()
    run = run_policy(store, corpus, SlowPrimary(provider, script), policy=one_clause(), clock=clock)

    assert provider.timeouts == pytest.approx(timeouts)
    assert clock() - start <= runs.PRIMARY_PHASE_S  # the agent model's calls ended inside its share
    assert runs.RUN_BUDGET_S - (clock() - start) >= runs.MIN_FALLBACK_S
    assert run.status == RunStatus.DONE and run.summary.fallback_clauses == 1
    f = findings(store, run.run_id)["c1"]
    assert (f.agent_model, f.fallback_reason) == (FALLBACK, reason)
    assert HAND_OFF in [s.summary for s in steps_of(store, run.run_id, "c1")]
    assert script.remaining() == {"complete": 0, "complete_json": 0, "chat": 0}


# ---- no fallback for anything but a busy agent model ----------------------------------------------------


@pytest.mark.parametrize(
    ("script", "busy_failure"),
    [
        (FakeLLM(turns=[LLMError("OpenRouter m failed: 400", kind="fatal", model=AGENT_MODEL)]), False),
        (
            FakeLLM(turns=[turn(text="Ready.")], json_replies=[LLMError("returned invalid JSON 2 times")]),
            False,
        ),
        (
            FakeLLM(
                turns=[turn(text="Ready.")], json_replies=[draft_reply(POLICY, "c4"), busy(MONITOR_MODEL)]
            ),
            True,  # the monitor model was busy: a provider failure, but the agent model is not the one
        ),
        (
            FakeLLM(
                turns=[turn(text="Ready.")], json_replies=[draft_reply(POLICY, "c4"), MonitorError("bad")]
            ),
            False,
        ),
        (FakeLLM(turns=[CorpusError("library missing a paragraph")]), False),
        (FakeLLM(turns=[busy(FALLBACK)]), True),  # a busy model that is not the run's agent model
    ],
    ids=["fatal_400", "invalid_json", "monitor_busy", "monitor_error", "corpus_error", "other_model_busy"],
)
def test_only_a_busy_agent_model_triggers_the_fallback(
    store: MemoryStore, corpus: Corpus, script: FakeLLM, busy_failure: bool
) -> None:
    run = run_policy(store, corpus, policy_llm(POLICY, [("c4", script)]))
    assert FALLBACK not in {m for _, m in models_called(script)}
    assert run.summary.failed == 1 and run.summary.fallback_clauses == 0
    f = findings(store, run.run_id)["c4"]
    assert f.error is not None and f.fallback_reason is None
    assert f.error.startswith(runs.PROVIDER_BUSY) == busy_failure
    final = steps_of(store, run.run_id, "c4")[-1]
    assert final.kind == StepKind.FINAL and "failed" in final.summary and HAND_OFF != final.summary


def test_no_fallback_with_too_little_of_the_run_budget_left(store: MemoryStore, corpus: Corpus) -> None:
    script = FakeLLM(turns=[busy()])
    run = run_policy(store, corpus, policy_llm(POLICY, [("c4", script)]), budget_s=runs.MIN_FALLBACK_S - 1)
    assert models_called(script) == [("chat", AGENT_MODEL)]
    f = findings(store, run.run_id)["c4"]
    assert f.error is not None and f.error.startswith(runs.PROVIDER_BUSY) and f.fallback_reason is None
    assert run.summary.fallback_clauses == 0


def test_without_a_fallback_model_a_busy_clause_fails_as_before(store: MemoryStore, corpus: Corpus) -> None:
    script = FakeLLM(turns=[busy()])
    run = run_policy(store, corpus, policy_llm(POLICY, [("c4", script)]), settings())
    assert models_called(script) == [("chat", AGENT_MODEL)]
    f = findings(store, run.run_id)["c4"]
    assert f.error is not None and f.error.startswith(runs.PROVIDER_BUSY) and "LLMError" in f.error
    assert run.status == RunStatus.DONE and run.summary.failed == 1  # the other clauses were reviewed


def test_an_admission_429_reviews_nothing(store: MemoryStore, corpus: Corpus) -> None:
    store.set(
        runs.LIMITS_PATH,
        Limits(daily_run_cap=0, hourly_run_cap=9, per_user_daily_run_cap=9, enabled=True).model_dump(),
    )
    llm = policy_llm(POLICY)
    with pytest.raises(runs.DailyCapReached):
        runs.Runner(store, llm, with_fallback(), corpus, breaker=Breaker(AGENT_MODEL)).run_review(
            new_run_id(), OWNER, runs.load_policy(POLICY)
        )
    assert all(r == {"complete": 0, "complete_json": 2, "chat": 1} for r in llm.remaining().values())


# ---- the fallback fails too -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("second", "busy_failure"),
    [(busy(FALLBACK), True), (LLMError("OpenRouter lite failed: 400", kind="fatal", model=FALLBACK), False)],
    ids=["fallback_busy", "fallback_fatal"],
)
def test_when_the_fallback_fails_too_the_clause_fails(
    store: MemoryStore, corpus: Corpus, second: LLMError, busy_failure: bool
) -> None:
    script = FakeLLM(turns=[busy(), second])
    run = run_policy(store, corpus, policy_llm(POLICY, [("c4", script)]))
    assert models_called(script) == [("chat", AGENT_MODEL), ("chat", FALLBACK)]
    assert run.status == RunStatus.DONE and run.summary.failed == 1 and run.summary.fallback_clauses == 0
    assert run.error is not None and run.error.startswith(f"1 of {run.summary.clauses} clauses failed: c4: ")
    f = findings(store, run.run_id)["c4"]
    assert f.error is not None and f.error.startswith(runs.PROVIDER_BUSY) == busy_failure
    assert f"after {THROTTLED_ATTEMPTS} tries, so Gemini 3.1 Flash-Lite reviewed it again" in f.error
    summaries = [s.summary for s in steps_of(store, run.run_id, "c4")]
    assert summaries.count(HAND_OFF) == 1 and "failed" in summaries[-1]


# ---- the circuit breaker in a run -----------------------------------------------------------------------


class Ticks:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_busy_give_ups_open_the_breaker_and_then_clauses_go_straight_to_the_fallback(
    store: MemoryStore, corpus: Corpus, caplog: pytest.LogCaptureFixture
) -> None:
    ticks = Ticks()
    breaker = Breaker(AGENT_MODEL, clock=ticks)
    policy = runs.load_policy(POLICY)
    # Only the stormy clauses, so no clause of this run starts after the breaker opens.
    stormy = policy.model_copy(update={"clauses": policy.clauses[:OPEN_AFTER]})
    llm = ClauseRoutedLLM(stormy, {c.id: gold_after(busy(), c.id) for c in stormy.clauses})
    with caplog.at_level("INFO", logger="argus.breaker"):
        first = run_policy(store, corpus, llm, policy=stormy, breaker=breaker)
    assert first.summary.fallback_clauses == OPEN_AFTER and breaker.is_open
    assert [r.getMessage() for r in caplog.records if r.name == "argus.breaker"] == ["llm_breaker_open"]

    second_llm = policy_llm(POLICY)
    second = run_policy(store, corpus, second_llm, breaker=breaker)
    clauses = runs.load_policy(POLICY).clauses
    assert second.status == RunStatus.DONE and second.summary.fallback_clauses == len(clauses)
    called = {m for script in second_llm.scripts.values() for _, m in models_called(script)}
    assert AGENT_MODEL not in called and FALLBACK in called
    for cid, f in findings(store, second.run_id).items():
        assert f.agent_model == FALLBACK
        # The web line names the backup model itself: "Reviewed by Gemini 3.1 Flash-Lite because ...".
        assert f.fallback_reason == "Gemini 3.8 Flash was busy on recent clauses"
        assert (
            steps_of(store, second.run_id, cid)[0].summary
            == "Gemini 3.8 Flash is busy; reviewing this clause with Gemini 3.1 Flash-Lite."
        )


def test_after_the_open_time_a_trial_clause_on_the_agent_model_closes_the_breaker(
    store: MemoryStore, corpus: Corpus
) -> None:
    ticks = Ticks()
    breaker = Breaker(AGENT_MODEL, clock=ticks)
    for _ in range(OPEN_AFTER):
        breaker.report("primary", "busy")
    ticks.now += OPEN_S
    script = gold_clause_llm(POLICY, "c1")
    run = run_policy(store, corpus, script, policy=one_clause(), breaker=breaker)
    assert {m for _, m in models_called(script)} == {AGENT_MODEL, MONITOR_MODEL}
    assert findings(store, run.run_id)["c1"].agent_model == AGENT_MODEL
    assert not breaker.is_open


def test_a_busy_trial_clause_still_gets_the_fallback_and_reopens_the_breaker(
    store: MemoryStore, corpus: Corpus
) -> None:
    ticks = Ticks()
    breaker = Breaker(AGENT_MODEL, clock=ticks)
    for _ in range(OPEN_AFTER):
        breaker.report("primary", "busy")
    ticks.now += OPEN_S
    run = run_policy(store, corpus, gold_after(busy(), "c1"), policy=one_clause(), breaker=breaker)
    assert findings(store, run.run_id)["c1"].agent_model == FALLBACK
    assert breaker.is_open and breaker.route() == "fallback"


# ---- the agent loop: the model comes from Settings, the hand-over wording from the caller ---------------


def test_the_loop_records_the_model_that_wrote_the_finding(corpus: Corpus) -> None:
    clause = Clause(id="c1", text=runs.load_policy(POLICY).clauses[0].text)
    for model in (AGENT_MODEL, FALLBACK):
        s = Settings(**{**settings().__dict__, "agent_model": model})
        llm = gold_clause_llm(POLICY, "c1")
        outcome = review_clause(llm, s, corpus, clause, lambda step: None)
        assert outcome.finding.agent_model == model and outcome.finding.fallback_reason is None
        assert models_called(llm)[0] == ("chat", model)


@pytest.mark.parametrize("words", [None, "Handing over."])
def test_the_final_step_of_a_failed_review_is_the_callers_hand_over_when_it_gives_one(
    corpus: Corpus, words: str | None
) -> None:
    clause = Clause(id="c1", text=runs.load_policy(POLICY).clauses[0].text)
    steps: list[AgentStep] = []
    asked: list[LLMError] = []

    def hand_off(e: LLMError) -> str | None:
        asked.append(e)
        return words

    error = busy()
    with pytest.raises(ClauseReviewError) as raised:
        review_clause(FakeLLM(turns=[error]), settings(), corpus, clause, steps.append, hand_off=hand_off)
    assert asked == [error] and raised.value.__cause__ is error
    assert steps[-1].summary == (words or "Review of clause c1 failed at the search step: LLMError.")


def test_the_loop_never_asks_to_hand_over_other_failures(corpus: Corpus) -> None:
    clause = Clause(id="c1", text=runs.load_policy(POLICY).clauses[0].text)
    asked: list[LLMError] = []
    with pytest.raises(ClauseReviewError):
        review_clause(
            FakeLLM(turns=[CorpusError("x")]),
            settings(),
            corpus,
            clause,
            lambda s: None,
            hand_off=lambda e: asked.append(e) or "x",
        )
    assert asked == []


# ---- provenance fields: old documents still validate; the eval keeps the fallback off -------------------


def test_documents_written_before_the_fallback_fields_still_validate() -> None:
    old = {
        "clause_id": "c1",
        "clause_text": "t",
        "label": "gap",
        "rationale": "r",
        "citation": None,
        "monitor_status": "passed",
    }
    f = Finding.model_validate(old)
    assert (f.agent_model, f.fallback_reason) == (None, None)
    assert RunSummary.model_validate({"clauses": 8, "failed": 0}).fallback_clauses == 0
    assert (
        json.loads(Finding.model_validate({**old, "agent_model": FALLBACK}).model_dump_json())["agent_model"]
        == FALLBACK
    )


def test_the_evaluation_measures_one_fixed_model(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ARGUS_AGENT_MODEL", "ARGUS_MONITOR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARGUS_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ARGUS_FALLBACK_MODEL", FALLBACK)
    assert EvalSettings.from_env().settings.fallback_model is None
    assert EvalSettings.from_settings(with_fallback()).settings.fallback_model is None


# ---- the agent model's share of the time cuts only the agent model's calls ------------------------------


class ScriptedProvider:
    """An OpenAI-compatible client on a fake clock, for every model: a request to `model` takes the next of
    `took[model]` seconds (0 when none is left) and then answers from the FakeLLM script, or times out when
    that is not less than the attempt's timeout. Each request is recorded as (model, timeout, sent at)."""

    def __init__(self, clock: Clock, script: FakeLLM, took: dict[str, list[float]]) -> None:
        self.clock, self.script, self.took = clock, script, took
        self.requests: list[tuple[str, float, float]] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> Any:
        model, timeout = kwargs["model"], kwargs["timeout"]
        self.requests.append((model, timeout, self.clock()))
        delays = self.took.get(model) or []
        delay = delays.pop(0) if delays else 0.0
        if delay >= timeout:
            self.clock.now += timeout
            raise openai.APITimeoutError(request=REQ)
        self.clock.now += delay
        message: dict[str, Any] = {"role": "assistant"}
        if "response_format" in kwargs:
            schema = kwargs["response_format"]["json_schema"]["schema"]
            value, _ = self.script.complete_json(model, kwargs["messages"][-1]["content"], schema)
            message["content"] = json.dumps(value)
        else:
            t = self.script.chat(model, [], [])
            message["content"] = t.text
            assert not t.tool_calls, "ScriptedProvider scripts turns without tool calls"
        return ChatCompletion.model_validate(
            {
                "id": "gen-1",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 30, "total_tokens": 40},
            }
        )


def clocked_llm(clock: Clock, script: FakeLLM, took: dict[str, list[float]]) -> tuple[OpenRouterLLM, Any]:
    provider = ScriptedProvider(clock, script, took)
    return OpenRouterLLM(None, client=provider, sleep=clock.sleep, clock=clock), provider  # type: ignore[arg-type]


def sent(provider: ScriptedProvider, model: str) -> list[tuple[float, float]]:
    """(timeout, seconds after `start`) of each request to `model`."""
    return [(timeout, at) for m, timeout, at in provider.requests if m == model]


def test_a_slow_but_correct_draft_is_still_judged_on_the_agent_model(
    store: MemoryStore, corpus: Corpus
) -> None:
    """The draft lands about 34 s into the clause, just inside the agent model's share: the monitor model
    still gets a whole attempt (it keeps the run deadline), and the agent model's finding stands."""
    clock = Clock()
    start = clock()
    llm, provider = clocked_llm(
        clock, gold_clause_llm(POLICY, "c1"), {AGENT_MODEL: [10.0, 24.0], MONITOR_MODEL: [3.0]}
    )
    breaker = Breaker(AGENT_MODEL)
    run = run_policy(store, corpus, llm, policy=one_clause(), clock=clock, breaker=breaker)

    ((monitor_timeout, monitor_at),) = sent(provider, MONITOR_MODEL)
    assert monitor_at - start == pytest.approx(34.0) and monitor_at - start > runs.PRIMARY_PHASE_S - 2.0
    assert monitor_timeout == pytest.approx(REQUEST_TIMEOUT_S)  # not cut to the agent model's last second
    assert run.status == RunStatus.DONE and run.summary.fallback_clauses == 0
    f = findings(store, run.run_id)["c1"]
    assert (f.agent_model, f.fallback_reason, f.error) == (AGENT_MODEL, None, None)
    assert FALLBACK not in {m for m, _, _ in provider.requests}
    assert HAND_OFF not in [s.summary for s in steps_of(store, run.run_id, "c1")]
    assert breaker.state == "closed"


def revise_script(corpus: Corpus) -> FakeLLM:
    """Clause c1 with one revision: the first draft cites a paragraph the library does not have, so the
    monitor asks for a revision; the revised draft is the gold finding and passes."""
    first = draft_reply(POLICY, "c1")
    first["citation_para"] += "(99)"
    assert corpus.paragraph(first["citation_instrument_ref"], first["citation_para"]) is None
    return FakeLLM(
        turns=[turn(text="Ready to draft.")],
        json_replies=[first, judgement(), draft_reply(POLICY, "c1"), judgement()],
    )


@pytest.mark.parametrize("monitor_took", [27.0, 5.0], ids=["revise_at_61s", "revise_at_39s"])
def test_a_revise_after_the_agent_models_share_still_runs_on_the_agent_model(
    store: MemoryStore, corpus: Corpus, monitor_took: float
) -> None:
    """The draft lands at 34 s, just inside the agent model's share, and the monitor asks for a revision after
    the share has ended (at 61 s, when too little of the run is left for a hand-over; or at 39 s, when a
    hand-over would throw the finished draft and verdict away). The agent model's share ends with its draft:
    the revise call keeps the run deadline and finishes on the agent model, with no hand-over."""
    clock = Clock()
    start = clock()
    script = revise_script(corpus)
    took = {AGENT_MODEL: [10.0, 24.0, 6.0], MONITOR_MODEL: [monitor_took, 3.0]}
    llm, provider = clocked_llm(clock, script, took)
    breaker = Breaker(AGENT_MODEL)
    run = run_policy(store, corpus, llm, policy=one_clause(), clock=clock, breaker=breaker)

    revise_at = 34.0 + monitor_took
    (_, _), (_, draft_at), (revise_timeout, revise_sent) = sent(provider, AGENT_MODEL)
    assert draft_at - start == pytest.approx(10.0)
    assert revise_sent - start == pytest.approx(revise_at) and revise_at > runs.PRIMARY_PHASE_S
    # Cut only by the run deadline (29 s left at 61 s), not refused for the agent model's spent share.
    assert revise_timeout == pytest.approx(min(REQUEST_TIMEOUT_S, runs.RUN_BUDGET_S - revise_at))
    assert clock() - start <= runs.RUN_BUDGET_S

    assert run.status == RunStatus.DONE and run.summary.fallback_clauses == 0 and run.summary.failed == 0
    assert run.summary.monitor_revisions == 1
    f = findings(store, run.run_id)["c1"]
    assert (f.agent_model, f.fallback_reason, f.error) == (AGENT_MODEL, None, None)
    assert FALLBACK not in {m for m, _, _ in provider.requests}
    summaries = [s.summary for s in steps_of(store, run.run_id, "c1")]
    assert not any("handing this clause" in s for s in summaries)
    assert breaker.state == "closed"
    assert script.remaining() == {"complete": 0, "complete_json": 0, "chat": 0}


def test_the_agent_model_is_cut_at_its_share_and_the_fallback_gets_the_rest_of_the_run(
    store: MemoryStore, corpus: Corpus
) -> None:
    """The agent model's draft is still running when its share ends: the call is cut there (our deadline,
    no provider failure), the clause is handed over, and the fallback model and the monitor work to the run
    deadline, well past the agent model's share."""
    clock = Clock()
    start = clock()
    script = FakeLLM(
        turns=[turn(text="Ready to draft."), turn(text="Ready to draft.")],
        json_replies=[draft_reply(POLICY, "c1"), judgement()],
    )
    took = {AGENT_MODEL: [10.0, 60.0], FALLBACK: [20.0, 25.0], MONITOR_MODEL: [5.0]}
    llm, provider = clocked_llm(clock, script, took)
    breaker = Breaker(AGENT_MODEL)
    run = run_policy(store, corpus, llm, policy=one_clause(), clock=clock, breaker=breaker)

    (_, agent_chat), (draft_timeout, draft_at) = sent(provider, AGENT_MODEL)
    assert draft_at - start == pytest.approx(10.0)
    assert draft_timeout == pytest.approx(runs.PRIMARY_PHASE_S - 10.0)  # cut to the end of its share
    (fb_chat_timeout, fb_chat_at), (fb_draft_timeout, _) = sent(provider, FALLBACK)
    assert fb_chat_at - start == pytest.approx(runs.PRIMARY_PHASE_S)
    assert (fb_chat_timeout, fb_draft_timeout) == pytest.approx((CHAT_TIMEOUT_S, REQUEST_TIMEOUT_S))
    ((monitor_timeout, monitor_at),) = sent(provider, MONITOR_MODEL)
    assert monitor_at - start == pytest.approx(runs.PRIMARY_PHASE_S + 45.0)  # 80 s: past the share
    assert monitor_timeout == pytest.approx(runs.RUN_BUDGET_S - 80.0)  # cut only by the run deadline
    assert clock() - start <= runs.RUN_BUDGET_S

    assert run.status == RunStatus.DONE and run.summary.fallback_clauses == 1
    f = findings(store, run.run_id)["c1"]
    assert f.agent_model == FALLBACK and f.error is None
    assert f.fallback_reason == (
        "Gemini 3.8 Flash was too slow to finish within its "
        f"{runs.PRIMARY_PHASE_S:g} s share of the run budget"
    )
    summaries = [s.summary for s in steps_of(store, run.run_id, "c1")]
    assert "Gemini 3.8 Flash ran out of time; handing this clause to Gemini 3.1 Flash-Lite." in summaries
    assert breaker.state == "closed"  # our deadline cut it: not a busy give-up
    assert script.remaining() == {"complete": 0, "complete_json": 0, "chat": 0}


# ---- a pure budget cut is not the provider's fault ------------------------------------------------------


def out_of_time(model: str = AGENT_MODEL) -> LLMError:
    """Our deadline ended the call and the provider never failed (llm.py: kind deadline, no cause)."""
    return LLMError(
        f"OpenRouter {model}: attempt 1 not started, 1.0 s left before the deadline",
        kind="deadline",
        model=model,
    )


def busy_then_out_of_time(model: str = AGENT_MODEL) -> LLMError:
    """A 429 on the first try, then no retry fitted before the deadline (kind deadline, cause throttled)."""
    return LLMError(
        f"OpenRouter {model} failed on try 1: 429; not retried",
        kind="deadline",
        model=model,
        cause="throttled",
        attempts=1,
        failures=("throttled",),
    )


def test_a_clause_that_only_ran_out_of_time_is_not_reported_as_busy(
    store: MemoryStore, corpus: Corpus
) -> None:
    script = FakeLLM(turns=[out_of_time()])
    run = run_policy(store, corpus, policy_llm(POLICY, [("c4", script)]), settings())
    f = findings(store, run.run_id)["c4"]
    assert f.error is not None and f.error.startswith(runs.OUT_OF_TIME)
    assert runs.PROVIDER_BUSY not in f.error


@pytest.mark.parametrize(
    ("first", "second", "starts"),
    [
        # The agent model only ran out of its share; then the fallback ran out of the run: too slow, ours.
        (out_of_time(), out_of_time(FALLBACK), runs.OUT_OF_TIME),
        # A 429 sent the clause to the fallback, which then ran out of time: the provider's doing.
        (busy(), out_of_time(FALLBACK), runs.PROVIDER_BUSY),
        (busy_then_out_of_time(), out_of_time(FALLBACK), runs.PROVIDER_BUSY),
        # The fallback's own 429 behind its deadline: the provider's.
        (out_of_time(), busy_then_out_of_time(FALLBACK), runs.PROVIDER_BUSY),
    ],
    ids=["cut_then_cut", "busy_then_cut", "busy_deadline_then_cut", "cut_then_busy_deadline"],
)
def test_a_failed_fallback_says_whose_fault_it_was(
    store: MemoryStore, corpus: Corpus, first: LLMError, second: LLMError, starts: str
) -> None:
    script = FakeLLM(turns=[first, second])
    run = run_policy(store, corpus, policy_llm(POLICY, [("c4", script)]))
    assert models_called(script) == [("chat", AGENT_MODEL), ("chat", FALLBACK)]
    f = findings(store, run.run_id)["c4"]
    assert f.error is not None and f.error.startswith(starts)
    other = runs.PROVIDER_BUSY if starts == runs.OUT_OF_TIME else runs.OUT_OF_TIME
    assert not f.error.startswith(other)


@pytest.mark.parametrize(
    ("error", "reason", "step"),
    [
        (
            out_of_time(),
            f"Gemini 3.8 Flash was too slow to finish within its {runs.PRIMARY_PHASE_S:g} s share of the run "
            "budget",
            "Gemini 3.8 Flash ran out of time; handing this clause to Gemini 3.1 Flash-Lite.",
        ),
        (
            busy_then_out_of_time(),
            "Gemini 3.8 Flash was busy (HTTP 429) and no retry fitted in its "
            f"{runs.PRIMARY_PHASE_S:g} s share of the run budget",
            HAND_OFF,
        ),
    ],
    ids=["pure_cut", "busy_cut"],
)
def test_a_deadline_hand_over_names_its_cause(
    store: MemoryStore, corpus: Corpus, error: LLMError, reason: str, step: str
) -> None:
    run = run_policy(store, corpus, policy_llm(POLICY, [("c4", gold_after(error, "c4"))]))
    f = findings(store, run.run_id)["c4"]
    assert (f.agent_model, f.fallback_reason) == (FALLBACK, reason)
    assert step in [s.summary for s in steps_of(store, run.run_id, "c4")]


# ---- the breaker counts only the agent model's provider failures ----------------------------------------


def test_failures_that_are_not_the_agent_models_provider_failing_leave_the_breaker_closed(
    store: MemoryStore, corpus: Corpus
) -> None:
    """More than OPEN_AFTER clauses fail, or run out of the agent model's share, in one run, none because
    the provider failed the agent model: the breaker stays closed and the next clause starts on it."""
    policy = runs.load_policy(POLICY)
    ids = [c.id for c in policy.clauses[:5]]
    scripts = {
        ids[0]: FakeLLM(turns=[LLMError("OpenRouter m failed: 400", kind="fatal", model=AGENT_MODEL)]),
        ids[1]: FakeLLM(
            turns=[turn(text="Ready.")], json_replies=[LLMError("returned invalid JSON 2 times")]
        ),
        ids[2]: FakeLLM(turns=[CorpusError("library missing a paragraph")]),
        ids[3]: FakeLLM(
            turns=[turn(text="Ready.")], json_replies=[draft_reply(POLICY, ids[3]), busy(MONITOR_MODEL)]
        ),
        ids[4]: gold_after(out_of_time(), ids[4]),  # handed over, reviewed by the fallback
    }
    assert len(scripts) > OPEN_AFTER
    stormy = policy.model_copy(update={"clauses": policy.clauses[:5]})
    breaker = Breaker(AGENT_MODEL, clock=Ticks())
    run = run_policy(store, corpus, ClauseRoutedLLM(stormy, scripts), policy=stormy, breaker=breaker)
    assert run.summary.failed == 4 and run.summary.fallback_clauses == 1
    assert breaker.state == "closed" and breaker.route() == "primary"


# ---- a busy monitor model: the real monitor path, no fallback, the provider's fault ---------------------


def test_a_busy_monitor_model_is_the_providers_fault_and_has_no_fallback(
    store: MemoryStore, corpus: Corpus
) -> None:
    """monitor/core.py lets the monitor model's LLMError through bare (not as MonitorError), so the failure is
    classified by its own kind and model: PROVIDER_BUSY, no hand-over (the monitor has no backup), and not a
    busy give-up of the agent model."""

    def script() -> FakeLLM:
        return FakeLLM(
            turns=[turn(text="Ready.")], json_replies=[draft_reply(POLICY, "c4"), busy(MONITOR_MODEL)]
        )

    clause = Clause(id="c4", text=next(c.text for c in runs.load_policy(POLICY).clauses if c.id == "c4"))
    with pytest.raises(ClauseReviewError) as raised:
        review_clause(script(), settings(), corpus, clause, lambda s: None)
    cause = raised.value.__cause__
    assert isinstance(cause, LLMError) and not isinstance(cause, MonitorError)
    assert (cause.kind, cause.model, cause.capacity) == ("throttled", MONITOR_MODEL, True)
    assert runs.busy_cause(raised.value) is cause

    # OPEN_AFTER runs of it: the agent model answered every time, so the breaker never opens.
    breaker = Breaker(AGENT_MODEL, clock=Ticks())
    for _ in range(OPEN_AFTER):
        scripted = script()
        run = run_policy(store, corpus, policy_llm(POLICY, [("c4", scripted)]), breaker=breaker)
        assert models_called(scripted) == [
            ("chat", AGENT_MODEL),
            ("complete_json", AGENT_MODEL),
            ("complete_json", MONITOR_MODEL),
        ]
        f = findings(store, run.run_id)["c4"]
        assert f.error is not None and f.error.startswith(runs.PROVIDER_BUSY) and f.fallback_reason is None
        assert run.summary.fallback_clauses == 0
    assert breaker.state == "closed"


# ---- one instant for the run's deadline, wait and latency ----------------------------------------------


class SlowCommitStore(MemoryStore):
    """A store whose commits take `seconds` on the fake clock (a slow Firestore write)."""

    def __init__(self, clock: Clock, seconds: float) -> None:
        super().__init__()
        self.clock, self.seconds = clock, seconds

    def commit(self, writes: list[Any]) -> None:
        self.clock.now += self.seconds
        super().commit(writes)


def test_the_model_call_deadline_starts_when_the_run_does_not_after_its_first_write(corpus: Corpus) -> None:
    clock = Clock()
    store = SlowCommitStore(clock, 5.0)
    store.set(
        runs.LIMITS_PATH,
        Limits(daily_run_cap=9, hourly_run_cap=9, per_user_daily_run_cap=9, enabled=True).model_dump(),
    )
    seen: list[float | None] = []

    def reviewer(llm: Any, s: Settings, corpus: Corpus, clause: Clause, emit: Any, **kw: Any) -> Any:
        scope = llm_module._scope.get()
        seen.append(scope.deadline if scope is not None else None)
        return review_clause(gold_clause_llm(POLICY, "c1"), s, corpus, clause, emit, **kw)

    runner = runs.Runner(
        store, FakeLLM(), settings(), corpus, reviewer=reviewer, clock=clock, breaker=Breaker(AGENT_MODEL)
    )
    admitted = runner.admit(new_run_id(), OWNER, one_clause())
    started = clock()
    run = runner.execute(admitted, one_clause())  # its run_started write takes 5 s
    assert run.status == RunStatus.DONE
    assert seen == [pytest.approx(started + runs.RUN_BUDGET_S)]  # not 5 s later
    assert run.latency_ms is not None and run.latency_ms >= 5000  # measured from the same instant


# ---- the hand-over reason says what the agent model's tries met, and how many there were ---------------


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (busy(), f"Gemini 3.8 Flash was busy (HTTP 429) after {THROTTLED_ATTEMPTS} tries"),
        # Two 429s, then a timeout: it gives up as kind transient after 3 tries, not "timeouts after 2 tries".
        (
            LLMError(
                "x",
                kind="transient",
                model=AGENT_MODEL,
                attempts=3,
                failures=("throttled", "throttled", "transient"),
            ),
            "Gemini 3.8 Flash was busy (HTTP 429) and did not answer (timeouts or HTTP 5xx) after 3 tries",
        ),
        (
            LLMError("x", kind="transient", model=AGENT_MODEL, attempts=2, failures=("transient",) * 2),
            "Gemini 3.8 Flash did not answer (timeouts or HTTP 5xx) after 2 tries",
        ),
        (
            LLMError(
                "x",
                kind="deadline",
                model=AGENT_MODEL,
                cause="transient",
                attempts=2,
                failures=("throttled", "transient"),
            ),
            "Gemini 3.8 Flash was busy (HTTP 429) and did not answer (timeouts or HTTP 5xx), and no retry "
            f"fitted in its {runs.PRIMARY_PHASE_S:g} s share of the run budget",
        ),
    ],
    ids=["429s", "429_429_timeout", "timeouts", "mixed_then_deadline"],
)
def test_the_fallback_reason_counts_the_tries_actually_made(error: LLMError, reason: str) -> None:
    assert runs.fallback_reason(error) == reason


def test_a_mixed_give_up_through_the_real_adapter_is_reported_as_it_happened(
    store: MemoryStore, corpus: Corpus
) -> None:
    """The agent model's first chat call meets a 429, a 429 and then a timeout (a 30 s stall) through the real
    OpenRouter adapter: the clause is handed over, and its reason says so, with 3 tries."""
    clock = Clock()
    errors: list[Callable[[], Exception]] = [
        lambda: openai.RateLimitError("busy", response=httpx2.Response(429, request=REQ), body=None),
        lambda: openai.RateLimitError("busy", response=httpx2.Response(429, request=REQ), body=None),
        lambda: openai.APITimeoutError(request=REQ),
    ]
    provider = _Provider(clock, lambda timeout: 1.0 if len(errors) > 1 else timeout, lambda: errors.pop(0)())
    script = gold_clause_llm(POLICY, "c1")
    run = run_policy(store, corpus, SlowPrimary(provider, script), policy=one_clause(), clock=clock)
    assert len(provider.timeouts) == 3
    f = findings(store, run.run_id)["c1"]
    assert (f.agent_model, f.fallback_reason) == (
        FALLBACK,
        "Gemini 3.8 Flash was busy (HTTP 429) and did not answer (timeouts or HTTP 5xx) after 3 tries",
    )


# ---- a reply Gemini reports as broken (llm.LLMError kind "malformed"), seen live on 18 Oct ----------------


def malformed(model: str = AGENT_MODEL) -> LLMError:
    """A broken reply on the retry too, as llm._with_retries raises it."""
    return LLMError(
        f"OpenRouter {model} returned an unusable reply on 2 of 2 tries: finish reason MALFORMED_RESPONSE",
        kind="malformed",
        model=model,
        attempts=2,
    )


MALFORMED_HAND_OFF = (
    f"{model_name(AGENT_MODEL)} returned an unusable reply; handing this clause to {model_name(FALLBACK)}."
)


def test_a_broken_reply_on_the_agent_model_hands_over_and_the_breaker_stays_closed(
    store: MemoryStore, corpus: Corpus
) -> None:
    """More than OPEN_AFTER clauses get a broken reply from the agent model: each goes to the fallback model,
    labelled with why, and the breaker is never told "busy" (the model answered)."""
    policy = runs.load_policy(POLICY)
    stormy = policy.model_copy(update={"clauses": policy.clauses[: OPEN_AFTER + 1]})
    scripts = {c.id: gold_after(malformed(), c.id) for c in stormy.clauses}
    breaker = Breaker(AGENT_MODEL, clock=Ticks())
    run = run_policy(store, corpus, ClauseRoutedLLM(stormy, scripts), policy=stormy, breaker=breaker)
    assert run.status == RunStatus.DONE and run.error is None
    assert run.summary.fallback_clauses == len(stormy.clauses) and run.summary.failed == 0
    for cid, f in findings(store, run.run_id).items():
        assert f.error is None and f.agent_model == FALLBACK
        assert f.fallback_reason == "Gemini 3.8 Flash returned an unusable reply"
        assert MALFORMED_HAND_OFF in [s.summary for s in steps_of(store, run.run_id, cid)]
    assert breaker.state == "closed" and breaker.route() == "primary"


def test_a_broken_reply_on_the_fallback_fails_only_that_clause_and_says_so(
    store: MemoryStore, corpus: Corpus
) -> None:
    """The 18 Oct incident: the agent model was busy on c5, the fallback's reply came back broken. The clause
    goes to a person with MODEL_OUTPUT, and the run is done with the other clauses' findings."""
    script = FakeLLM(turns=[busy(), malformed(FALLBACK)])
    run = run_policy(store, corpus, policy_llm(POLICY, [("c5", script)]))
    assert models_called(script) == [("chat", AGENT_MODEL), ("chat", FALLBACK)]
    assert run.status == RunStatus.DONE and run.summary.failed == 1
    found = findings(store, run.run_id)
    assert len(found) == run.summary.clauses
    assert [cid for cid, f in found.items() if f.error is not None] == ["c5"]
    f = found["c5"]
    assert f.error is not None and f.error.startswith(runs.MODEL_OUTPUT)
    assert not f.error.startswith(runs.PROVIDER_BUSY)
    assert "so Gemini 3.1 Flash-Lite reviewed it again" in f.error
    assert run.error is not None and f"c5: {runs.MODEL_OUTPUT}" in run.error
    assert runs.sample_refusal(run) is not None


def test_a_broken_reply_on_the_monitor_model_has_no_fallback(store: MemoryStore, corpus: Corpus) -> None:
    script = FakeLLM(
        turns=[turn(text="Ready.")], json_replies=[draft_reply(POLICY, "c4"), malformed(MONITOR_MODEL)]
    )
    breaker = Breaker(AGENT_MODEL, clock=Ticks())
    run = run_policy(store, corpus, policy_llm(POLICY, [("c4", script)]), breaker=breaker)
    assert FALLBACK not in {m for _, m in models_called(script)}
    f = findings(store, run.run_id)["c4"]
    assert f.error is not None and f.error.startswith(runs.MODEL_OUTPUT) and f.fallback_reason is None
    assert run.status == RunStatus.DONE and breaker.state == "closed"


def test_the_model_output_marker_is_neither_busy_nor_out_of_time() -> None:
    assert runs.MODEL_OUTPUT == "Gemini returned an unusable reply; a person must review this clause."
    assert runs.hands_over(malformed()) and not malformed().capacity
    assert len({runs.MODEL_OUTPUT, runs.PROVIDER_BUSY, runs.OUT_OF_TIME}) == 3
