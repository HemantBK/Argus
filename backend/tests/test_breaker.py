"""The agent model's circuit breaker (breaker.py) on a fake clock: closed, open, half-open trial, closed."""

import threading

import pytest

from argus_api.breaker import OPEN_AFTER, OPEN_S, WINDOW_S, Breaker

MODEL = "gemini-test"


class Clock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now


def opened(clock: Clock) -> Breaker:
    b = Breaker(MODEL, clock=clock)
    for _ in range(OPEN_AFTER):
        assert b.route() == "primary"
        b.report("primary", "busy")
    assert b.is_open
    return b


def events(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "argus.breaker"]


def test_it_opens_after_enough_busy_give_ups_within_the_window(caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    b = Breaker(MODEL, clock=clock)
    for _ in range(OPEN_AFTER - 1):
        b.report("primary", "busy")
    b.report("primary", "ok")  # an answer does not open it, nor clear the count
    assert not b.is_open and b.route() == "primary"
    with caplog.at_level("INFO", logger="argus.breaker"):
        b.report("primary", "busy")
    assert b.is_open and b.route() == "fallback"
    assert events(caplog) == ["llm_breaker_open"]
    assert caplog.records[-1].json_fields["model"] == MODEL  # type: ignore[attr-defined]


def test_busy_give_ups_spread_wider_than_the_window_do_not_open_it() -> None:
    clock = Clock()
    b = Breaker(MODEL, clock=clock)
    for _ in range(OPEN_AFTER * 2):
        b.report("primary", "busy")
        clock.now += WINDOW_S / (OPEN_AFTER - 1) + 1
    assert not b.is_open


def test_while_open_every_clause_goes_to_the_fallback() -> None:
    clock = Clock()
    b = opened(clock)
    clock.now += OPEN_S - 1
    assert [b.route() for _ in range(5)] == ["fallback"] * 5


def test_after_the_open_time_one_trial_clause_and_a_good_answer_closes_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    b = opened(clock)
    clock.now += OPEN_S
    assert b.route() == "trial"
    assert b.route() == "fallback"  # only one trial at a time
    caplog.clear()
    with caplog.at_level("INFO", logger="argus.breaker"):
        b.report("trial", "ok")
    assert not b.is_open and b.route() == "primary"
    assert events(caplog) == ["llm_breaker_close"]


def test_a_busy_trial_opens_it_again_for_the_full_time() -> None:
    clock = Clock()
    b = opened(clock)
    clock.now += OPEN_S
    assert b.route() == "trial"
    b.report("trial", "busy")
    assert b.is_open and b.route() == "fallback"
    clock.now += OPEN_S - 1
    assert b.route() == "fallback"
    clock.now += 1
    assert b.route() == "trial"


def test_a_trial_that_ended_unknown_lets_the_next_clause_try() -> None:
    clock = Clock()
    b = opened(clock)
    clock.now += OPEN_S
    assert b.route() == "trial"
    b.report("trial", None)
    assert b.is_open and b.route() == "trial"


def test_reports_from_clauses_that_started_before_it_opened_change_nothing() -> None:
    clock = Clock()
    b = opened(clock)
    b.report("primary", "busy")
    b.report("fallback", "busy")
    clock.now += OPEN_S
    assert b.route() == "trial"


def test_one_trial_among_many_threads() -> None:
    clock = Clock()
    b = opened(clock)
    clock.now += OPEN_S
    routes: list[str] = []
    lock = threading.Lock()

    def take() -> None:
        r = b.route()
        with lock:
            routes.append(r)

    threads = [threading.Thread(target=take) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(routes) == ["fallback"] * 15 + ["trial"]


def test_its_state_reads_closed_open_and_half_open() -> None:
    clock = Clock()
    assert Breaker(MODEL, clock=clock).state == "closed"
    b = opened(clock)
    assert b.state == "open"
    clock.now += OPEN_S
    assert b.state == "half_open"  # the next clause is the trial
    assert b.route() == "trial" and b.state == "half_open"  # the trial is running
    b.report("trial", "ok")
    assert b.state == "closed"
