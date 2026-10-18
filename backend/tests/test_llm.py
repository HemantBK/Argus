"""LLM layer: Gemini and OpenRouter adapters against mocked clients (no network), plus FakeLLM."""

import json
import warnings
from typing import Any

import httpx
import httpx2
import openai
import pytest
from google.genai import errors as genai_errors
from google.genai import types as gt
from openai.types.chat import ChatCompletion

from argus_api.config import (
    AGENT_THINKING,
    FALLBACK_MODELS,
    MONITOR_THINKING,
    PINNED_MODELS,
    ConfigError,
    Settings,
)
from argus_api.llm import (
    BACKOFF_BASE_S,
    CHAT_TIMEOUT_S,
    JSON_ATTEMPTS,
    LLM,
    MALFORMED_ATTEMPTS,
    MALFORMED_BACKOFF_S,
    MALFORMED_FINISH,
    MAX_ATTEMPTS,
    MIN_ATTEMPT_S,
    PRICES_USD_PER_MTOK,
    REQUEST_TIMEOUT_S,
    THROTTLED_ATTEMPTS,
    GoogleLLM,
    LLMError,
    Message,
    OpenRouterLLM,
    ToolCall,
    ToolSpec,
    call_scope,
    estimate_cost_usd,
    thinking_levels,
)
from argus_api.llm_fake import FakeLLM, FakeLLMExhausted, turn

MODEL = "gemini-test"
OR_MODEL = next(iter(PRICES_USD_PER_MTOK))
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"label": {"type": "string", "enum": ["gap", "compliant"]}},
    "required": ["label"],
    "additionalProperties": False,
}
TOOL = ToolSpec(
    name="read_paragraph",
    description="Read one RBI paragraph.",
    parameters={"type": "object", "properties": {"para": {"type": "string"}}, "required": ["para"]},
)


class Sleeps(list[float]):
    def __call__(self, seconds: float) -> None:
        self.append(seconds)


def expected_backoff(retries: int) -> list[float]:
    return [BACKOFF_BASE_S * 2**i for i in range(retries)]


@pytest.fixture(autouse=True)
def no_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Throttled waits are jittered to 50-100%; with the top of the range they equal expected_backoff."""
    monkeypatch.setattr("argus_api.llm._jitter", lambda: 1.0)


# --------------------------------------------------------------------------------------------- Gemini mocks


class FakeModels:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> gt.GenerateContentResponse:
        self.requests.append(kwargs)
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, gt.GenerateContentResponse)
        return item


class FakeGenaiClient:
    def __init__(self, replies: list[Any]) -> None:
        self.models = FakeModels(replies)


def g_response(
    parts: list[gt.Part],
    prompt: int | None = 11,
    candidates: int | None = 7,
    thoughts: int | None = None,
    finish: gt.FinishReason = gt.FinishReason.STOP,
) -> gt.GenerateContentResponse:
    return gt.GenerateContentResponse(
        candidates=[gt.Candidate(content=gt.Content(role="model", parts=parts), finish_reason=finish)],
        usage_metadata=gt.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt, candidates_token_count=candidates, thoughts_token_count=thoughts
        ),
    )


def g_text(text: str, **kw: Any) -> gt.GenerateContentResponse:
    return g_response([gt.Part(text=text)], **kw)


def google(replies: list[Any]) -> tuple[GoogleLLM, FakeModels, Sleeps]:
    client = FakeGenaiClient(replies)
    sleeps = Sleeps()
    return GoogleLLM(client, sleep=sleeps), client.models, sleeps  # type: ignore[arg-type]


def test_google_counts_thinking_tokens_and_hides_thought_text() -> None:
    llm, _, _ = google(
        [g_response([gt.Part(text="hidden plan", thought=True), gt.Part(text=" ready ")], thoughts=20)]
    )
    c = llm.complete(MODEL, "hi")
    assert c.text == "ready"
    assert (c.input_tokens, c.output_tokens) == (11, 7 + 20)


def test_google_complete_json_sends_schema_and_validates() -> None:
    llm, models, _ = google([g_text(json.dumps({"label": "gap"}))])
    value, c = llm.complete_json(MODEL, "judge", SCHEMA)
    assert value == {"label": "gap"}
    config = models.requests[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == SCHEMA
    assert json.loads(c.text) == value


def test_google_complete_json_retries_once_and_sums_tokens() -> None:
    llm, models, _ = google([g_text('{"label": "maybe"}'), g_text('{"label": "compliant"}')])
    value, c = llm.complete_json(MODEL, "judge", SCHEMA)
    assert value == {"label": "compliant"}
    assert len(models.requests) == 2
    assert models.requests[1]["contents"].startswith("judge")
    assert "label" in models.requests[1]["contents"]  # the retry says what was wrong
    assert (c.input_tokens, c.output_tokens) == (2 * 11, 2 * 7)


def test_google_complete_json_gives_up_after_the_retry() -> None:
    llm, models, _ = google([g_text("not json") for _ in range(JSON_ATTEMPTS)])
    with pytest.raises(LLMError, match="invalid JSON"):
        llm.complete_json(MODEL, "judge", SCHEMA)
    assert len(models.requests) == JSON_ATTEMPTS


def test_google_chat_tool_round_trip_replays_thought_signature() -> None:
    signature = b"sig-bytes"
    call_part = gt.Part(
        function_call=gt.FunctionCall(name=TOOL.name, args={"para": "10(4)"}), thought_signature=signature
    )
    second_part = gt.Part(function_call=gt.FunctionCall(name=TOOL.name, args={"para": "9(ii)"}))
    llm, models, _ = google([g_response([call_part, second_part]), g_text("done")])
    msgs = [Message("system", "be careful"), Message("user", "read it")]
    t = llm.chat(MODEL, msgs, [TOOL])

    config = models.requests[0]["config"]
    assert config.system_instruction == "be careful"
    decl = config.tools[0].function_declarations[0]
    assert (decl.name, decl.parameters_json_schema) == (TOOL.name, TOOL.parameters)
    assert [(c.name, c.args) for c in t.tool_calls] == [
        (TOOL.name, {"para": "10(4)"}),
        (TOOL.name, {"para": "9(ii)"}),
    ]
    assert len({c.id for c in t.tool_calls}) == 2

    msgs += [
        Message("assistant", t.text, tool_calls=t.tool_calls),
        Message("tool", "text of 10(4)", tool_call_id=t.tool_calls[0].id),
        Message("tool", "text of 9(ii)", tool_call_id=t.tool_calls[1].id),
    ]
    final = llm.chat(MODEL, msgs, [TOOL])
    assert final.text == "done" and final.tool_calls == []

    contents = models.requests[1]["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[0].thought_signature == signature
    responses = [p.function_response for p in contents[2].parts]
    assert [(r.name, r.response) for r in responses] == [
        (TOOL.name, {"output": "text of 10(4)"}),
        (TOOL.name, {"output": "text of 9(ii)"}),
    ]


def test_google_chat_rejects_tool_message_for_unknown_call() -> None:
    llm, _, _ = google([])
    with pytest.raises(ValueError, match="unknown tool call"):
        llm.chat(MODEL, [Message("user", "x"), Message("tool", "y", tool_call_id="nope")], [TOOL])


def test_google_retries_transient_errors_with_backoff() -> None:
    transient = [genai_errors.ServerError(503, {}), httpx.ReadTimeout("slow")]
    llm, models, sleeps = google([transient[i % 2] for i in range(MAX_ATTEMPTS - 1)] + [g_text("ok")])
    assert llm.complete(MODEL, "hi").text == "ok"
    assert len(models.requests) == MAX_ATTEMPTS
    assert sleeps == expected_backoff(MAX_ATTEMPTS - 1)


def test_google_gives_up_after_max_attempts() -> None:
    llm, models, sleeps = google([genai_errors.ServerError(503, {}) for _ in range(MAX_ATTEMPTS)])
    with pytest.raises(LLMError, match=f"after {MAX_ATTEMPTS} tries"):
        llm.complete(MODEL, "hi")
    assert len(models.requests) == MAX_ATTEMPTS
    assert sleeps == expected_backoff(MAX_ATTEMPTS - 1)


def test_google_retries_a_busy_429_more_than_a_stall() -> None:
    # The candidate revision's smoke review on 2026-10-17 failed on two 429s one second apart.
    busy = [genai_errors.ClientError(429, {}) for _ in range(THROTTLED_ATTEMPTS - 1)]
    llm, models, sleeps = google([*busy, g_text("ok")])
    assert llm.complete(MODEL, "hi").text == "ok"
    assert len(models.requests) == THROTTLED_ATTEMPTS > MAX_ATTEMPTS
    assert sleeps == expected_backoff(THROTTLED_ATTEMPTS - 1)


def test_google_gives_up_on_429_after_throttled_attempts() -> None:
    llm, models, _ = google([genai_errors.ClientError(429, {}) for _ in range(THROTTLED_ATTEMPTS)])
    with pytest.raises(LLMError, match=f"after {THROTTLED_ATTEMPTS} tries"):
        llm.complete(MODEL, "hi")
    assert len(models.requests) == THROTTLED_ATTEMPTS


def test_a_stall_after_429s_keeps_the_stall_limit() -> None:
    # A 503 on the third try is past MAX_ATTEMPTS: stalls never get the extra tries 429s get.
    replies = [
        genai_errors.ClientError(429, {}),
        genai_errors.ClientError(429, {}),
        genai_errors.ServerError(503, {}),
    ]
    llm, models, _ = google(replies)
    with pytest.raises(LLMError, match="after 3 tries"):
        llm.complete(MODEL, "hi")
    assert len(models.requests) == 3


def test_throttled_waits_are_jittered_between_half_and_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("argus_api.llm._jitter", lambda: 0.0)
    llm, _, sleeps = google(
        [genai_errors.ClientError(429, {}), genai_errors.ClientError(429, {}), g_text("ok")]
    )
    llm.complete(MODEL, "hi")
    assert sleeps == [w / 2 for w in expected_backoff(2)]


def test_google_does_not_retry_client_errors() -> None:
    llm, models, sleeps = google([genai_errors.ClientError(400, {})])
    with pytest.raises(LLMError):
        llm.complete(MODEL, "hi")
    assert len(models.requests) == 1 and sleeps == []


@pytest.mark.parametrize(
    "reply",
    [
        g_text("cut off", finish=gt.FinishReason.MAX_TOKENS),
        gt.GenerateContentResponse(candidates=[]),
    ],
)
def test_google_unusable_reply_is_an_error(reply: gt.GenerateContentResponse) -> None:
    llm, _, _ = google([reply])
    with pytest.raises(LLMError):
        llm.complete(MODEL, "hi")


# ----------------------------------------------------------------------------------------- OpenRouter mocks


class FakeCompletions:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> ChatCompletion:
        self.requests.append(json.loads(json.dumps(kwargs)))  # snapshot what was sent
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ChatCompletion.model_validate(item)


class FakeOpenAI:
    def __init__(self, replies: list[Any]) -> None:
        self.completions = FakeCompletions(replies)
        self.chat = self


def or_reply(
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish: str = "stop",
    extra: dict[str, Any] | None = None,
    usage: tuple[int, int] = (10, 30),
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content, **(extra or {})}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": OR_MODEL,
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)},
    }


def or_call(call_id: str, args: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": TOOL.name, "arguments": args}}


def openrouter(replies: list[Any]) -> tuple[OpenRouterLLM, FakeCompletions, Sleeps]:
    client = FakeOpenAI(replies)
    sleeps = Sleeps()
    return OpenRouterLLM(None, client=client, sleep=sleeps), client.completions, sleeps  # type: ignore[arg-type]


REQ = httpx2.Request("POST", "https://openrouter.test/chat/completions")


def status_error(cls: type[openai.APIStatusError], code: int) -> openai.APIStatusError:
    return cls("boom", response=httpx2.Response(code, request=REQ), body=None)


def test_openrouter_complete_json_uses_json_schema_response_format() -> None:
    llm, api, _ = openrouter([or_reply(json.dumps({"label": "gap"}))])
    value, c = llm.complete_json(OR_MODEL, "judge", SCHEMA)
    assert value == {"label": "gap"}
    fmt = api.requests[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["schema"] == SCHEMA
    assert (c.input_tokens, c.output_tokens) == (10, 30)


def test_openrouter_complete_json_invalid_twice_is_an_error() -> None:
    llm, api, _ = openrouter([or_reply('{"label": 3}') for _ in range(JSON_ATTEMPTS)])
    with pytest.raises(LLMError, match="invalid JSON"):
        llm.complete_json(OR_MODEL, "judge", SCHEMA)
    assert len(api.requests) == JSON_ATTEMPTS


def test_openrouter_chat_tool_round_trip_replays_reasoning() -> None:
    reasoning = [{"type": "reasoning.encrypted", "data": "opaque", "id": "c1"}]
    llm, api, _ = openrouter(
        [
            or_reply(
                tool_calls=[or_call("c1", '{"para": "10(4)"}')],
                finish="tool_calls",
                extra={"reasoning_details": reasoning},
            ),
            or_reply("done"),
        ]
    )
    msgs = [Message("system", "be careful"), Message("user", "read it")]
    t = llm.chat(OR_MODEL, msgs, [TOOL])
    assert t.tool_calls == [ToolCall(id="c1", name=TOOL.name, args={"para": "10(4)"})]
    assert api.requests[0]["tools"] == [
        {
            "type": "function",
            "function": {"name": TOOL.name, "description": TOOL.description, "parameters": TOOL.parameters},
        }
    ]

    msgs += [
        Message("assistant", t.text, tool_calls=t.tool_calls),
        Message("tool", "text", tool_call_id="c1"),
    ]
    assert llm.chat(OR_MODEL, msgs, [TOOL]).text == "done"
    sent = api.requests[1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool"]
    assert sent[2]["reasoning_details"] == reasoning
    assert json.loads(sent[2]["tool_calls"][0]["function"]["arguments"]) == {"para": "10(4)"}
    assert sent[3] == {"role": "tool", "tool_call_id": "c1", "content": "text"}


def test_openrouter_chat_without_tools_sends_no_tools_field() -> None:
    llm, api, _ = openrouter([or_reply("hi")])
    llm.chat(OR_MODEL, [Message("user", "x")], [])
    assert "tools" not in api.requests[0]


def test_openrouter_invalid_tool_arguments_are_an_error() -> None:
    llm, _, _ = openrouter([or_reply(tool_calls=[or_call("c1", "{broken")], finish="tool_calls")])
    with pytest.raises(LLMError, match="invalid JSON arguments"):
        llm.chat(OR_MODEL, [Message("user", "x")], [TOOL])


def test_openrouter_retries_rate_limits_and_timeouts() -> None:
    transient = [status_error(openai.RateLimitError, 429), openai.APITimeoutError(request=REQ)]
    llm, api, sleeps = openrouter([transient[i % 2] for i in range(MAX_ATTEMPTS - 1)] + [or_reply("ok")])
    assert llm.complete(OR_MODEL, "hi").text == "ok"
    assert len(api.requests) == MAX_ATTEMPTS
    assert sleeps == expected_backoff(MAX_ATTEMPTS - 1)


def test_openrouter_treats_a_429_inside_a_200_body_as_throttled() -> None:
    busy = {**or_reply("x"), "choices": [], "error": {"code": 429, "message": "busy"}}
    llm, api, _ = openrouter([busy] * (THROTTLED_ATTEMPTS - 1) + [or_reply("ok")])
    assert llm.complete(OR_MODEL, "hi").text == "ok"
    assert len(api.requests) == THROTTLED_ATTEMPTS


def test_openrouter_retries_an_error_inside_a_200_body() -> None:
    body_error = {**or_reply("x"), "choices": [], "error": {"code": 502, "message": "upstream"}}
    llm, api, sleeps = openrouter([body_error, or_reply("ok")])
    assert llm.complete(OR_MODEL, "hi").text == "ok"
    assert sleeps == expected_backoff(1)


def test_openrouter_gives_up_after_max_attempts() -> None:
    llm, api, _ = openrouter([status_error(openai.InternalServerError, 500) for _ in range(MAX_ATTEMPTS)])
    with pytest.raises(LLMError, match=f"after {MAX_ATTEMPTS} tries"):
        llm.complete(OR_MODEL, "hi")
    assert len(api.requests) == MAX_ATTEMPTS


def test_openrouter_does_not_retry_auth_errors() -> None:
    llm, api, sleeps = openrouter([status_error(openai.AuthenticationError, 401)])
    with pytest.raises(LLMError):
        llm.complete(OR_MODEL, "hi")
    assert len(api.requests) == 1 and sleeps == []


def test_openrouter_truncated_reply_is_an_error() -> None:
    llm, _, _ = openrouter([or_reply("cut", finish="length")])
    with pytest.raises(LLMError, match="length"):
        llm.complete(OR_MODEL, "hi")


# ------------------------------------------------------------------------------------------------- cost


def test_cost_comes_from_the_price_table() -> None:
    for model, (price_in, price_out) in PRICES_USD_PER_MTOK.items():
        assert estimate_cost_usd(model, 1_000_000, 2_000_000) == pytest.approx(price_in + 2 * price_out)


def test_every_pinned_model_of_every_provider_is_priced() -> None:
    for provider, models in PINNED_MODELS.items():
        for model in models:
            assert model in PRICES_USD_PER_MTOK, (provider, model)
            assert estimate_cost_usd(model, 1, 1) is not None


def test_openrouter_sends_the_pinned_thinking_level_only_for_its_model() -> None:
    client = FakeOpenAI([or_reply("a"), or_reply("b")])
    llm = OpenRouterLLM(None, client=client, thinking={OR_MODEL: AGENT_THINKING}, sleep=Sleeps())  # type: ignore[arg-type]
    other = next(m for m in PRICES_USD_PER_MTOK if m != OR_MODEL)
    llm.complete(OR_MODEL, "hi")
    llm.complete(other, "hi")
    first, second = client.completions.requests
    assert first["extra_body"] == {"reasoning": {"effort": AGENT_THINKING}}
    assert "extra_body" not in second


def test_google_sends_the_pinned_thinking_level_only_for_its_model() -> None:
    client = FakeGenaiClient([g_text("a"), g_text("b"), g_text("{}")])
    llm = GoogleLLM(client, thinking={"pinned-model": AGENT_THINKING}, sleep=Sleeps())  # type: ignore[arg-type]
    llm.complete("pinned-model", "hi")
    llm.complete("other-model", "hi")
    llm.complete_json("pinned-model", "hi", {"type": "object"})
    plain, other, structured = client.models.requests
    assert plain["config"].thinking_config.thinking_level.value.lower() == AGENT_THINKING
    assert other["config"].thinking_config is None
    assert structured["config"].thinking_config.thinking_level.value.lower() == AGENT_THINKING
    assert structured["config"].response_mime_type == "application/json"  # the call's own config is kept


def test_cost_is_unknown_without_price_or_tokens() -> None:
    assert estimate_cost_usd("unknown/model", 10, 10) is None
    assert estimate_cost_usd(OR_MODEL, None, 10) is None
    assert estimate_cost_usd(OR_MODEL, 10, None) is None


# ---------------------------------------------------------------------------------------------- FakeLLM


def test_fake_llm_scripts_and_records_calls() -> None:
    call = ToolCall(id="t1", name=TOOL.name, args={"para": "10(4)"})
    fake = FakeLLM(completions=["ready"], json_replies=[{"label": "gap"}], turns=[turn(tool_calls=[call])])
    llm: LLM = fake  # satisfies the Protocol
    assert llm.complete(MODEL, "p").text == "ready"
    assert llm.complete_json(MODEL, "q", SCHEMA)[0] == {"label": "gap"}
    msgs = [Message("user", "x")]
    assert llm.chat(MODEL, msgs, [TOOL]).tool_calls == [call]
    msgs.append(Message("user", "later"))
    assert [c.method for c in fake.calls] == ["complete", "complete_json", "chat"]
    assert fake.calls[2].messages == [Message("user", "x")]  # snapshot, not the live list
    assert fake.remaining() == {"complete": 0, "complete_json": 0, "chat": 0}


def test_fake_llm_raises_scripted_errors_and_when_exhausted() -> None:
    fake = FakeLLM(json_replies=[LLMError("down"), {"label": "nope"}])
    with pytest.raises(LLMError, match="down"):
        fake.complete_json(MODEL, "q", SCHEMA)
    with pytest.raises(LLMError, match="schema"):
        fake.complete_json(MODEL, "q", SCHEMA)
    with pytest.raises(FakeLLMExhausted):
        fake.chat(MODEL, [], [])


def test_each_role_gets_its_pinned_thinking_level() -> None:
    s = Settings(
        mode="local",
        provider="openrouter",
        project="p",
        location=None,
        api_key="k",
        agent_model=PINNED_MODELS["openrouter"][0],
        monitor_model=PINNED_MODELS["openrouter"][1],
    )
    assert thinking_levels(s) == {s.agent_model: AGENT_THINKING, s.monitor_model: MONITOR_THINKING}
    same = Settings(**{**s.__dict__, "monitor_model": s.agent_model})
    if AGENT_THINKING != MONITOR_THINKING:
        with pytest.raises(ConfigError, match="two thinking levels"):
            thinking_levels(same)


# --------------------------------------------------------------------------------- timeouts and deadlines


class Clock:
    """A fake perf_counter: advances only when the code under test sleeps (or a test moves it)."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def openrouter_clocked(replies: list[Any]) -> tuple[OpenRouterLLM, FakeCompletions, Clock]:
    client = FakeOpenAI(replies)
    clock = Clock()
    return OpenRouterLLM(None, client=client, sleep=clock.sleep, clock=clock), client.completions, clock  # type: ignore[arg-type]


def test_each_call_shape_gets_its_own_attempt_timeout() -> None:
    llm, api, _ = openrouter_clocked([or_reply("x"), or_reply(json.dumps({"label": "gap"}))])
    llm.chat(OR_MODEL, [Message("user", "x")], [TOOL])
    llm.complete_json(OR_MODEL, "judge", SCHEMA)
    assert [r["timeout"] for r in api.requests] == [CHAT_TIMEOUT_S, REQUEST_TIMEOUT_S]


def test_google_sends_the_attempt_timeout_per_request() -> None:
    llm, models, _ = google([g_text("ok"), g_text("ok")])
    llm.complete(MODEL, "hi")
    llm.chat(MODEL, [Message("user", "x")], [TOOL])
    timeouts = [r["config"].http_options.timeout for r in models.requests]
    assert timeouts == [round(REQUEST_TIMEOUT_S * 1000), round(CHAT_TIMEOUT_S * 1000)]


def test_an_attempt_never_runs_past_the_scope_deadline() -> None:
    llm, api, clock = openrouter_clocked([or_reply("ok")])
    left = REQUEST_TIMEOUT_S / 2
    with call_scope(clock() + left):
        llm.complete(OR_MODEL, "hi")
    assert api.requests[0]["timeout"] == left


def test_no_retry_starts_without_time_to_finish() -> None:
    llm, api, clock = openrouter_clocked([openai.APITimeoutError(request=REQ), or_reply("ok")])
    # After the first backoff, less than MIN_ATTEMPT_S would remain.
    with (
        call_scope(clock() + BACKOFF_BASE_S + MIN_ATTEMPT_S / 2),
        pytest.raises(LLMError, match="not retried"),
    ):
        llm.complete(OR_MODEL, "hi")
    assert len(api.requests) == 1 and clock.sleeps == []


def test_no_attempt_starts_after_the_deadline() -> None:
    llm, api, clock = openrouter_clocked([or_reply("ok")])
    with call_scope(clock() + MIN_ATTEMPT_S / 2), pytest.raises(LLMError, match="not started"):
        llm.complete(OR_MODEL, "hi")
    assert api.requests == []


def test_an_inner_scope_cannot_extend_the_deadline() -> None:
    llm, api, clock = openrouter_clocked([or_reply("ok")])
    with call_scope(clock() + MIN_ATTEMPT_S * 2), call_scope(clock() + REQUEST_TIMEOUT_S * 10):
        llm.complete(OR_MODEL, "hi")
    assert api.requests[0]["timeout"] == MIN_ATTEMPT_S * 2


def test_every_failed_attempt_is_logged_with_what_happens_next(caplog: pytest.LogCaptureFixture) -> None:
    llm, _, clock = openrouter_clocked([openai.APITimeoutError(request=REQ) for _ in range(MAX_ATTEMPTS)])
    with (
        caplog.at_level("WARNING", logger="argus.llm"),
        call_scope(clock() + 10 * REQUEST_TIMEOUT_S, run_id="r1", clause_id="c6"),
        pytest.raises(LLMError, match=f"after {MAX_ATTEMPTS} tries"),
    ):
        llm.complete(OR_MODEL, "hi")
    records = [r for r in caplog.records if r.getMessage() == "llm_attempt_failed"]
    fields = [r.json_fields for r in records]  # type: ignore[attr-defined]
    assert [f["outcome"] for f in fields] == ["retry"] * (MAX_ATTEMPTS - 1) + ["gave_up"]
    assert [f["attempt"] for f in fields] == list(range(1, MAX_ATTEMPTS + 1))
    assert all(r.levelname == "WARNING" for r in records)
    assert all(f["run_id"] == "r1" and f["clause_id"] == "c6" for f in fields)
    assert all(f["error_type"] == "APITimeoutError" for f in fields)
    assert [f["backoff_s"] for f in fields[:-1]] == expected_backoff(MAX_ATTEMPTS - 1)


def test_google_a_note_after_tool_results_gets_its_own_turn() -> None:
    """Vertex AI rejects a turn mixing function responses with text, so the monitor's note is separate."""
    llm, models, _ = google([g_text("ok")])
    tc = ToolCall(id="c1", name=TOOL.name, args={"para": "1"})
    llm.chat(
        MODEL,
        [
            Message("user", "x"),
            Message("assistant", "", tool_calls=[tc]),
            Message("tool", "{}", tool_call_id="c1"),
            Message("user", "monitor note"),
        ],
        [TOOL],
    )
    contents = models.requests[0]["contents"]
    assert [c.role for c in contents] == ["user", "model", "user", "user"]
    responses, note = contents[2].parts, contents[3].parts
    assert all(p.function_response is not None for p in responses)
    assert [p.text for p in note] == ["monitor note"]
    for c in contents:
        kinds = {
            "function_response" if p.function_response else "text" for p in c.parts if not p.function_call
        }
        assert len(kinds) <= 1, f"a {c.role} turn mixes {kinds}"


# ------------------------------------------------------------------- error kinds (runs.py's fallback trigger)


@pytest.mark.parametrize(
    ("replies", "kind"),
    [
        ([genai_errors.ClientError(429, {}) for _ in range(THROTTLED_ATTEMPTS)], "throttled"),
        ([genai_errors.ServerError(504, {}) for _ in range(MAX_ATTEMPTS)], "transient"),
        ([httpx.ReadTimeout("slow") for _ in range(MAX_ATTEMPTS)], "transient"),
        ([genai_errors.ClientError(400, {})], "fatal"),
    ],
    ids=["429", "504", "timeout", "400"],
)
def test_a_provider_failure_says_its_kind_and_model(replies: list[Any], kind: str) -> None:
    llm, _, _ = google(replies)
    with pytest.raises(LLMError) as raised:
        llm.complete(MODEL, "hi")
    assert (raised.value.kind, raised.value.model) == (kind, MODEL)
    assert raised.value.capacity == (kind != "fatal")


def test_a_try_cut_short_by_the_deadline_is_a_deadline_failure() -> None:
    llm, _, clock = openrouter_clocked([openai.APITimeoutError(request=REQ), or_reply("ok")])
    with call_scope(clock() + BACKOFF_BASE_S + MIN_ATTEMPT_S / 2), pytest.raises(LLMError) as raised:
        llm.complete(OR_MODEL, "hi")
    assert (raised.value.kind, raised.value.model, raised.value.capacity) == ("deadline", OR_MODEL, True)
    with call_scope(clock() + MIN_ATTEMPT_S / 2), pytest.raises(LLMError) as not_started:
        llm.complete(OR_MODEL, "hi")
    assert (not_started.value.kind, not_started.value.model) == ("deadline", OR_MODEL)


def test_an_unusable_reply_is_not_a_provider_failure() -> None:
    llm, _, _ = openrouter([or_reply("not json"), or_reply("still not json")])
    with pytest.raises(LLMError, match="invalid JSON") as raised:
        llm.complete_json(OR_MODEL, "judge", SCHEMA)
    assert (raised.value.kind, raised.value.model, raised.value.capacity) == (None, None, False)


def test_the_fallback_agent_model_runs_at_the_agents_thinking_level() -> None:
    agent, monitor = PINNED_MODELS["vertex"]
    s = Settings(
        mode="cloud",
        provider="vertex",
        project="p",
        location="global",
        api_key=None,
        agent_model=agent,
        monitor_model=monitor,
        fallback_model=FALLBACK_MODELS["vertex"],
    )
    assert thinking_levels(s) == {
        agent: AGENT_THINKING,
        FALLBACK_MODELS["vertex"]: AGENT_THINKING,
        monitor: MONITOR_THINKING,
    }
    if AGENT_THINKING != MONITOR_THINKING:
        with pytest.raises(ConfigError, match="two thinking levels"):
            thinking_levels(Settings(**{**s.__dict__, "fallback_model": monitor}))


# ------------------------------------------------- per-model deadlines and the cause behind a deadline error


def test_a_model_deadline_cuts_only_that_models_calls() -> None:
    other = OR_MODEL + "-monitor"
    llm, api, clock = openrouter_clocked([or_reply("ok"), or_reply("ok"), or_reply("ok")])
    own = REQUEST_TIMEOUT_S / 3
    with call_scope(clock() + REQUEST_TIMEOUT_S * 10, model_deadlines={OR_MODEL: clock() + own}):
        llm.complete(OR_MODEL, "hi")
        llm.complete(other, "hi")
        # An inner scope cannot extend a model's own deadline either.
        with call_scope(clock() + REQUEST_TIMEOUT_S * 10, model_deadlines={OR_MODEL: clock() + 100.0}):
            llm.complete(OR_MODEL, "hi")
    assert [r["timeout"] for r in api.requests] == [own, REQUEST_TIMEOUT_S, own]


def test_a_model_deadline_is_never_later_than_the_scope_deadline() -> None:
    llm, api, clock = openrouter_clocked([or_reply("ok")])
    with call_scope(clock() + MIN_ATTEMPT_S * 2, model_deadlines={OR_MODEL: clock() + 100.0}):
        llm.complete(OR_MODEL, "hi")
    assert api.requests[0]["timeout"] == MIN_ATTEMPT_S * 2


class _TimedClient:
    """Each request takes `took` seconds on the fake clock (capped at its timeout) and then raises `error`."""

    def __init__(self, clock: Clock, took: float, error: Any) -> None:
        self.clock, self.took, self.error = clock, took, error
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> Any:
        self.clock.now += min(self.took, kwargs["timeout"])
        raise self.error()


def timed(took: float, error: Any) -> tuple[OpenRouterLLM, Clock]:
    clock = Clock()
    client = _TimedClient(clock, took, error)
    return OpenRouterLLM(None, client=client, sleep=clock.sleep, clock=clock), clock  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("took", "error", "cause"),
    [
        # The try ran to the timeout our deadline had shortened: our budget ended it, not the provider.
        (100.0, lambda: openai.APITimeoutError(request=REQ), None),
        # A 504 that came back inside the shortened timeout: the provider failed, and no retry fitted.
        (3.0, lambda: status_error(openai.InternalServerError, 504), "transient"),
        # A 429: the provider was busy, and no retry fitted.
        (3.0, lambda: status_error(openai.RateLimitError, 429), "throttled"),
    ],
    ids=["cut_by_our_deadline", "504", "429"],
)
def test_a_deadline_error_carries_the_providers_failure_behind_it(
    took: float, error: Any, cause: Any
) -> None:
    llm, clock = timed(took, error)
    with call_scope(clock() + 4.0), pytest.raises(LLMError) as raised:
        llm.complete(OR_MODEL, "hi")
    e = raised.value
    assert (e.kind, e.model, e.cause, e.out_of_time) == ("deadline", OR_MODEL, cause, True)
    assert e.capacity == (cause is not None)


def test_a_try_that_timed_out_on_its_own_limit_is_the_providers_failure() -> None:
    """With the whole timeout available, a timeout is a stalled provider, even if no retry then fits."""
    llm, clock = timed(100.0, lambda: openai.APITimeoutError(request=REQ))
    with call_scope(clock() + REQUEST_TIMEOUT_S + 1.0), pytest.raises(LLMError) as raised:
        llm.complete(OR_MODEL, "hi")
    assert (raised.value.kind, raised.value.cause, raised.value.capacity) == ("deadline", "transient", True)


def test_a_give_up_is_the_providers_failure_by_its_kind() -> None:
    for kind in ("throttled", "transient"):
        e = LLMError("x", kind=kind, model=OR_MODEL)  # type: ignore[arg-type]
        assert (e.cause, e.capacity, e.out_of_time) == (kind, True, False)
    fatal = LLMError("x", kind="fatal", model=OR_MODEL)
    assert (fatal.cause, fatal.capacity) == (None, False)


def test_an_ended_model_deadline_gives_the_models_next_calls_the_scope_deadline() -> None:
    """runs.py ends the agent model's own deadline once its draft is written: from the next call on, the
    model's calls end by the scope's deadline, and an outer scope's deadline for it still holds."""
    llm, api, clock = openrouter_clocked([or_reply("ok") for _ in range(4)])
    own = REQUEST_TIMEOUT_S / 3
    with call_scope(clock() + REQUEST_TIMEOUT_S * 10, model_deadlines={OR_MODEL: clock() + own}) as scope:
        llm.complete(OR_MODEL, "hi")
        scope.end_model_deadline(OR_MODEL)
        llm.complete(OR_MODEL, "hi")
    with call_scope(clock() + REQUEST_TIMEOUT_S * 10, model_deadlines={OR_MODEL: clock() + own}):
        with call_scope(clock() + REQUEST_TIMEOUT_S * 10) as inner:
            inner.end_model_deadline(OR_MODEL)  # ends nothing of the outer scope's
            llm.complete(OR_MODEL, "hi")
    with call_scope(clock() + REQUEST_TIMEOUT_S * 10, model_deadlines={OR_MODEL: clock() + own}) as outer:
        with call_scope(clock() + REQUEST_TIMEOUT_S * 10):
            outer.end_model_deadline(OR_MODEL)  # an inner scope already open follows the outer one
            llm.complete(OR_MODEL, "hi")
    assert [r["timeout"] for r in api.requests] == [own, REQUEST_TIMEOUT_S, own, REQUEST_TIMEOUT_S]


def test_a_deadline_error_names_the_deadline_that_ended_the_call() -> None:
    llm, _, clock = openrouter_clocked([or_reply("ok"), or_reply("ok")])
    with (
        call_scope(clock() + 100.0, model_deadlines={OR_MODEL: clock() + MIN_ATTEMPT_S / 2}),
        pytest.raises(LLMError, match="left before the model's own deadline"),
    ):
        llm.complete(OR_MODEL, "hi")
    with call_scope(clock() + MIN_ATTEMPT_S / 2), pytest.raises(LLMError, match="left before the deadline$"):
        llm.complete(OR_MODEL, "hi")


@pytest.mark.parametrize(
    ("replies", "kind", "failures"),
    [
        # Two 429s, then a timeout: the timeout's limit (MAX_ATTEMPTS) has passed, so it gives up as transient
        # after 3 tries, and says what each try met.
        (
            [genai_errors.ClientError(429, {}), genai_errors.ClientError(429, {}), httpx.ReadTimeout("slow")],
            "transient",
            ("throttled", "throttled", "transient"),
        ),
        (
            [genai_errors.ClientError(429, {}) for _ in range(THROTTLED_ATTEMPTS)],
            "throttled",
            ("throttled",) * 5,
        ),
        ([genai_errors.ServerError(504, {}) for _ in range(MAX_ATTEMPTS)], "transient", ("transient",) * 2),
        ([genai_errors.ClientError(429, {}), genai_errors.ClientError(400, {})], "fatal", ("throttled",)),
    ],
    ids=["429_429_timeout", "429s", "504s", "429_then_400"],
)
def test_a_provider_failure_carries_its_tries(
    replies: list[Any], kind: str, failures: tuple[str, ...]
) -> None:
    llm, _, _ = google(replies)
    with pytest.raises(LLMError) as raised:
        llm.complete(MODEL, "hi")
    e = raised.value
    assert (e.kind, e.attempts, e.failures) == (kind, len(replies), failures)
    if kind != "fatal":
        assert f"after {len(replies)} tries" in str(e)


def test_a_deadline_error_carries_the_tries_made() -> None:
    llm, clock = timed(3.0, lambda: status_error(openai.RateLimitError, 429))
    with call_scope(clock() + 4.0), pytest.raises(LLMError) as raised:
        llm.complete(OR_MODEL, "hi")
    assert (raised.value.attempts, raised.value.failures) == (1, ("throttled",))
    llm, _, clock = openrouter_clocked([or_reply("ok")])
    with call_scope(clock() + MIN_ATTEMPT_S / 2), pytest.raises(LLMError) as not_started:
        llm.complete(OR_MODEL, "hi")
    assert (not_started.value.attempts, not_started.value.failures) == (0, ())


class _ScriptedTimedClient(_TimedClient):
    """Request n takes script[n][0] s on the fake clock (capped at its timeout), then raises script[n][1]."""

    def __init__(self, clock: Clock, script: list[tuple[float, Any]]) -> None:
        super().__init__(clock, 0.0, None)
        self.script = list(script)

    def create(self, **kwargs: Any) -> Any:
        self.took, self.error = self.script.pop(0)
        return super().create(**kwargs)


def test_a_last_try_cut_by_our_deadline_is_our_deadline_not_the_providers_give_up() -> None:
    # A 429, then a try that stalls until the deadline we set cuts it: the second try is ours, so the call
    # ends as "deadline", and only the 429 before it makes the provider the cause.
    clock = Clock()
    client = _ScriptedTimedClient(
        clock,
        [
            (0.5, lambda: status_error(openai.RateLimitError, 429)),
            (100.0, lambda: openai.APITimeoutError(request=REQ)),
        ],
    )
    llm = OpenRouterLLM(None, client=client, sleep=clock.sleep, clock=clock)  # type: ignore[arg-type]
    with call_scope(clock() + 15.0), pytest.raises(LLMError) as raised:
        llm.complete(OR_MODEL, "hi")
    e = raised.value
    assert (e.kind, e.cause, e.attempts, e.failures) == ("deadline", "throttled", 2, ("throttled",))


def test_google_treats_499_cancelled_like_a_504_stall() -> None:
    # Vertex AI answered 499 CANCELLED for a stalled call on 2026-10-17, where its sibling calls got 504.
    llm, models, _ = google([genai_errors.ClientError(499, {}), g_text("ok")])
    assert llm.complete(MODEL, "hi").text == "ok"
    assert len(models.requests) == 2
    llm, _, _ = google([genai_errors.ClientError(499, {}) for _ in range(MAX_ATTEMPTS)])
    with pytest.raises(LLMError) as raised:
        llm.complete(MODEL, "hi")
    assert (raised.value.kind, raised.value.cause) == ("transient", "transient")


def test_other_client_errors_are_still_fatal() -> None:
    for code in (400, 403, 404):
        llm, models, _ = google([genai_errors.ClientError(code, {})])
        with pytest.raises(LLMError) as raised:
            llm.complete(MODEL, "hi")
        assert raised.value.kind == "fatal" and len(models.requests) == 1


# ------------------------------------------- a reply Gemini reports as broken (MALFORMED_FINISH), 18 Oct


def g_finish(name: str) -> gt.GenerateContentResponse:
    """A reply that stopped with finish reason `name`. google-genai 2.31 has no member for
    MALFORMED_RESPONSE and warns while it parses it as an unknown value that keeps its name, as Vertex
    AI's reply did on 2026-10-18."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return g_text("", finish=gt.FinishReason(name))


@pytest.mark.parametrize("reason", sorted(MALFORMED_FINISH))
def test_a_broken_reply_is_sent_again_once_unchanged_and_then_succeeds(reason: str) -> None:
    llm, models, sleeps = google([g_finish(reason), g_text("ok")])
    messages = [Message("system", "s"), Message("user", "x")]
    assert llm.chat(MODEL, messages, [TOOL]).text == "ok"
    assert len(models.requests) == MALFORMED_ATTEMPTS
    assert models.requests[0]["contents"] == models.requests[1]["contents"]  # the same contents again
    assert sleeps == [MALFORMED_BACKOFF_S]


@pytest.mark.parametrize("reason", sorted(MALFORMED_FINISH))
def test_a_broken_reply_twice_is_kind_malformed_never_busy(reason: str) -> None:
    llm, models, sleeps = google([g_finish(reason), g_finish(reason), g_text("never sent")])
    with pytest.raises(LLMError, match=reason) as raised:
        llm.complete(MODEL, "hi")
    e = raised.value
    assert (e.kind, e.model, e.cause, e.capacity, e.out_of_time) == ("malformed", MODEL, None, False, False)
    assert e.attempts == MALFORMED_ATTEMPTS and e.failures == ()
    assert len(models.requests) == MALFORMED_ATTEMPTS and sleeps == [MALFORMED_BACKOFF_S]


def test_a_safety_stop_stays_fatal_and_is_not_retried() -> None:
    llm, models, sleeps = google([g_text("", finish=gt.FinishReason.SAFETY), g_text("never sent")])
    with pytest.raises(LLMError, match="SAFETY") as raised:
        llm.complete(MODEL, "hi")
    assert (raised.value.kind, raised.value.capacity) == (None, False)
    assert len(models.requests) == 1 and sleeps == []


def test_a_broken_reply_with_no_time_to_retry_is_still_malformed() -> None:
    # Our deadline leaves no room for the retry; the reply was still the model's broken one, not a stall.
    client = FakeGenaiClient([g_finish("MALFORMED_RESPONSE"), g_text("never sent")])
    clock = Clock()
    llm = GoogleLLM(client, sleep=clock.sleep, clock=clock)  # type: ignore[arg-type]
    with (
        call_scope(clock() + MALFORMED_BACKOFF_S + MIN_ATTEMPT_S / 2),
        pytest.raises(LLMError, match="no time left to retry") as raised,
    ):
        llm.complete(MODEL, "hi")
    assert (raised.value.kind, raised.value.cause, raised.value.attempts) == ("malformed", None, 1)
    assert len(client.models.requests) == 1 and clock.sleeps == []


def test_a_broken_reply_after_busy_tries_still_gets_its_retry() -> None:
    # Broken replies count on their own, so 429s before one do not use up its retry, and the busy tries
    # stay on the error's failures only when the call fails.
    busy = [genai_errors.ClientError(429, {}) for _ in range(THROTTLED_ATTEMPTS - 1)]
    llm, models, _ = google([*busy, g_finish("MALFORMED_FUNCTION_CALL"), g_text("ok")])
    assert llm.complete(MODEL, "hi").text == "ok"
    assert len(models.requests) == THROTTLED_ATTEMPTS + 1


def test_openrouter_retries_a_reply_gemini_reported_as_broken() -> None:
    broken = or_reply("", extra=None)
    broken["choices"][0]["native_finish_reason"] = "MALFORMED_FUNCTION_CALL"
    llm, api, sleeps = openrouter([broken, or_reply("ok")])
    assert llm.complete(OR_MODEL, "hi").text == "ok"
    assert len(api.requests) == 2 and sleeps == [MALFORMED_BACKOFF_S]
    llm, _, _ = openrouter([broken, broken])
    with pytest.raises(LLMError) as raised:
        llm.complete(OR_MODEL, "hi")
    assert (raised.value.kind, raised.value.capacity) == ("malformed", False)
