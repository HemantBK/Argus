"""A scripted LLM for tests: no network, every call recorded.

Each method pops its next scripted response from its own queue. A scripted exception is raised instead of
returned (for example LLMError, to test failed states). An empty queue raises FakeLLMExhausted, an
AssertionError, so a test that makes more calls than it scripted fails instead of passing on a default.
"""

import copy
import json
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

import jsonschema

from argus_api.llm import Completion, LLMError, Message, ToolSpec, Turn

FAKE_MODEL = "fake-model"


class FakeLLMExhausted(AssertionError):
    pass


@dataclass(frozen=True)
class FakeCall:
    method: Literal["complete", "complete_json", "chat"]
    model: str
    prompt: str | None = None
    schema: dict[str, Any] | None = None
    messages: list[Message] | None = None
    tools: list[ToolSpec] | None = None


def turn(text: str = "", tool_calls: Iterable[Any] = (), model: str = FAKE_MODEL) -> Turn:
    """A Turn with no token counts, for scripting chat()."""
    return Turn(
        text=text,
        tool_calls=list(tool_calls),
        model=model,
        input_tokens=None,
        output_tokens=None,
        latency_ms=0,
    )


class FakeLLM:
    def __init__(
        self,
        *,
        completions: Iterable[str | Completion | BaseException] = (),
        json_replies: Iterable[dict[str, Any] | BaseException] = (),
        turns: Iterable[Turn | BaseException] = (),
    ) -> None:
        self._completions = deque(completions)
        self._json = deque(json_replies)
        self._turns: deque[Turn | BaseException] = deque(turns)
        self.calls: list[FakeCall] = []

    @staticmethod
    def _pop(queue: deque[Any], method: str) -> Any:
        if not queue:
            raise FakeLLMExhausted(f"FakeLLM.{method} called with no scripted response left")
        item = queue.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    def remaining(self) -> dict[str, int]:
        return {
            "complete": len(self._completions),
            "complete_json": len(self._json),
            "chat": len(self._turns),
        }

    def complete(self, model: str, prompt: str) -> Completion:
        self.calls.append(FakeCall("complete", model, prompt=prompt))
        item = self._pop(self._completions, "complete")
        if isinstance(item, Completion):
            return item
        return Completion(text=item, model=model, input_tokens=None, output_tokens=None)

    def complete_json(
        self, model: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Completion]:
        self.calls.append(FakeCall("complete_json", model, prompt=prompt, schema=schema))
        reply: dict[str, Any] = self._pop(self._json, "complete_json")
        # Same contract as the real providers: a reply that breaks the schema is an LLMError.
        try:
            jsonschema.validate(reply, schema)
        except jsonschema.ValidationError as e:
            raise LLMError(f"FakeLLM scripted reply breaks the schema: {e.message}") from e
        text = json.dumps(reply)
        return copy.deepcopy(reply), Completion(text=text, model=model, input_tokens=None, output_tokens=None)

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec]) -> Turn:
        # Copy: callers usually keep appending to the same list.
        self.calls.append(FakeCall("chat", model, messages=list(messages), tools=list(tools)))
        result: Turn = self._pop(self._turns, "chat")
        return result
