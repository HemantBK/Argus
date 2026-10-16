"""Structured logging: every AI call becomes one JSON line at INFO on the "argus" logger tree, whatever level
the root logger is at (uvicorn leaves it at WARNING)."""

import io
import json
import logging
from collections.abc import Iterator
from datetime import date

import pytest

from argus_api import logs
from argus_api.agent import Clause, review_clause
from argus_api.llm_fake import FakeLLM, turn
from tests.run_support import draft_reply, fixture_corpus, judgement, settings


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    """configure_logging with its stdout swapped for a buffer; the logger is restored afterwards."""
    logger = logging.getLogger(logs.ROOT_LOGGER)
    saved = (list(logger.handlers), logger.level, logger.propagate)
    logger.handlers.clear()
    buffer = io.StringIO()
    monkeypatch.setattr(logs.sys, "stdout", buffer)
    logging.getLogger().setLevel(logging.WARNING)  # what uvicorn leaves the root logger at
    logs.configure_logging()
    yield buffer
    logger.handlers[:], logger.level, logger.propagate = saved


def lines(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


def test_configure_logging_is_idempotent(captured: io.StringIO) -> None:
    logs.configure_logging()
    logs.log_event(logging.getLogger(f"{logs.ROOT_LOGGER}.test"), "probe", n=1)
    assert lines(captured) == [{"severity": "INFO", "logger": "argus.test", "message": "probe", "n": 1}]


def test_event_fields_cannot_overwrite_the_entry() -> None:
    record = logging.LogRecord("argus.x", logging.INFO, __file__, 1, "e", None, None)
    record.json_fields = {"severity": "DEBUG"}
    with pytest.raises(ValueError, match="severity"):
        logs.JsonLineFormatter().format(record)


def test_every_model_call_of_a_review_is_logged_with_its_run_id(captured: io.StringIO) -> None:
    text = "Loans shall always be disbursed directly into the bank account of the borrower."
    llm = FakeLLM(turns=[turn(text="Ready.")], json_replies=[draft_reply("policy_a", "c4"), judgement()])
    s = settings()
    review_clause(
        llm,
        s,
        fixture_corpus(),
        Clause(id="c4", text=text),
        lambda _: None,
        today=date(2026, 10, 16),
        run_id="r1",
    )

    entries = lines(captured)
    calls = [e for e in entries if e["message"] in ("agent_call", "monitor_call")]
    assert len(calls) == len(llm.calls)
    for e in calls:
        assert e["run_id"] == "r1" and e["clause_id"] == "c4"
        assert {"model", "prompt_sha256", "latency_ms", "input_tokens", "output_tokens"} <= set(e)
        assert "cost_usd_estimate" in e
    assert {e["model"] for e in calls} == {s.agent_model, s.monitor_model}
    verdicts = [e for e in entries if e["message"] == "monitor_verdict"]
    assert len(verdicts) == 1 and verdicts[0]["action"] == "continue"
