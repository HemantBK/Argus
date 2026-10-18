"""Hedged requests: an attempt with no reply after its hedge delay sends one copy more (llm._run_hedged).

The copies run on SimRunner, on a fake clock: each scripted request takes a set time, so a stall, a fast
hedge and which copy answers first are exact, with no threads and no waiting. One test at the end runs the
real thread runner.
"""

import json
import logging
import threading
import warnings
from concurrent.futures import Future
from typing import Any

import httpx
import openai
import pytest
from google.genai import errors as genai_errors
from google.genai import types as gt

from argus_api.llm import (
    BACKOFF_BASE_S,
    CHAT_HEDGE_AFTER_S,
    CHAT_TIMEOUT_S,
    HEDGE_MAX_IN_FLIGHT,
    MALFORMED_BACKOFF_S,
    MIN_ATTEMPT_S,
    PRICES_USD_PER_MTOK,
    REQUEST_HEDGE_AFTER_S,
    REQUEST_TIMEOUT_S,
    THROTTLED_ATTEMPTS,
    GoogleLLM,
    HedgeLimit,
    LLMError,
    Message,
    OpenRouterLLM,
    ToolSpec,
    call_scope,
)

MODEL = "gemini-test"
OR_MODEL = next(iter(PRICES_USD_PER_MTOK))
TOOL = ToolSpec(
    name="read_paragraph",
    description="Read one RBI paragraph.",
    parameters={"type": "object", "properties": {"para": {"type": "string"}}, "required": ["para"]},
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"label": {"type": "string"}},
    "required": ["label"],
}
MESSAGES = [Message("system", "s"), Message("user", "x")]
STALL = 70.0  # the longest stall seen on 2026-10-18; longer than any attempt timeout


@pytest.fixture(autouse=True)
def no_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("argus_api.llm._jitter", lambda: 1.0)


class Clock:
    """A fake perf_counter: advances only when the code under test sleeps or waits."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class SimRunner:
    """A Runner on the fake clock. submit() runs the call at once (so scripted replies are taken in the order
    the copies are sent) but finishes its future only when wait() moves the clock to the time the call took,
    which the scripted client reports through spend(). A call made in the calling thread (no hedge possible)
    spends its time on the clock directly."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._spent: float | None = None
        self._due: dict[Future[Any], tuple[float, bool, Any]] = {}

    def spend(self, seconds: float) -> None:
        if self._spent is None:
            self.clock.now += seconds
        else:
            self._spent += seconds

    def submit(self, fn: Any) -> Future[Any]:
        self._spent = 0.0
        try:
            outcome: tuple[bool, Any] = (True, fn())
        except Exception as e:
            outcome = (False, e)
        spent, self._spent = self._spent, None
        future: Future[Any] = Future()
        future.set_running_or_notify_cancel()
        self._due[future] = (self.clock.now + spent, *outcome)
        return future

    def wait(self, futures: set[Future[Any]], timeout: float | None) -> set[Future[Any]]:
        if any(f.done() for f in futures):
            return {f for f in futures if f.done()}
        at = min(self._due[f][0] for f in futures)
        if timeout is not None and at > self.clock.now + timeout:
            self.clock.now += timeout
            return set()
        self.clock.now = max(self.clock.now, at)
        for f in futures:
            due, ok, value = self._due[f]
            if due <= at:
                f.set_result(value) if ok else f.set_exception(value)
        return {f for f in futures if f.done()}

    def finish_all(self) -> None:
        """Let every copy still running reach its end, as an abandoned copy does in its thread."""
        for f, (due, ok, value) in self._due.items():
            if not f.done():
                self.clock.now = max(self.clock.now, due)
                f.set_result(value) if ok else f.set_exception(value)


def g_text(
    text: str, prompt: int = 11, out: int = 7, finish: gt.FinishReason = gt.FinishReason.STOP
) -> gt.GenerateContentResponse:
    return gt.GenerateContentResponse(
        candidates=[
            gt.Candidate(content=gt.Content(role="model", parts=[gt.Part(text=text)]), finish_reason=finish)
        ],
        usage_metadata=gt.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt, candidates_token_count=out
        ),
    )


def g_finish(name: str) -> gt.GenerateContentResponse:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return g_text("", finish=gt.FinishReason(name))


def busy() -> genai_errors.ClientError:
    return genai_errors.ClientError(429, {})


class ScriptedModels:
    """Request n takes script[n][0] s on the fake clock, then returns or raises script[n][1]; a request that
    takes its whole timeout (the one sent in http_options) times out as httpx does. Each request is recorded
    as (sent at, timeout)."""

    def __init__(self, runner: SimRunner, script: list[tuple[float, Any]]) -> None:
        self.runner, self.script = runner, list(script)
        self.requests: list[tuple[float, float]] = []

    def generate_content(self, **kwargs: Any) -> gt.GenerateContentResponse:
        timeout = kwargs["config"].http_options.timeout / 1000
        self.requests.append((self.runner.clock(), timeout))
        took, item = self.script.pop(0)
        if took >= timeout:
            self.runner.spend(timeout)
            raise httpx.ReadTimeout("timed out")
        self.runner.spend(took)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, gt.GenerateContentResponse)
        return item


class _Client:
    def __init__(self, models: ScriptedModels) -> None:
        self.models = models


def scripted(
    script: list[tuple[float, Any]], limit: HedgeLimit | None = None
) -> tuple[GoogleLLM, ScriptedModels, Clock, SimRunner]:
    clock = Clock()
    runner = SimRunner(clock)
    models = ScriptedModels(runner, script)
    llm = GoogleLLM(
        _Client(models),  # type: ignore[arg-type]
        sleep=clock.sleep,
        clock=clock,
        runner=runner,
        hedge_limit=limit or HedgeLimit(HEDGE_MAX_IN_FLIGHT),
    )
    return llm, models, clock, runner


def hedge_events(caplog: pytest.LogCaptureFixture, event: str = "llm_hedge") -> list[dict[str, Any]]:
    return [r.json_fields for r in caplog.records if r.getMessage() == event]  # type: ignore[attr-defined]


@pytest.fixture
def logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="argus.llm")
    return caplog


# ------------------------------------------------------------------------------------------------- basics


def test_the_hedge_delays_sit_past_the_slowest_normal_reply_and_leave_room_for_a_hedge() -> None:
    # 2026-10-18: normal chat turns 1.5-6 s, drafts up to about 7 s; stalls 25-70 s against a 30 s attempt.
    assert 6.0 < CHAT_HEDGE_AFTER_S < CHAT_TIMEOUT_S - MIN_ATTEMPT_S
    assert 7.0 < REQUEST_HEDGE_AFTER_S < REQUEST_TIMEOUT_S - MIN_ATTEMPT_S


def test_a_fast_reply_sends_no_hedge(logs: pytest.LogCaptureFixture) -> None:
    llm, models, clock, _ = scripted([(2.0, g_text("ok"))])
    t0 = clock()
    assert llm.chat(MODEL, MESSAGES, [TOOL]).text == "ok"
    assert len(models.requests) == 1 and clock() - t0 == 2.0
    assert hedge_events(logs) == []


def test_a_stalled_first_copy_loses_to_a_fast_hedge(logs: pytest.LogCaptureFixture) -> None:
    llm, models, clock, _ = scripted([(STALL, g_text("slow", out=900)), (2.0, g_text("fast", out=5))])
    t0 = clock()
    with call_scope(None, run_id="r1", clause_id="c1"):
        turn = llm.chat(MODEL, MESSAGES, [TOOL])
    assert (turn.text, turn.output_tokens) == ("fast", 5)  # only the winner's tokens count
    assert clock() - t0 == CHAT_HEDGE_AFTER_S + 2.0
    # The hedge is the same request, sent after the delay with what is left of the attempt's timeout.
    assert models.requests == [
        (t0, CHAT_TIMEOUT_S),
        (t0 + CHAT_HEDGE_AFTER_S, CHAT_TIMEOUT_S - CHAT_HEDGE_AFTER_S),
    ]
    [event] = hedge_events(logs)
    assert event == {
        "won_by": "hedge",
        "waited_ms": round((CHAT_HEDGE_AFTER_S + 2.0) * 1000),
        "after_s": CHAT_HEDGE_AFTER_S,
        "hedges": 1,
        "run_id": "r1",
        "clause_id": "c1",
        "call": f"Gemini {MODEL}",
        "model": MODEL,
        "attempt": 1,
    }


def test_json_calls_hedge_after_their_own_delay() -> None:
    llm, models, clock, _ = scripted([(STALL, g_text("{}")), (3.0, g_text(json.dumps({"label": "gap"})))])
    t0 = clock()
    value, _ = llm.complete_json(MODEL, "draft", SCHEMA)
    assert value == {"label": "gap"} and clock() - t0 == REQUEST_HEDGE_AFTER_S + 3.0
    assert [r[1] for r in models.requests] == [REQUEST_TIMEOUT_S, REQUEST_TIMEOUT_S - REQUEST_HEDGE_AFTER_S]


def test_the_first_copy_wins_when_it_answers_while_the_hedge_is_in_flight(
    logs: pytest.LogCaptureFixture,
) -> None:
    first_at = CHAT_HEDGE_AFTER_S + 1.0
    llm, models, clock, _ = scripted([(first_at, g_text("first", out=3)), (5.0, g_text("hedge", out=99))])
    t0 = clock()
    turn = llm.chat(MODEL, MESSAGES, [TOOL])
    assert (turn.text, turn.output_tokens) == ("first", 3)
    assert clock() - t0 == first_at and len(models.requests) == 2
    assert [e["won_by"] for e in hedge_events(logs)] == ["first"]


# ------------------------------------------------------------------------------------------------ failures


def test_a_first_copy_that_fails_before_the_delay_is_retried_as_before(
    logs: pytest.LogCaptureFixture,
) -> None:
    llm, models, clock, _ = scripted([(1.0, busy()), (2.0, g_text("ok"))])
    assert llm.complete(MODEL, "hi").text == "ok"
    assert len(models.requests) == 2 and clock.sleeps == [BACKOFF_BASE_S]
    assert hedge_events(logs) == []


def test_both_copies_busy_then_a_normal_retry(logs: pytest.LogCaptureFixture) -> None:
    after = CHAT_HEDGE_AFTER_S
    llm, models, clock, _ = scripted([(after + 1.0, busy()), (0.5, busy()), (2.0, g_text("ok"))])
    t0 = clock()
    assert llm.chat(MODEL, MESSAGES, [TOOL]).text == "ok"
    # Both copies failed with 429: one throttled try, its backoff, then the retry.
    assert clock.sleeps == [BACKOFF_BASE_S]
    assert [sent - t0 for sent, _ in models.requests] == [0.0, after, after + 1.0 + BACKOFF_BASE_S]
    assert [e["won_by"] for e in hedge_events(logs)] == ["neither"]


def test_a_copy_that_fails_waits_for_the_other(logs: pytest.LogCaptureFixture) -> None:
    # The hedge is busy at once, but the first copy, still running, then answers.
    llm, _, clock, _ = scripted([(CHAT_HEDGE_AFTER_S + 3.0, g_text("late but good")), (0.5, busy())])
    assert llm.chat(MODEL, MESSAGES, [TOOL]).text == "late but good"
    assert clock.sleeps == [] and [e["won_by"] for e in hedge_events(logs)] == ["first"]


def test_both_failing_prefers_the_busy_providers_error(logs: pytest.LogCaptureFixture) -> None:
    # The first copy's reply is broken, the hedge gets a 429: the attempt is a throttled try (busy), so it is
    # retried on the 429 limit and the call's error tells the breaker and the hand-over the provider was busy.
    llm, _, clock, _ = scripted(
        [
            (CHAT_HEDGE_AFTER_S + 1.0, g_finish("MALFORMED_RESPONSE")),
            (0.5, busy()),
            (1.0, genai_errors.ClientError(400, {})),
        ]
    )
    with pytest.raises(LLMError) as raised:
        llm.chat(MODEL, MESSAGES, [TOOL])
    assert (raised.value.kind, raised.value.failures) == ("fatal", ("throttled",))
    first = hedge_events(logs, "llm_attempt_failed")[0]
    assert (first["error_type"], first["max_attempts"]) == ("ClientError", THROTTLED_ATTEMPTS)
    assert clock.sleeps == [BACKOFF_BASE_S]


def test_both_copies_stalling_is_one_stalled_try() -> None:
    llm, models, clock, _ = scripted([(STALL, None), (STALL, None), (STALL, None), (STALL, None)])
    t0 = clock()
    with pytest.raises(LLMError) as raised:
        llm.chat(MODEL, MESSAGES, [TOOL])
    e = raised.value
    assert (e.kind, e.cause, e.attempts, e.failures) == ("transient", "transient", 2, ("transient",) * 2)
    # Each attempt ended with its own timeout; the hedge did not extend it.
    assert clock() - t0 == 2 * CHAT_TIMEOUT_S + BACKOFF_BASE_S and len(models.requests) == 4


def test_a_broken_reply_before_the_delay_still_gets_its_retry() -> None:
    llm, models, clock, _ = scripted([(1.0, g_finish("MALFORMED_FUNCTION_CALL")), (2.0, g_text("ok"))])
    assert llm.chat(MODEL, MESSAGES, [TOOL]).text == "ok"
    assert len(models.requests) == 2 and clock.sleeps == [MALFORMED_BACKOFF_S]


def test_a_broken_reply_twice_with_hedges_is_still_malformed() -> None:
    late = CHAT_HEDGE_AFTER_S + 1.0
    broken = g_finish("MALFORMED_RESPONSE")
    llm, _, clock, _ = scripted([(late, broken), (0.5, broken), (late, broken), (0.5, broken)])
    with pytest.raises(LLMError) as raised:
        llm.chat(MODEL, MESSAGES, [TOOL])
    assert (raised.value.kind, raised.value.capacity) == ("malformed", False)
    assert clock.sleeps == [MALFORMED_BACKOFF_S]


# ---------------------------------------------------------------------------------------- deadline and cap


def test_a_hedge_never_runs_past_the_deadline() -> None:
    left = 20.0
    llm, models, clock, _ = scripted([(STALL, None), (STALL, None)])
    t0 = clock()
    with call_scope(t0 + left), pytest.raises(LLMError) as raised:
        llm.chat(MODEL, MESSAGES, [TOOL])
    assert raised.value.kind == "deadline"
    assert models.requests == [(t0, left), (t0 + CHAT_HEDGE_AFTER_S, left - CHAT_HEDGE_AFTER_S)]
    assert clock() == t0 + left


def test_a_model_deadline_bounds_the_hedge_too() -> None:
    own = 15.0
    llm, models, clock, _ = scripted([(STALL, None), (3.0, g_text("ok"))])
    t0 = clock()
    with call_scope(t0 + 100.0, model_deadlines={MODEL: t0 + own}):
        assert llm.chat(MODEL, MESSAGES, [TOOL]).text == "ok"
    assert [r[1] for r in models.requests] == [own, own - CHAT_HEDGE_AFTER_S]


def test_no_hedge_without_room_for_one_before_the_deadline(logs: pytest.LogCaptureFixture) -> None:
    left = CHAT_HEDGE_AFTER_S + MIN_ATTEMPT_S / 2
    llm, models, clock, _ = scripted([(STALL, None)])
    t0 = clock()
    with call_scope(t0 + left), pytest.raises(LLMError):
        llm.chat(MODEL, MESSAGES, [TOOL])
    assert len(models.requests) == 1 and clock() == t0 + left
    assert hedge_events(logs) == []


def test_no_hedge_past_the_cap(logs: pytest.LogCaptureFixture) -> None:
    limit = HedgeLimit(1)
    assert limit.acquire()  # another call's hedge holds the only slot
    llm, models, clock, _ = scripted(
        [(CHAT_HEDGE_AFTER_S + 4.0, g_text("ok")), (1.0, g_text("never sent"))], limit
    )
    t0 = clock()
    assert llm.chat(MODEL, MESSAGES, [TOOL]).text == "ok"
    assert len(models.requests) == 1 and clock() - t0 == CHAT_HEDGE_AFTER_S + 4.0
    assert hedge_events(logs) == []
    [skipped] = hedge_events(logs, "llm_hedge_skipped")
    assert (skipped["reason"], skipped["cap"]) == ("cap", 1)
    # Once that slot is free, the next stalled call hedges again. Its slot is freed only when both copies have
    # ended: the hedge won, but the stalled first copy still holds a request open until its own timeout.
    limit.release()
    llm, models, _, runner = scripted([(STALL, g_text("slow")), (1.0, g_text("fast"))], limit)
    assert llm.chat(MODEL, MESSAGES, [TOOL]).text == "fast"
    assert len(models.requests) == 2 and limit.in_flight == 1
    runner.finish_all()
    assert limit.in_flight == 0


def test_the_hedge_limit_is_thread_safe() -> None:
    limit = HedgeLimit(HEDGE_MAX_IN_FLIGHT)
    got: list[bool] = []
    lock = threading.Lock()
    start = threading.Barrier(40)

    def take() -> None:
        start.wait()
        ok = limit.acquire()
        with lock:
            got.append(ok)

    threads = [threading.Thread(target=take) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(got) == HEDGE_MAX_IN_FLIGHT == limit.in_flight
    for _ in range(HEDGE_MAX_IN_FLIGHT):
        limit.release()
    with pytest.raises(RuntimeError):
        limit.release()


# ------------------------------------------------------------------------------------- OpenRouter adapter


class ScriptedOpenRouter:
    """OpenAI-compatible client on SimRunner: request n takes script[n][0] s, then answers script[n][1]."""

    def __init__(self, runner: SimRunner, script: list[tuple[float, str]]) -> None:
        self.runner, self.script = runner, list(script)
        self.timeouts: list[float] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> Any:
        from openai.types.chat import ChatCompletion

        self.timeouts.append(kwargs["timeout"])
        took, text = self.script.pop(0)
        if took >= kwargs["timeout"]:
            self.runner.spend(kwargs["timeout"])
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://openrouter.ai"))
        self.runner.spend(took)
        return ChatCompletion.model_validate(
            {
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": kwargs["model"],
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )


def test_openrouter_hedges_a_stalled_call_the_same_way() -> None:
    clock = Clock()
    runner = SimRunner(clock)
    client = ScriptedOpenRouter(runner, [(STALL, "slow"), (2.0, "fast")])
    llm = OpenRouterLLM(
        None,
        client=client,  # type: ignore[arg-type]
        sleep=clock.sleep,
        clock=clock,
        runner=runner,
        hedge_limit=HedgeLimit(HEDGE_MAX_IN_FLIGHT),
    )
    t0 = clock()
    assert llm.complete(OR_MODEL, "hi").text == "fast"
    assert clock() - t0 == REQUEST_HEDGE_AFTER_S + 2.0
    assert client.timeouts == [REQUEST_TIMEOUT_S, REQUEST_TIMEOUT_S - REQUEST_HEDGE_AFTER_S]


# ----------------------------------------------------------------------------------- the real thread runner


class RacingModels:
    """Real threads: the first request answers once the hedge has been sent; the hedge then blocks until the
    test releases it, so it is still running when the first copy wins."""

    def __init__(self) -> None:
        self.hedge_sent = threading.Event()
        self.release = threading.Event()
        self.hedge_done = threading.Event()
        self.calls = 0
        self.lock = threading.Lock()

    def generate_content(self, **kwargs: Any) -> gt.GenerateContentResponse:
        with self.lock:
            self.calls += 1
            n = self.calls
        if n == 1:
            assert self.hedge_sent.wait(10)
            return g_text("first")
        self.hedge_sent.set()
        self.release.wait(10)
        self.hedge_done.set()
        return g_text("hedge")


def wait_until(condition: Any, seconds: float = 5.0) -> bool:
    done = threading.Event()
    for _ in range(int(seconds / 0.01)):
        if condition():
            return True
        done.wait(0.01)
    return bool(condition())


def test_real_threads_an_abandoned_hedge_holds_its_slot_until_it_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("argus_api.llm.CHAT_HEDGE_AFTER_S", 0.05)
    models = RacingModels()
    limit = HedgeLimit(HEDGE_MAX_IN_FLIGHT)
    llm = GoogleLLM(_Client(models), hedge_limit=limit)  # type: ignore[arg-type]
    try:
        assert llm.chat(MODEL, MESSAGES, [TOOL]).text == "first"
        # The first copy won; the hedge still runs in its thread, its reply to be ignored, and holds its slot.
        assert models.calls == 2 and not models.hedge_done.is_set() and limit.in_flight == 1
    finally:
        models.release.set()
    assert models.hedge_done.wait(5) and wait_until(lambda: limit.in_flight == 0)


class StalledFirstModels:
    """Real threads: every first copy stalls until the test releases it; every hedge answers at once."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.stalled = 0
        self.lock = threading.Lock()

    def generate_content(self, **kwargs: Any) -> gt.GenerateContentResponse:
        with self.lock:
            first = kwargs["config"].http_options.timeout == CHAT_TIMEOUT_S * 1000
            self.stalled += first
        if first:
            self.release.wait(10)
            return g_text("slow")
        return g_text("fast")


def test_real_threads_an_abandoned_first_copy_holds_its_slot_until_it_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The hedge wins, but the stalled first copy runs on in its thread with a request open to the provider,
    # so the slot stays taken: the next stalled call gets no hedge until that copy has ended.
    monkeypatch.setattr("argus_api.llm.CHAT_HEDGE_AFTER_S", 0.05)
    models = StalledFirstModels()
    limit = HedgeLimit(1)
    llm = GoogleLLM(_Client(models), hedge_limit=limit)  # type: ignore[arg-type]
    try:
        assert llm.chat(MODEL, MESSAGES, [TOOL]).text == "fast"
        assert limit.in_flight == 1
        second = threading.Thread(target=lambda: llm.chat(MODEL, MESSAGES, [TOOL]))
        second.start()
        assert wait_until(lambda: models.stalled == 2)
        second.join(0.3)
        assert second.is_alive() and limit.in_flight == 1  # no hedge past the cap: it waits on its stall
    finally:
        models.release.set()
    second.join(5)
    assert not second.is_alive() and wait_until(lambda: limit.in_flight == 0)
