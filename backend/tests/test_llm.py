"""LLM layer: Gemini and OpenRouter adapters against mocked clients (no network), plus FakeLLM."""

import json
from typing import Any

import httpx
import httpx2
import openai
import pytest
from google.genai import errors as genai_errors
from google.genai import types as gt
from openai.types.chat import ChatCompletion

from argus_api.config import AGENT_THINKING, MONITOR_THINKING, PINNED_MODELS, ConfigError, Settings
from argus_api.llm import (
    BACKOFF_BASE_S,
    CHAT_TIMEOUT_S,
    JSON_ATTEMPTS,
    LLM,
    MAX_ATTEMPTS,
    MIN_ATTEMPT_S,
    PRICES_USD_PER_MTOK,
    REQUEST_TIMEOUT_S,
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
    llm, models, sleeps = google([genai_errors.ClientError(429, {}) for _ in range(MAX_ATTEMPTS)])
    with pytest.raises(LLMError, match=f"after {MAX_ATTEMPTS} tries"):
        llm.complete(MODEL, "hi")
    assert len(models.requests) == MAX_ATTEMPTS
    assert sleeps == expected_backoff(MAX_ATTEMPTS - 1)


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


def test_google_a_note_after_tool_results_joins_their_turn() -> None:
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
    assert [c.role for c in contents] == ["user", "model", "user"]
    last = contents[-1].parts
    assert last[0].function_response is not None and last[1].text == "monitor note"
