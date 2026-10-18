"""One interface over the three ways we reach Gemini.

- vertex:     Gemini on Vertex AI (the deployed app; the only provider allowed in cloud mode)
- aistudio:   Gemini through a Google AI Studio API key (local development)
- openrouter: Gemini through OpenRouter's OpenAI-compatible API (local development only)

Every provider offers the same three calls: complete (plain text), complete_json (structured output validated
against a JSON Schema) and chat (one model turn with function calling). Provider failures surface as LLMError;
transient ones (HTTP 429, 5xx, timeouts) are retried with exponential backoff first. A 429 (the shared
Gemini capacity is busy) is answered at once, so it gets more tries (THROTTLED_ATTEMPTS) than a 5xx or a
timeout, which can stall for the whole attempt (MAX_ATTEMPTS). Every failed attempt is
logged (llm_attempt_failed, WARNING) with what happens next, so retries are visible in Cloud Logging. The
LLMError of a provider failure carries its kind (ErrorKind) and model, so a caller can tell a busy model
(LLMError.capacity; runs.py then hands the clause to the fallback model) from a request that cannot succeed,
and both from a call our own deadline cut with no provider failure (kind "deadline", cause None).

A reply Gemini itself reports as broken (finish reason MALFORMED_FUNCTION_CALL, MALFORMED_RESPONSE or
UNEXPECTED_TOOL_CALL: MALFORMED_FINISH) is sent again once, unchanged, MALFORMED_BACKOFF_S later and within
the deadline. If that fails too it is LLMError kind "malformed" with no cause: the model answered, so it is
not a busy provider, and runs.py may hand the clause to the fallback model without telling the breaker
"busy". Every other finish reason that is not STOP (SAFETY, MAX_TOKENS, ...) is still an LLMError at once.

Each attempt has its own timeout, sized to the call: a chat turn returns a few dozen tokens, so it is cut
sooner than a structured call that writes a whole finding. Inside call_scope(deadline) every call also ends by
the deadline: an attempt's timeout never runs past it, and no retry starts without time to finish, so one
stalled call cannot carry a clause past the run budget; it fails as LLMError saying so. A scope may also
give one model an earlier deadline of its own (model_deadlines), which the caller can end early
(_Scope.end_model_deadline): runs.py cuts the agent model's calls at its share of the run budget until its
first draft is written, while the monitor model's calls keep the run deadline.

A model may be given a pinned thinking level (config.AGENT_THINKING, config.MONITOR_THINKING): Gemini's
thinking_level on google-genai, reasoning effort on OpenRouter. Models without one use the provider default.

Token counts are what the provider bills: for Gemini the output count includes thinking tokens.
"""

import json
import logging
import random
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

import httpx
import httpx2
import jsonschema
import openai
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from argus_api.config import (
    GEMINI_AGENT_MODEL,
    GEMINI_MONITOR_MODEL,
    OPENROUTER_PREFIX,
    ConfigError,
    Settings,
    ThinkingLevel,
)
from argus_api.logs import log_event

log = logging.getLogger("argus.llm")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Transient failures: tries in total (first call included), and the wait before retry n is BASE * 2**(n-1).
MAX_ATTEMPTS = 2
BACKOFF_BASE_S = 1.0
# HTTP 429 (RESOURCE_EXHAUSTED): Gemini 3.x on Vertex AI "global" runs on shared capacity with no per-project
# quota, and a busy pool rejects calls in about a second. On 2026-10-17 a sample review on a new revision
# failed when 2 tries 1 s apart both got 429. These rejections cost no time, unlike the stalls below, so they
# get 5 tries (waits of about 1, 2, 4 and 8 s, 15 s at most), still cut short by the run deadline. The waits
# are jittered to 50-100% so clauses rejected together do not all retry in the same instant.
THROTTLED_ATTEMPTS = 5
# Per attempt. Live runs on 2026-10-16 showed that provider stalls are correlated, not random: when a
# call sat in the queue, its retries usually stalled too, so cutting attempts at 8-12 s turned slow
# clauses into failed ones (2 of 6 runs failed). Normal turns finish in 2-10 s and the slowest
# successful turn seen was ~28 s, so each attempt gets 30 s and only one retry; the run budget
# (runs.RUN_BUDGET_S) bounds the total.
CHAT_TIMEOUT_S = 30.0
REQUEST_TIMEOUT_S = 30.0  # complete and complete_json
# Inside call_scope: an attempt is not started with less than this left before the deadline.
MIN_ATTEMPT_S = 2.0
# complete_json: one retry when the reply is not valid JSON for the schema.
JSON_ATTEMPTS = 2
# Finish reasons that say the model's reply itself came out broken, not that the request was refused. On
# 2026-10-18 a live run's backup review (gemini-3.1-flash-lite) stopped once with MALFORMED_RESPONSE and the
# clause failed, though the next identical call would likely have answered. Such a reply is sent again once,
# unchanged (MALFORMED_ATTEMPTS tries in all), after MALFORMED_BACKOFF_S. Names, because google-genai 2.31
# has no enum member for MALFORMED_RESPONSE (it parses it as an unknown value with that name). OpenRouter
# passes Gemini's own reason on as native_finish_reason.
MALFORMED_FINISH = frozenset({"MALFORMED_FUNCTION_CALL", "MALFORMED_RESPONSE", "UNEXPECTED_TOOL_CALL"})
MALFORMED_ATTEMPTS = 2
MALFORMED_BACKOFF_S = 1.0

# USD per million tokens (input, output) for every model id in config.PINNED_MODELS, pinned on 2026-10-16.
# OpenRouter ids: OpenRouter's list prices (its /api/v1/models). Bare ids (Vertex AI, AI Studio): Google's
# standard paid-tier list prices for prompts up to 200k tokens, as reported by price trackers on 2026-10-16;
# check them against the Vertex AI pricing page before quoting a cost.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    OPENROUTER_PREFIX + GEMINI_AGENT_MODEL: (0.825, 4.125),
    OPENROUTER_PREFIX + GEMINI_MONITOR_MODEL: (0.30, 2.50),
    GEMINI_AGENT_MODEL: (0.75, 3.75),
    GEMINI_MONITOR_MODEL: (0.30, 2.50),
}
_GOOGLE_THINKING: dict[ThinkingLevel, genai_types.ThinkingLevel] = {
    "minimal": genai_types.ThinkingLevel.MINIMAL,
    "low": genai_types.ThinkingLevel.LOW,
    "medium": genai_types.ThinkingLevel.MEDIUM,
    "high": genai_types.ThinkingLevel.HIGH,
}

# Provider state kept per tool call so a later chat() can replay it (Gemini thought signatures).
_PROVIDER_STATE_CAP = 4096

T = TypeVar("T")
Sleep = Callable[[float], None]
Clock = Callable[[], float]


@dataclass
class _Scope:
    deadline: float | None  # on the time.perf_counter clock, never later than the outer scope's
    fields: dict[str, Any]
    outer: "_Scope | None" = None
    # Model id -> an earlier deadline for that model's calls only (same clock), set by this scope.
    model_deadlines: dict[str, float] = field(default_factory=dict)

    def deadline_for(self, model: str) -> float | None:
        """When a call to `model` must end: the earliest of this scope's deadline, the model's own deadline
        here, and what every outer scope says for it. Read at each call, so an ended model deadline counts
        from the next call on."""
        d = _earliest(self.model_deadlines.get(model), self.deadline)
        return d if self.outer is None else _earliest(d, self.outer.deadline_for(model))

    def own_deadline(self, model: str) -> bool:
        """Whether a model deadline, not the scope's own, is what ends a call to `model` first."""
        d = self.deadline_for(model)
        return d is not None and (self.deadline is None or d < self.deadline)

    def end_model_deadline(self, model: str) -> None:
        """From the next call on, calls to `model` in this scope end by the scope's deadline only (and by any
        outer scope's deadline for it): this scope's earlier deadline for the model no longer applies."""
        self.model_deadlines.pop(model, None)


_scope: ContextVar[_Scope | None] = ContextVar("argus_llm_scope", default=None)


def _earliest(a: float | None, b: float | None) -> float | None:
    return b if a is None else a if b is None else min(a, b)


@contextmanager
def call_scope(
    deadline: float | None, *, model_deadlines: dict[str, float] | None = None, **log_fields: Any
) -> Iterator[_Scope]:
    """Every LLM call made inside (in this context) ends by `deadline` (a time.perf_counter() value; None
    for no deadline), and a call to a model named in `model_deadlines` ends by that model's own deadline
    too, until the yielded scope's end_model_deadline(model). Retry logs carry `log_fields` (run_id,
    clause_id). An inner scope cannot extend an outer deadline, for any model."""
    outer = _scope.get()
    if outer is not None:
        deadline = _earliest(deadline, outer.deadline)
    fields = {**(outer.fields if outer is not None else {}), **log_fields}
    scope = _Scope(deadline, fields, outer, dict(model_deadlines or {}))
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass(frozen=True)
class Turn:
    text: str
    tool_calls: list[ToolCall]
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int  # wall time of the call, retries included


# How a model call failed, on LLMError.kind: "throttled" (gave up after a 429 on the last try), "transient"
# (gave up after a 5xx or a timeout on the last try), "deadline" (the call_scope deadline came first: a try
# failed and no retry fitted, or no try could start), "fatal" (an error retrying cannot fix, such as HTTP
# 400), "malformed" (Gemini reported its own reply as broken, MALFORMED_FINISH, on the retry too or with no
# time left to retry; cause None). None: any other unusable reply (a SAFETY stop, invalid JSON). The tries
# before the last may have failed differently (a 429, then a timeout): LLMError.failures has each.
ErrorKind = Literal["throttled", "transient", "fatal", "deadline", "malformed"]
# The provider's own failure behind an LLMError, on LLMError.cause: "throttled" (an HTTP 429) or "transient"
# (a 5xx, or a try that timed out on its own limit). None when the provider did not fail: a "fatal" error,
# or a "deadline" error where our deadline was all that ended the call (no try failed, or the only failures
# were tries cut short by the deadline itself).
BusyCause = Literal["throttled", "transient"]


class LLMError(RuntimeError):
    """A model call failed. `kind`, `cause` and `model` are set on provider failures (_with_retries); see
    ErrorKind and BusyCause. `attempts` is the number of tries made, and `failures` the provider's failure on
    each try that it failed, in order (a try our deadline cut short is not one)."""

    def __init__(
        self,
        message: str,
        *,
        kind: ErrorKind | None = None,
        model: str | None = None,
        cause: BusyCause | None = None,
        attempts: int | None = None,
        failures: tuple[BusyCause, ...] = (),
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.model = model
        # A give-up on 429s or on 5xx/timeouts is the provider's failure by its kind.
        self.cause: BusyCause | None = kind if kind in ("throttled", "transient") else cause
        self.attempts = attempts
        self.failures = failures

    @property
    def capacity(self) -> bool:
        """The provider was busy or slow: a 429, 5xx or timeout ended the call, or came before our deadline
        did. Not a call that only ran out of our time budget."""
        return self.cause is not None

    @property
    def out_of_time(self) -> bool:
        """The call_scope deadline ended the call (kind "deadline"), whether or not the provider failed."""
        return self.kind == "deadline"


class LLM(Protocol):
    def complete(self, model: str, prompt: str) -> Completion: ...

    def complete_json(
        self, model: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Completion]:
        """Reply as a JSON object valid against `schema`; one retry on invalid JSON, then LLMError.

        The Completion holds the raw reply text and the tokens of every attempt.
        """
        ...

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec]) -> Turn: ...


def estimate_cost_usd(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    """Cost from the pinned price table; None when the model is not priced or a token count is unknown."""
    price = PRICES_USD_PER_MTOK.get(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


# ---------------------------------------------------------------------------------------------------------
# Shared helpers


class _Transient(Exception):
    """A provider failure worth retrying that the SDK did not raise as an exception (error in a 200 body)."""


class _Throttled(_Transient):
    """A 429 reported in a 200 body."""


class _Malformed(Exception):
    """A reply the model itself reports as broken (MALFORMED_FINISH). Not a _Transient: the provider answered,
    so it is never counted as busy (LLMError.failures, LLMError.cause)."""


# throttled: a 429, retried up to THROTTLED_ATTEMPTS times; transient: a 5xx or a timeout, MAX_ATTEMPTS;
# malformed: a broken reply (_Malformed), MALFORMED_ATTEMPTS.
Classification = Literal["throttled", "transient", "fatal", "malformed"] | None


def _jitter() -> float:
    """A fraction in [0, 1) that spreads the waits of throttled retries; tests replace it."""
    return random.random()


def _attempt_timeout(
    timeout_s: float,
    deadline: float | None,
    which: str,
    clock: Clock,
    what: str,
    attempt: int,
    model: str,
    failures: tuple[BusyCause, ...],
) -> float:
    """This attempt's timeout: the call's own limit, cut to the time left before the deadline (named
    `which`). `failures` are the provider's failures on earlier tries of this call."""
    if deadline is None:
        return timeout_s
    left = deadline - clock()
    if left < MIN_ATTEMPT_S:
        raise LLMError(
            f"{what}: attempt {attempt} not started, {max(left, 0.0):.1f} s left before {which}",
            kind="deadline",
            model=model,
            cause=failures[-1] if failures else None,
            attempts=attempt - 1,
            failures=failures,
        )
    return min(timeout_s, left)


def _with_retries(
    call: Callable[[float], T],
    classify: Callable[[BaseException], Classification],
    sleep: Sleep,
    what: str,
    timeout_s: float,
    clock: Clock,
    model: str,
) -> T:
    """Run `call(timeout)` with retries on transient failures and on a reply the model reports as broken
    (_Malformed, counted on its own: MALFORMED_ATTEMPTS). Each failed attempt is logged. A provider failure
    is raised as LLMError with its kind, its cause and `model`."""
    scope = _scope.get()
    fields = scope.fields if scope is not None else {}
    deadline = scope.deadline_for(model) if scope is not None else None
    # Errors name the deadline that applies: the model's own (runs.py: its share of the run) or the scope's.
    which = "the model's own deadline" if scope is not None and scope.own_deadline(model) else "the deadline"
    failures: tuple[BusyCause, ...] = ()  # the provider's failure on each try of this call it failed
    malformed = 0  # tries of this call whose reply came back broken (_Malformed)
    # A broken reply is retried on its own count, so it can add one try to what 429s alone allow.
    for attempt in range(1, max(MAX_ATTEMPTS, THROTTLED_ATTEMPTS) + MALFORMED_ATTEMPTS):
        timeout = _attempt_timeout(timeout_s, deadline, which, clock, what, attempt, model, failures)
        started = clock()
        try:
            return call(timeout)
        except LLMError:
            raise
        except Exception as e:
            if isinstance(e, _Malformed):
                kind: Classification = "malformed"
                malformed += 1
            elif isinstance(e, _Throttled):
                kind = "throttled"
            elif isinstance(e, _Transient):
                kind = "transient"
            else:
                kind = classify(e)
            if kind is None:
                raise  # not a provider failure: a bug, let it surface as is
            limit = {"throttled": THROTTLED_ATTEMPTS, "malformed": MALFORMED_ATTEMPTS}.get(kind, MAX_ATTEMPTS)
            # A try that ran to a timeout our deadline had shortened was cut by our budget, not failed by the
            # provider; any other 429, 5xx or timeout is the provider's.
            cut_by_deadline = kind == "transient" and timeout < timeout_s and clock() - started >= timeout
            if kind in ("throttled", "transient") and not cut_by_deadline:
                failures = (*failures, kind)
            failure = {
                **fields,
                "call": what,
                "attempt": attempt,
                "max_attempts": limit,
                "error_type": type(e).__name__,
                "error": str(e)[:300],
                "attempt_ms": round((clock() - started) * 1000),
                "timeout_s": round(timeout, 1),
            }
            backoff = MALFORMED_BACKOFF_S if kind == "malformed" else BACKOFF_BASE_S * 2 ** (attempt - 1)
            if kind == "throttled":
                backoff *= 0.5 + 0.5 * _jitter()
            no_room = deadline is not None and deadline - clock() - backoff < MIN_ATTEMPT_S
            if kind == "fatal":
                outcome = "fatal"
                error = LLMError(
                    f"{what} failed: {e}", kind="fatal", model=model, attempts=attempt, failures=failures
                )
            elif kind == "malformed" and (malformed >= MALFORMED_ATTEMPTS or no_room):
                # The model answered each time, so this is its broken reply, not a busy provider or our
                # deadline: kind "malformed", no cause, even when no time was left for the retry.
                outcome = "gave_up"
                error = LLMError(
                    f"{what} returned an unusable reply on {malformed} of {attempt} "
                    f"{'try' if attempt == 1 else 'tries'}: {e}"
                    + ("" if malformed >= MALFORMED_ATTEMPTS else f"; no time left to retry before {which}"),
                    kind="malformed",
                    model=model,
                    attempts=attempt,
                    failures=failures,
                )
            elif kind != "malformed" and attempt >= limit and not cut_by_deadline:
                outcome = "gave_up"
                error = LLMError(
                    f"{what} failed after {attempt} tries: {e}",
                    kind=kind,
                    model=model,
                    attempts=attempt,
                    failures=failures,
                )
            elif kind != "malformed" and (cut_by_deadline or no_room):
                # Our deadline cut this try short, or leaves no room for another: the call ends on our budget,
                # and only earlier tries the provider itself failed make it the provider's (cause).
                left = deadline - clock() - backoff if deadline is not None else 0.0
                outcome = "deadline"
                error = LLMError(
                    f"{what} failed on try {attempt}: {e}; not retried, {max(left, 0.0):.1f} s would be left "
                    f"before {which}",
                    kind="deadline",
                    model=model,
                    cause=failures[-1] if failures else None,
                    attempts=attempt,
                    failures=failures,
                )
            else:
                log_event(
                    log,
                    "llm_attempt_failed",
                    level=logging.WARNING,
                    outcome="retry",
                    backoff_s=backoff,
                    **failure,
                )
                sleep(backoff)
                continue
            log_event(log, "llm_attempt_failed", level=logging.WARNING, outcome=outcome, **failure)
            raise error from e
    raise AssertionError("unreachable")


# HTTP 499 CANCELLED: Vertex AI answers it, like 504 DEADLINE_EXCEEDED, when a stalled call reaches the
# server-side deadline that google-genai derives from our timeout. On 2026-10-17 a release candidate's clause
# got 499 "The operation was cancelled." after 27.6 s of a 30 s try, while its other clauses got 504 after
# 27.1-27.4 s: the same stall, so it is retried and handed over the same way.
CANCELLED_STATUS = 499


def _is_transient_status(code: int | None) -> bool:
    return code is not None and (code == 429 or code == CANCELLED_STATUS or code >= 500)


def _status_kind(code: int | None) -> Classification:
    """How a provider's HTTP status is retried: 429 throttled, other 5xx transient, the rest fatal."""
    if code == 429:
        return "throttled"
    return "transient" if _is_transient_status(code) else "fatal"


class _InvalidJSON(Exception):
    pass


def parse_json_reply(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Parse a model reply as one JSON object and validate it against `schema`; raises _InvalidJSON."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        raise _InvalidJSON(f"not JSON ({e.msg} at position {e.pos})") from e
    if not isinstance(value, dict):
        raise _InvalidJSON(f"expected a JSON object, got {type(value).__name__}")
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as e:
        path = "/".join(str(p) for p in e.absolute_path) or "(root)"
        raise _InvalidJSON(f"schema violation at {path}: {e.message}") from e
    return value


def _retry_prompt(prompt: str, problem: str) -> str:
    return (
        f"{prompt}\n\nYour previous reply was rejected: {problem}. "
        "Reply again with only a JSON object that matches the schema."
    )


def _add_tokens(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else a + b


def _complete_json_with(
    call: Callable[[str], Completion], model: str, prompt: str, schema: dict[str, Any]
) -> tuple[dict[str, Any], Completion]:
    jsonschema.Draft202012Validator.check_schema(schema)  # a bad schema is a caller bug; fail before paying
    attempt_prompt = prompt
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    problem = ""
    for _ in range(JSON_ATTEMPTS):
        c = call(attempt_prompt)
        input_tokens = _add_tokens(input_tokens, c.input_tokens)
        output_tokens = _add_tokens(output_tokens, c.output_tokens)
        try:
            value = parse_json_reply(c.text, schema)
        except _InvalidJSON as e:
            problem = str(e)
            attempt_prompt = _retry_prompt(prompt, problem)
            continue
        return value, Completion(
            text=c.text, model=c.model, input_tokens=input_tokens, output_tokens=output_tokens
        )
    raise LLMError(f"{model} returned invalid JSON {JSON_ATTEMPTS} times; last problem: {problem}")


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


class _BoundedStore:
    """Thread-safe map of tool call id -> provider state, oldest entries dropped past the cap."""

    def __init__(self) -> None:
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > _PROVIDER_STATE_CAP:
                self._data.popitem(last=False)

    def get(self, key: str) -> Any | None:
        with self._lock:
            return self._data.get(key)


def _tool_names(messages: list[Message]) -> dict[str, str]:
    return {tc.id: tc.name for m in messages if m.role == "assistant" for tc in m.tool_calls}


# ---------------------------------------------------------------------------------------------------------
# Gemini through google-genai (Vertex AI and AI Studio)

_GOOGLE_TIMEOUTS = (httpx.TimeoutException, httpx2.TimeoutException)
_GOOGLE_OK_FINISH = {genai_types.FinishReason.STOP, None}


def _classify_google(e: BaseException) -> Classification:
    if isinstance(e, genai_errors.APIError):
        return _status_kind(e.code)
    if isinstance(e, _GOOGLE_TIMEOUTS):
        return "transient"
    return None


def _google_finish_name(r: genai_types.GenerateContentResponse) -> str | None:
    """The first candidate's finish reason by name (an unknown value keeps its name), or None."""
    if not r.candidates or r.candidates[0].finish_reason is None:
        return None
    reason = r.candidates[0].finish_reason
    return getattr(reason, "name", None) or str(reason)


def _google_usage(r: genai_types.GenerateContentResponse) -> tuple[int | None, int | None]:
    u = r.usage_metadata
    if u is None:
        return None, None
    # Gemini omits zero counts from the response, so an absent field next to a present one means 0.
    input_tokens = (
        None if u.prompt_token_count is None else u.prompt_token_count + (u.tool_use_prompt_token_count or 0)
    )
    out = [n for n in (u.candidates_token_count, u.thoughts_token_count) if n is not None]
    return input_tokens, (sum(out) if out else None)


class GoogleLLM:
    def __init__(
        self,
        client: genai.Client,
        *,
        thinking: dict[str, ThinkingLevel] | None = None,
        sleep: Sleep = time.sleep,
        clock: Clock = time.perf_counter,
    ) -> None:
        self._client = client
        self._thinking = dict(thinking or {})
        self._sleep = sleep
        self._clock = clock
        self._call_parts = (
            _BoundedStore()
        )  # tool call id -> the model's function_call Part (thought signature)

    def _generate(
        self,
        model: str,
        contents: Any,
        config: genai_types.GenerateContentConfig | None,
        timeout_s: float = REQUEST_TIMEOUT_S,
    ) -> tuple[genai_types.GenerateContentResponse, list[genai_types.Part]]:
        base = config or genai_types.GenerateContentConfig()
        level = self._thinking.get(model)
        if level is not None:
            base = base.model_copy(
                update={"thinking_config": genai_types.ThinkingConfig(thinking_level=_GOOGLE_THINKING[level])}
            )

        def once(timeout: float) -> genai_types.GenerateContentResponse:
            attempt_config = base.model_copy(
                update={"http_options": genai_types.HttpOptions(timeout=round(timeout * 1000))}
            )
            r = self._client.models.generate_content(model=model, contents=contents, config=attempt_config)
            reason = _google_finish_name(r)
            if reason in MALFORMED_FINISH:
                raise _Malformed(f"Gemini {model} stopped with finish reason {reason}")
            return r

        r = _with_retries(
            once, _classify_google, self._sleep, f"Gemini {model}", timeout_s, self._clock, model
        )
        if not r.candidates:
            block = r.prompt_feedback.block_reason if r.prompt_feedback else None
            raise LLMError(f"Gemini {model} returned no candidates (block reason: {block})")
        cand = r.candidates[0]
        if cand.finish_reason not in _GOOGLE_OK_FINISH:
            raise LLMError(f"Gemini {model} stopped with finish reason {cand.finish_reason}")
        return r, list(cand.content.parts or []) if cand.content else []

    @staticmethod
    def _text(parts: list[genai_types.Part]) -> str:
        return "".join(p.text for p in parts if p.text and not p.thought).strip()

    def complete(self, model: str, prompt: str) -> Completion:
        r, parts = self._generate(model, prompt, None)
        input_tokens, output_tokens = _google_usage(r)
        return Completion(
            text=self._text(parts), model=model, input_tokens=input_tokens, output_tokens=output_tokens
        )

    def _complete_json_once(self, model: str, prompt: str, schema: dict[str, Any]) -> Completion:
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json", response_json_schema=schema
        )
        r, parts = self._generate(model, prompt, config)
        input_tokens, output_tokens = _google_usage(r)
        return Completion(
            text=self._text(parts), model=model, input_tokens=input_tokens, output_tokens=output_tokens
        )

    def complete_json(
        self, model: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Completion]:
        return _complete_json_with(
            lambda p: self._complete_json_once(model, p, schema), model, prompt, schema
        )

    def _contents(self, messages: list[Message]) -> tuple[str | None, list[genai_types.Content]]:
        names = _tool_names(messages)
        system: list[str] = []
        contents: list[genai_types.Content] = []
        for m in messages:
            if m.role == "system":
                system.append(m.content)
            elif m.role == "user":
                # Always its own turn. Vertex AI rejects a turn that mixes function responses with text
                # ("Requests ending with a model turn are not supported", seen live 2026-10-17), so a
                # note after tool results (the step monitor's) must not join their turn.
                contents.append(genai_types.Content(role="user", parts=[genai_types.Part(text=m.content)]))
            elif m.role == "assistant":
                parts = [genai_types.Part(text=m.content)] if m.content else []
                for tc in m.tool_calls:
                    original = self._call_parts.get(tc.id)
                    parts.append(
                        original
                        if original is not None
                        else genai_types.Part(
                            function_call=genai_types.FunctionCall(name=tc.name, args=tc.args)
                        )
                    )
                if not parts:
                    raise ValueError("assistant message with neither content nor tool calls")
                contents.append(genai_types.Content(role="model", parts=parts))
            else:
                if m.tool_call_id is None or m.tool_call_id not in names:
                    raise ValueError(f"tool message answers unknown tool call {m.tool_call_id!r}")
                original = self._call_parts.get(m.tool_call_id)
                provider_id = (
                    original.function_call.id if original is not None and original.function_call else None
                )
                part = genai_types.Part(
                    function_response=genai_types.FunctionResponse(
                        id=provider_id, name=names[m.tool_call_id], response={"output": m.content}
                    )
                )
                # Gemini wants all responses to one model turn in a single user turn.
                last = contents[-1] if contents else None
                if (
                    last is not None
                    and last.role == "user"
                    and last.parts
                    and last.parts[-1].function_response
                ):
                    last.parts.append(part)
                else:
                    contents.append(genai_types.Content(role="user", parts=[part]))
        return ("\n\n".join(system) if system else None), contents

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec]) -> Turn:
        started = time.perf_counter()
        system, contents = self._contents(messages)
        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            tools=[
                genai_types.Tool(
                    function_declarations=[
                        genai_types.FunctionDeclaration(
                            name=t.name, description=t.description, parameters_json_schema=t.parameters
                        )
                        for t in tools
                    ]
                )
            ]
            if tools
            else None,
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
        )
        r, parts = self._generate(model, contents, config, CHAT_TIMEOUT_S)
        calls: list[ToolCall] = []
        for p in parts:
            fc = p.function_call
            if fc is None:
                continue
            if not fc.name:
                raise LLMError(f"Gemini {model} returned a function call without a name")
            call_id = fc.id or f"call_{uuid.uuid4().hex}"
            self._call_parts.put(call_id, p)
            calls.append(ToolCall(id=call_id, name=fc.name, args=dict(fc.args or {})))
        input_tokens, output_tokens = _google_usage(r)
        return Turn(
            text=self._text(parts),
            tool_calls=calls,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=_elapsed_ms(started),
        )


# ---------------------------------------------------------------------------------------------------------
# Gemini through OpenRouter (OpenAI-compatible API)

_OPENROUTER_OK_FINISH = {"stop", "tool_calls"}


def _classify_openai(e: BaseException) -> Classification:
    if isinstance(e, openai.APITimeoutError):
        return "transient"
    if isinstance(e, openai.APIStatusError):
        return _status_kind(e.status_code)
    if isinstance(e, openai.OpenAIError):
        return "fatal"
    return None


class OpenRouterLLM:
    def __init__(
        self,
        api_key: str | None,
        *,
        client: openai.OpenAI | None = None,
        thinking: dict[str, ThinkingLevel] | None = None,
        sleep: Sleep = time.sleep,
        clock: Clock = time.perf_counter,
    ) -> None:
        if client is None:
            if not api_key:
                raise ConfigError("OpenRouterLLM needs an OpenRouter API key")
            # Retries and per-attempt timeouts are ours (_with_retries), so the SDK's own retries are off.
            client = openai.OpenAI(
                base_url=OPENROUTER_BASE_URL, api_key=api_key, max_retries=0, timeout=REQUEST_TIMEOUT_S
            )
        self._client = client
        self._thinking = dict(thinking or {})
        self._sleep = sleep
        self._clock = clock
        self._reasoning = _BoundedStore()  # first tool call id of a turn -> that turn's reasoning_details

    def _create(self, timeout_s: float = REQUEST_TIMEOUT_S, **kwargs: Any) -> Any:
        model = kwargs["model"]
        level = self._thinking.get(model)
        if level is not None:
            kwargs["extra_body"] = {**kwargs.get("extra_body", {}), "reasoning": {"effort": level}}

        def once(timeout: float) -> Any:
            r = self._client.chat.completions.create(**kwargs, timeout=timeout)
            if not r.choices:
                err = (r.model_extra or {}).get("error") or {}
                code = err.get("code") if isinstance(err, dict) else None
                if isinstance(code, int) and _is_transient_status(code):
                    error = _Throttled if code == 429 else _Transient
                    raise error(f"OpenRouter error {code}: {err.get('message')}")
                raise LLMError(f"OpenRouter returned no choices for {model}: {err or 'no error given'}")
            # OpenRouter maps Gemini's finish reason to its own (a broken reply often to "stop" or "error")
            # and passes Gemini's on as native_finish_reason, which is what says the reply came out broken.
            native = (r.choices[0].model_extra or {}).get("native_finish_reason")
            if isinstance(native, str) and native.upper() in MALFORMED_FINISH:
                raise _Malformed(f"OpenRouter {model} stopped with native finish reason {native}")
            return r

        r = _with_retries(
            once, _classify_openai, self._sleep, f"OpenRouter {model}", timeout_s, self._clock, model
        )
        finish = r.choices[0].finish_reason
        if finish not in _OPENROUTER_OK_FINISH:
            raise LLMError(f"OpenRouter {model} stopped with finish reason {finish!r}")
        return r

    @staticmethod
    def _usage(r: Any) -> tuple[int | None, int | None]:
        # OpenRouter's completion_tokens already include reasoning (thinking) tokens.
        if r.usage is None:
            return None, None
        return r.usage.prompt_tokens, r.usage.completion_tokens

    def _completion(self, r: Any) -> Completion:
        input_tokens, output_tokens = self._usage(r)
        return Completion(
            text=(r.choices[0].message.content or "").strip(),
            model=r.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def complete(self, model: str, prompt: str) -> Completion:
        return self._completion(self._create(model=model, messages=[{"role": "user", "content": prompt}]))

    def complete_json(
        self, model: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Completion]:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "reply", "strict": True, "schema": schema},
        }

        def once(p: str) -> Completion:
            r = self._create(
                model=model, messages=[{"role": "user", "content": p}], response_format=response_format
            )
            return self._completion(r)

        return _complete_json_with(once, model, prompt, schema)

    def _messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        names = _tool_names(messages)
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role in ("system", "user"):
                out.append({"role": m.role, "content": m.content})
            elif m.role == "assistant":
                d: dict[str, Any] = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    d["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.args)},
                        }
                        for tc in m.tool_calls
                    ]
                    reasoning = self._reasoning.get(m.tool_calls[0].id)
                    if reasoning is not None:
                        d["reasoning_details"] = reasoning
                out.append(d)
            else:
                if m.tool_call_id is None or m.tool_call_id not in names:
                    raise ValueError(f"tool message answers unknown tool call {m.tool_call_id!r}")
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        return out

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec]) -> Turn:
        started = time.perf_counter()
        kwargs: dict[str, Any] = {"model": model, "messages": self._messages(messages)}
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        r = self._create(CHAT_TIMEOUT_S, **kwargs)
        msg = r.choices[0].message
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            if tc.type != "function":
                raise LLMError(f"OpenRouter {model} returned an unsupported tool call type {tc.type!r}")
            raw = tc.function.arguments
            try:
                args = json.loads(raw) if raw.strip() else {}  # an empty string is how no arguments are sent
            except json.JSONDecodeError as e:
                raise LLMError(
                    f"OpenRouter {model} sent invalid JSON arguments for {tc.function.name}: {e}"
                ) from e
            if not isinstance(args, dict):
                raise LLMError(f"OpenRouter {model} sent non-object arguments for {tc.function.name}")
            calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))
        reasoning = (msg.model_extra or {}).get("reasoning_details")
        if calls and reasoning:
            self._reasoning.put(calls[0].id, reasoning)
        input_tokens, output_tokens = self._usage(r)
        return Turn(
            text=(msg.content or "").strip(),
            tool_calls=calls,
            model=r.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=_elapsed_ms(started),
        )


def thinking_levels(s: Settings) -> dict[str, ThinkingLevel]:
    """Model id -> pinned thinking level; the fallback agent model runs at the agent's level. One model
    cannot serve two roles at different levels."""
    agents = [s.agent_model] + ([s.fallback_model] if s.fallback_model is not None else [])
    for agent in agents:
        if agent == s.monitor_model and s.agent_thinking != s.monitor_thinking:
            raise ConfigError(
                f"{agent} is both an agent and the monitor model but has two thinking levels "
                f"({s.agent_thinking}, {s.monitor_thinking})"
            )
    return {**{a: s.agent_thinking for a in agents}, s.monitor_model: s.monitor_thinking}


def build_llm(s: Settings) -> LLM:
    http_options = genai_types.HttpOptions(timeout=int(REQUEST_TIMEOUT_S * 1000))
    thinking = thinking_levels(s)
    if s.provider == "vertex":
        return GoogleLLM(
            genai.Client(vertexai=True, project=s.project, location=s.location, http_options=http_options),
            thinking=thinking,
        )
    if s.provider == "aistudio":
        return GoogleLLM(genai.Client(api_key=s.api_key, http_options=http_options), thinking=thinking)
    return OpenRouterLLM(api_key=s.api_key, thinking=thinking)
