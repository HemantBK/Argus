"""Shared by tests/test_runs.py and tests_emulator/: scripted per-clause LLM conversations built from the gold
findings and the production schemas, so whole runs can be reviewed offline and in parallel."""

import html
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from argus_api.agent.prompts import DRAFT_RESPONSE_SCHEMA
from argus_api.config import Settings
from argus_api.corpus.store import Corpus
from argus_api.llm import PRICES_USD_PER_MTOK, Completion, LLMError, Message, ToolSpec, Turn
from argus_api.llm_fake import FakeLLM, turn
from argus_api.monitor.prompts import MONITOR_RESPONSE_SCHEMA
from argus_api.runs import Policy, load_policy

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "corpus"
SAMPLES = Path(__file__).resolve().parents[1] / "argus_api" / "samples"
AGENT_MODEL, MONITOR_MODEL = list(PRICES_USD_PER_MTOK)[:2]


def fixture_corpus() -> Corpus:
    return Corpus.load(FIXTURE_DIR)


def settings() -> Settings:
    return Settings(
        mode="local",
        provider="openrouter",
        project="test-project",
        location=None,
        api_key="unused-by-fake",
        agent_model=AGENT_MODEL,
        monitor_model=MONITOR_MODEL,
    )


def gold(policy_id: str, clause_id: str) -> dict[str, Any]:
    g: dict[str, Any] = json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))["policies"][
        policy_id
    ][clause_id]
    return g


def draft_reply(policy_id: str, clause_id: str) -> dict[str, Any]:
    """The gold finding as the agent's JSON draft, shaped by the production draft schema."""
    g = gold(policy_id, clause_id)
    replaced = g["replaced_citation"]
    reply = {
        "label": g["label"],
        "rationale": g["rationale"],
        "citation_instrument_ref": g["citation"]["instrument_ref"],
        "citation_para": g["citation"]["para"],
        "citation_quote": g["citation"]["quote"],
        "policy_cited_instrument_ref": replaced["instrument_ref"] if replaced else None,
        "policy_cited_para": replaced["para"] if replaced else None,
    }
    assert set(reply) == set(DRAFT_RESPONSE_SCHEMA["required"])
    return reply


def judgement() -> dict[str, Any]:
    """A clean monitor judgement built from the monitor's production schema."""
    base: dict[str, Any] = {
        k: {"boolean": False, "string": ""}[v["type"]]
        for k, v in MONITOR_RESPONSE_SCHEMA["properties"].items()
    }
    base.update(supported=True, on_task=True, supported_detail="Supported.", on_task_detail="On task.")
    return base


def gold_clause_llm(policy_id: str, clause_id: str) -> FakeLLM:
    """One clause: research ends at once, the draft is the gold finding, the monitor judgement is clean."""
    return FakeLLM(
        turns=[turn(text="Ready to draft.")], json_replies=[draft_reply(policy_id, clause_id), judgement()]
    )


def failing_clause_llm(message: str = "provider returned HTTP 500 three times") -> FakeLLM:
    return FakeLLM(turns=[LLMError(message)])


class ClauseRoutedLLM:
    """One FakeLLM per clause, chosen by the clause text the prompt carries, so clauses reviewed in parallel
    each follow their own script. A prompt that names no clause, or more than one, is a test failure."""

    def __init__(self, policy: Policy, scripts: dict[str, FakeLLM]) -> None:
        assert set(scripts) == {c.id for c in policy.clauses}
        self.scripts = scripts
        self._needles = {c.id: html.escape(c.text, quote=False) for c in policy.clauses}

    def _route(self, text: str) -> FakeLLM:
        hits = [cid for cid, needle in self._needles.items() if needle in text]
        if len(hits) != 1:
            raise AssertionError(f"prompt matches clauses {hits}, expected exactly one")
        return self.scripts[hits[0]]

    def complete(self, model: str, prompt: str) -> Completion:
        return self._route(prompt).complete(model, prompt)

    def complete_json(
        self, model: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Completion]:
        return self._route(prompt).complete_json(model, prompt, schema)

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec]) -> Turn:
        return self._route("\n".join(m.content for m in messages)).chat(model, messages, tools)

    def remaining(self) -> dict[str, dict[str, int]]:
        return {cid: llm.remaining() for cid, llm in self.scripts.items()}


def policy_llm(policy_id: str, overrides: Iterable[tuple[str, FakeLLM]] = ()) -> ClauseRoutedLLM:
    policy = load_policy(policy_id)
    scripts = {c.id: gold_clause_llm(policy_id, c.id) for c in policy.clauses}
    scripts.update(dict(overrides))
    return ClauseRoutedLLM(policy, scripts)
