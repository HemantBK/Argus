"""One interface over the three ways we reach Gemini.

- vertex:     Gemini on Vertex AI (the deployed app; the only provider allowed in cloud mode)
- aistudio:   Gemini through a Google AI Studio API key (local development)
- openrouter: Gemini through OpenRouter's OpenAI-compatible API (local development only)
"""

from dataclasses import dataclass
from typing import Protocol

from google import genai

from argus_api.config import Settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


class LLM(Protocol):
    def complete(self, model: str, prompt: str) -> Completion: ...


class GoogleLLM:
    def __init__(self, client: genai.Client) -> None:
        self._client = client

    def complete(self, model: str, prompt: str) -> Completion:
        r = self._client.models.generate_content(model=model, contents=prompt)
        usage = r.usage_metadata
        return Completion(
            text=(r.text or "").strip(),
            model=model,
            input_tokens=usage.prompt_token_count if usage else None,
            output_tokens=usage.candidates_token_count if usage else None,
        )


class OpenRouterLLM:
    def __init__(self, api_key: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)

    def complete(self, model: str, prompt: str) -> Completion:
        r = self._client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
        if not r.choices:
            raise RuntimeError(f"OpenRouter returned no choices for {model}")
        return Completion(
            text=(r.choices[0].message.content or "").strip(),
            model=r.model,
            input_tokens=r.usage.prompt_tokens if r.usage else None,
            output_tokens=r.usage.completion_tokens if r.usage else None,
        )


def build_llm(s: Settings) -> LLM:
    if s.provider == "vertex":
        return GoogleLLM(genai.Client(vertexai=True, project=s.project, location=s.location))
    if s.provider == "aistudio":
        return GoogleLLM(genai.Client(api_key=s.api_key))
    return OpenRouterLLM(api_key=s.api_key or "")
