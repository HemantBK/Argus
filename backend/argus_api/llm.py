"""One interface over the three ways we reach Gemini.

- vertex:     Gemini on Vertex AI (the deployed app; the only provider allowed in cloud mode)
- aistudio:   Gemini through a Google AI Studio API key (local development)
- openrouter: Gemini through OpenRouter's OpenAI-compatible API (local development only)

Every provider offers the same three calls: complete (plain text), complete_json (structured output validated
against a JSON Schema) and chat (one model turn with function calling). Provider failures surface as LLMError;
transient ones (HTTP 429, 5xx, timeouts) are retried with exponential backoff first. Every failed attempt is
logged (llm_attempt_failed, WARNING) with what happens next, so retries are visible in Cloud Logging.

Each attempt has its own timeout, sized to the call: a chat turn returns a few dozen tokens, so it is cut
sooner than a structured call that writes a whole finding. Inside call_scope(deadline) every call also ends by
the deadline: an attempt's timeout never runs past it, and no retry starts without time to finish, so one
stalled call cannot carry a clause past the run budget; it fails as LLMError saying so.

A model may be given a pinned thinking level (config.AGENT_THINKING, config.MONITOR_THINKING): Gemini's
thinking_level on google-genai, reasoning effort on OpenRouter. Models without one use the provider default.

Token counts are what the provider bills: for Gemini the output count includes thinking tokens.
"""

import json
import logging
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


@dataclass(frozen=True)
class _Scope:
    deadline: float | None  # on the time.perf_counter clock
    fields: dict[str, Any]


_scope: ContextVar[_Scope | None] = ContextVar("argus_llm_scope", default=None)


@contextmanager
def call_scope(deadline: float | None, **log_fields: Any) -> Iterator[None]:
    """Every LLM call made inside (in this context) ends by `deadline` (a time.perf_counter() value; None
    for no deadline), and its retry logs carry `log_fields` (run_id, clause_id). An inner scope cannot
    extend an outer deadline."""
    outer = _scope.get()
    if outer is not None and outer.deadline is not None:
        deadline = outer.deadline if deadline is None else min(deadline, outer.deadline)
    fields = {**(outer.fields if outer is not None else {}), **log_fields}
    token = _scope.set(_Scope(deadline, fields))
    try:
        yield
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


class LLMError(RuntimeError):
    pass


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


Classification = Literal["transient", "fatal"] | None


def _attempt_timeout(timeout_s: float, scope: _Scope | None, clock: Clock, what: str, attempt: int) -> float:
    """This attempt's timeout: the call's own limit, cut to the time left before the scope's deadline."""
    if scope is None or scope.deadline is None:
        return timeout_s
    left = scope.deadline - clock()
    if left < MIN_ATTEMPT_S:
        raise LLMError(
            f"{what}: attempt {attempt} not started, {max(left, 0.0):.1f} s left before the run deadline"
        )
    return min(timeout_s, left)


def _with_retries(
    call: Callable[[float], T],
    classify: Callable[[BaseException], Classification],
    sleep: Sleep,
    what: str,
    timeout_s: float,
    clock: Clock,
) -> T:
    """Run `call(timeout)` with retries on transient failures. Each failed attempt is logged."""
    scope = _scope.get()
    fields = scope.fields if scope is not None else {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        timeout = _attempt_timeout(timeout_s, scope, clock, what, attempt)
        started = clock()
        try:
            return call(timeout)
        except LLMError:
            raise
        except Exception as e:
            kind = "transient" if isinstance(e, _Transient) else classify(e)
            if kind is None:
                raise  # not a provider failure: a bug, let it surface as is
            failure = {
                **fields,
                "call": what,
                "attempt": attempt,
                "max_attempts": MAX_ATTEMPTS,
                "error_type": type(e).__name__,
                "error": str(e)[:300],
                "attempt_ms": round((clock() - started) * 1000),
                "timeout_s": round(timeout, 1),
            }
            backoff = BACKOFF_BASE_S * 2 ** (attempt - 1)
            if kind == "fatal":
                outcome, error = "fatal", LLMError(f"{what} failed: {e}")
            elif attempt == MAX_ATTEMPTS:
                outcome, error = "gave_up", LLMError(f"{what} failed after {MAX_ATTEMPTS} tries: {e}")
            elif (
                scope is not None
                and scope.deadline is not None
                and (left := scope.deadline - clock() - backoff) < MIN_ATTEMPT_S
            ):
                outcome = "deadline"
                error = LLMError(
                    f"{what} failed on try {attempt}: {e}; not retried, {max(left, 0.0):.1f} s would be left "
                    "before the run deadline"
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


def _is_transient_status(code: int | None) -> bool:
    return code is not None and (code == 429 or code >= 500)


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
        return "transient" if _is_transient_status(e.code) else "fatal"
    if isinstance(e, _GOOGLE_TIMEOUTS):
        return "transient"
    return None


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
            return self._client.models.generate_content(model=model, contents=contents, config=attempt_config)

        r = _with_retries(once, _classify_google, self._sleep, f"Gemini {model}", timeout_s, self._clock)
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
                text = genai_types.Part(text=m.content)
                last = contents[-1] if contents else None
                if last is not None and last.role == "user" and last.parts:
                    last.parts.append(text)  # a note after tool results joins their turn: roles alternate
                else:
                    contents.append(genai_types.Content(role="user", parts=[text]))
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
        return "transient" if _is_transient_status(e.status_code) else "fatal"
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
                    raise _Transient(f"OpenRouter error {code}: {err.get('message')}")
                raise LLMError(f"OpenRouter returned no choices for {model}: {err or 'no error given'}")
            return r

        r = _with_retries(once, _classify_openai, self._sleep, f"OpenRouter {model}", timeout_s, self._clock)
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
    """Model id -> pinned thinking level. One model cannot serve both roles at different levels."""
    if s.agent_model == s.monitor_model and s.agent_thinking != s.monitor_thinking:
        raise ConfigError(
            f"{s.agent_model} is both the agent and the monitor model but has two thinking levels "
            f"({s.agent_thinking}, {s.monitor_thinking})"
        )
    return {s.agent_model: s.agent_thinking, s.monitor_model: s.monitor_thinking}


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
