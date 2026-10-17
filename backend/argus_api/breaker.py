"""A circuit breaker for the agent model, per process: when it keeps giving up busy, stop asking it a while.

runs.py hands a clause to the fallback agent model when the agent model gives up busy (HTTP 429, a stall, or
no time left in its share of the budget). When that happens again and again, every new clause would spend
its first PRIMARY_PHASE_S on a model that is not answering. The breaker counts those give-ups:

  closed     clauses start on the agent model. OPEN_AFTER give-ups within WINDOW_S open the breaker.
  open       for OPEN_S, clauses go straight to the fallback model (and are labelled as such).
  half-open  after OPEN_S, one clause tries the agent model while the others still use the fallback. If it
             finishes without a busy give-up, the breaker closes; if it gives up busy, it opens again.

Only a give-up the provider caused counts (a 429, 5xx or timeout: llm.LLMError.capacity): a clause that only
ran out of the agent model's share of the time is handed over too, but is not a busy give-up.

Opening and closing are logged (llm_breaker_open, llm_breaker_close), and GET /health shows the state (state:
"closed", "open" or "half_open"), so a release smoke that opened it is seen before a demo run. The state
lives in this process only: each Cloud Run instance learns for itself, and a restart starts closed.
Thread-safe; the clock is injectable for tests.
"""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Literal

from argus_api.logs import log_event

log = logging.getLogger("argus.breaker")

OPEN_AFTER = 3  # busy give-ups of the agent model ...
WINDOW_S = 60.0  # ... within this many seconds open the breaker
OPEN_S = 120.0  # how long it stays open before one trial clause on the agent model

# Where a clause starts: on the agent model, as the half-open trial on it, or on the fallback model.
Route = Literal["primary", "trial", "fallback"]
# How a clause that started on the agent model ended: "busy" (it gave up busy), "ok" (anything else: the
# agent model answered), or None (unknown, for example the run closed first).
Outcome = Literal["busy", "ok"] | None
# The breaker's state, as GET /health reports it (see the module docstring).
State = Literal["closed", "open", "half_open"]


class Breaker:
    def __init__(
        self,
        model: str,
        *,
        open_after: int = OPEN_AFTER,
        window_s: float = WINDOW_S,
        open_s: float = OPEN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self._open_after = open_after
        self._window_s = window_s
        self._open_s = open_s
        self._clock = clock
        self._lock = threading.Lock()
        self._busy: deque[float] = deque()  # times of recent busy give-ups while closed
        self._open_until: float | None = None  # set while open or half-open
        self._trial = False  # a half-open trial clause is on the agent model

    @property
    def is_open(self) -> bool:
        """Open or half-open: new clauses go to the fallback model, but for one trial."""
        with self._lock:
            return self._open_until is not None

    @property
    def state(self) -> State:
        """closed, open (still within OPEN_S), or half_open (OPEN_S has passed: the next clause, or the one
        now running, is the trial on the agent model)."""
        with self._lock:
            if self._open_until is None:
                return "closed"
            return "open" if self._clock() < self._open_until and not self._trial else "half_open"

    def route(self) -> Route:
        """Where the next clause starts. A "trial" route must be reported back (report) once it ends."""
        with self._lock:
            if self._open_until is None:
                return "primary"
            if self._clock() < self._open_until or self._trial:
                return "fallback"
            self._trial = True
            return "trial"

    def report(self, route: Route, outcome: Outcome) -> None:
        """How a clause that started on `route` ended on the agent model."""
        if route == "fallback":
            return
        with self._lock:
            now = self._clock()
            if route == "trial":
                self._trial = False
                if outcome == "busy":
                    self._open(now, "the trial clause on the agent model gave up busy")
                elif outcome == "ok":
                    self._open_until = None
                    self._busy.clear()
                    log_event(log, "llm_breaker_close", model=self.model)
                return
            if outcome != "busy" or self._open_until is not None:
                return
            self._busy.append(now)
            while self._busy and now - self._busy[0] > self._window_s:
                self._busy.popleft()
            if len(self._busy) >= self._open_after:
                self._open(now, f"{len(self._busy)} busy give-ups within {self._window_s:g} s")

    def _open(self, now: float, why: str) -> None:
        self._open_until = now + self._open_s
        self._busy.clear()
        log_event(
            log, "llm_breaker_open", level=logging.WARNING, model=self.model, open_s=self._open_s, why=why
        )


_breakers: dict[str, Breaker] = {}
_breakers_lock = threading.Lock()


def process_breaker(model: str) -> Breaker:
    """The breaker this process keeps for agent model `model`."""
    with _breakers_lock:
        if model not in _breakers:
            _breakers[model] = Breaker(model)
        return _breakers[model]
