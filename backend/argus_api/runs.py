"""Policy review runs: admission, orchestration and every Firestore write, laid out as schema.py documents.

A run, from POST /api/runs:
  1. admit     config/limits is read: kill switch off -> RunsDisabled (503); the Run doc is created with
               status running in the same transaction that counts it against today's, this hour's and the
               user's own caps (usage/{IST date}, .../hours/{HH}, .../users/{uid}), so a duplicate run_id
               (409) or a full cap (429) writes nothing. Listeners see the run at once.
  2. execute   every clause is reviewed at once by the agent (agent.review_clause): a policy has at most
               MAX_CLAUSES clauses and the pool has that many workers, so no clause waits for a slot and
               the budget is spent on the clause's own work. Every step
               lands in runs/{id}/steps/{seq:04d} and in the audit log as it happens; each finding lands in
               runs/{id}/findings/{clause_id} when its clause finishes.
  3. budget    RUN_BUDGET_S after the work starts the run is closed. A clause still in progress (or never
               started) is saved as a failed finding saying so; nothing is dropped. Late steps from a clause
               that is still waiting on the model are refused (RunClosed) and never written. Every model
               call of a clause ends by the same deadline (llm.call_scope): an attempt is cut to the time
               left and no retry starts without time to finish, so a stalled call fails its clause with
               the reason instead of outliving the budget.
  4. finish    Run.summary, status (done, or failed when any clause failed, with the reasons in Run.error),
               latency and the cost estimate summed from the recorded model calls.

Fallback agent model (Settings.fallback_model; on in the deployed app): when the agent model gives up busy on
a clause (HTTP 429 on every try, timeouts or 5xx on every try) or runs out of its share of the time, and at
least MIN_FALLBACK_S of the run budget is left, the clause is reviewed again from the start, with a fresh
conversation, by the fallback model. The monitor model and every citation check stay the same. So that time
is left, the agent model's calls for a clause end PRIMARY_PHASE_S after the clause started
(llm.call_scope's model_deadlines), until its first draft is written; the monitor model's calls keep the run
deadline, so a slow but correct draft is still judged, and the run budget itself does not change. Once a
draft exists the agent model's calls (the revise step) keep the run deadline too: a finished draft is worth
more than a fresh start on the fallback model. (A monitor model that is also the agent model shares the
agent's cut.) The agent model's steps stay in the trace, the last one saying the clause was
handed over; the finding records the model that wrote it (Finding.agent_model) and why (fallback_reason),
and RunSummary.fallback_clauses counts such clauses. Never on other failures: a refused request, an unusable
reply, a busy monitor model (it has no fallback), a monitor or library error. If the fallback review fails
too, the clause fails as it would have. A per-process circuit breaker (breaker.py) sends clauses straight to
the fallback model while the agent model keeps giving up busy; GET /health shows its state.

A failed clause's error says whose fault it was. When the provider failed the review (llm.LLMError.capacity:
a 429, a 5xx or a timeout, on the agent or the monitor model) it starts with PROVIDER_BUSY, so it reads as
the provider's state, not a fault of the review. When our own deadline ended the review with no provider
failure behind it, it starts with OUT_OF_TIME: the review was too slow. One exception: when the provider's
failure on the agent model sent the clause to the fallback model, running out of time there is still the
provider's doing (PROVIDER_BUSY). Only the agent model's provider failures count as busy give-ups for the
breaker.

A failed clause is an explicit state: a Finding with `error` set, label insufficient_evidence, no citation
and monitor_status needs_review, so it goes to a person. Anything unexpected (a bug, Firestore down) marks
the run failed with the error and is re-raised.

A pasted clause (custom.py builds its one-clause Policy with source "custom") runs exactly like a sample
policy, with three differences: admission also needs config/custom_limits (schema.CustomLimits) switched on
and counts the run against its custom_daily_cap (usage/{IST date}/sources/custom); every document of the run
carries expires_at, a Firestore timestamp CUSTOM_TTL after the start, for the TTL policy (admin.py ttl) and
purge_custom.py; and it can never become a saved sample. Nothing here logs the clause text.

Eval runs call Runner.run_review(..., enforce_cap=False) in Python; the HTTP endpoint always enforces the cap,
so the bypass cannot be reached from a browser. The kill switch applies to every run.
"""

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from argus_api.agent import (
    AgentStep,
    Clause,
    ClauseReviewError,
    HandOff,
    ReviewOutcome,
    SeqCounter,
    review_clause,
)
from argus_api.agent.loop import IST
from argus_api.agent.prompts import corpus_as_on
from argus_api.breaker import Breaker, Outcome, process_breaker
from argus_api.config import PINNED_MODELS, Provider, Settings, model_name
from argus_api.corpus.store import Corpus
from argus_api.docstore import AlreadyExists, Counter, CreateResult, DocumentStore, StoreError, Write
from argus_api.llm import LLM, LLMError, call_scope
from argus_api.logs import log_event
from argus_api.schema import (
    CUSTOM_SOURCE,
    AuditEvent,
    CustomLimits,
    Finding,
    FindingLabel,
    Limits,
    MonitorStatus,
    Run,
    RunClause,
    RunStatus,
    RunSummary,
    Signoff,
    SignoffDecision,
    StepKind,
)

MAX_CLAUSES = 10  # PLAN section 6: sample policies have 8-10 clauses
PARALLEL_CLAUSES = MAX_CLAUSES
RUN_BUDGET_S = 90.0  # well under the Cloud Run request timeout (300 s); see llm.CHAT_TIMEOUT_S
# With a fallback model: the agent model's calls for a clause, up to its first draft, end this long after the
# clause started, which leaves about 55 s of the run budget for the fallback review. A 504 stall takes about
# 27 s, so the agent model still gets one stalled try and a short second one, or all five tries at a 429 (15 s
# of waits at most).
PRIMARY_PHASE_S = 35.0
# A clause is handed to the fallback model only with at least this much of the run budget left: a complete
# review on the fallback model took 5-15 s in the probe of 2026-10-17, and a hand-over that cannot finish
# would only turn a clear failure into a timeout.
MIN_FALLBACK_S = 30.0
# The start of a failed clause's error when the provider failed (llm.LLMError.capacity): the provider's
# state, not a fault in the review. scripts/cloud_e2e.py reads it.
PROVIDER_BUSY = "Gemini capacity was busy; try again shortly."
# The start of a failed clause's error when the run budget (or the agent model's share of it) ran out and no
# provider failure was behind it: the review itself was too slow.
OUT_OF_TIME = "The review ran out of time within the run budget."
SAMPLES_DIR = Path(__file__).resolve().parent / "samples"
POLICY_IDS = ("policy_a", "policy_b")
CUSTOM_POLICY_ID = "custom"  # POST /api/runs with a pasted clause (custom.py)
# A custom run's documents carry expires_at = start + CUSTOM_TTL. Firestore TTL deletes an expired document
# typically within 24 h after expires_at, so 24 h here keeps deletion within CUSTOM_DELETED_WITHIN of the
# start (purge_custom.py deletes anything expired at once, whatever TTL has done).
CUSTOM_TTL = timedelta(hours=24)
TTL_DELETION_LAG = timedelta(hours=24)  # Firestore's documented "typically within 24 hours" after expiry
CUSTOM_DELETED_WITHIN = CUSTOM_TTL + TTL_DELETION_LAG  # 48 h: the bound the app's notice states
CUSTOM_TITLE_CHARS = 60  # a pasted clause's title is its first CUSTOM_TITLE_CHARS characters
# Run fields only a custom run has; a sample run's document and API response leave them out, as before them.
CUSTOM_ONLY_FIELDS = ("source", "expires_at")
EXPIRES_AT = "expires_at"
RUN_ID_PATTERN = r"^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$"  # uuid4().hex
RUNS = "runs"
SAMPLES = "samples"
LIMITS_PATH = "config/limits"
CUSTOM_LIMITS_PATH = "config/custom_limits"
RUN_CHILDREN = ("steps", "findings", "audit")  # the subcollections of runs/{run_id}

log = logging.getLogger("argus.runs")
USAGE_COLLECTION = "usage"
USAGE_FIELD = "runs"
SMOKE = "smoke"
SEQ_DIGITS = 4
TITLE_WORDS = 8
AUDIT_ATTEMPTS = 5
FIRESTORE_BATCH_LIMIT = 500
SAMPLE_PROVIDER: Provider = "vertex"  # saved runs are recorded on the deployed stack only (rules B-01, B-02)


# ---- errors the API maps to HTTP statuses --------------------------------------------------------------


class Rejected(RuntimeError):
    """A request refused before anything was written; status_code is the HTTP status to answer with."""

    status_code = 400


class UnknownPolicy(Rejected):
    status_code = 404


class RunsDisabled(Rejected):
    status_code = 503


class CustomDisabled(Rejected):
    status_code = 503


class LimitsMissing(Rejected):
    status_code = 503


class DuplicateRun(Rejected):
    status_code = 409


class DailyCapReached(Rejected):
    status_code = 429


class Missing(Rejected):
    status_code = 404


class Forbidden(Rejected):
    status_code = 403


class Conflict(Rejected):
    status_code = 409


class Invalid(Rejected):
    status_code = 422


class RunClosed(RuntimeError):
    """Raised into a clause still working after the run budget ran out; nothing more of it is written."""


# ---- policies and paths -------------------------------------------------------------------------------


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    title: str
    synthetic: bool
    disclaimer: str
    clauses: list[Clause]
    source: Literal["sample", "custom"] = "sample"

    @property
    def custom(self) -> bool:
        return self.source == CUSTOM_SOURCE


def load_policy(policy_id: str) -> Policy:
    if policy_id not in POLICY_IDS:
        raise UnknownPolicy(f"Unknown policy {policy_id!r}; choose one of {', '.join(POLICY_IDS)}")
    policy = Policy.model_validate(
        json.loads((SAMPLES_DIR / f"{policy_id}.json").read_text(encoding="utf-8"))
    )
    if policy.policy_id != policy_id:
        raise ValueError(f"{policy_id}.json declares policy_id {policy.policy_id!r}")
    ids = [c.id for c in policy.clauses]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError(f"{policy_id}.json must have clauses with unique ids")
    if len(ids) > MAX_CLAUSES:
        raise ValueError(f"{policy_id}.json has {len(ids)} clauses; a policy may have at most {MAX_CLAUSES}")
    return policy


def clause_title(text: str) -> str:
    """The clause's first TITLE_WORDS words (the sample policies have no clause headings)."""
    words = text.split()
    return " ".join(words[:TITLE_WORDS]) + ("…" if len(words) > TITLE_WORDS else "")


def run_clause_title(policy: Policy, clause: Clause) -> str:
    """The clause's title in the run: a pasted clause's first characters, else clause_title."""
    return clause.text[:CUSTOM_TITLE_CHARS] if policy.custom else clause_title(clause.text)


def seq_id(seq: int) -> str:
    if not 1 <= seq < 10**SEQ_DIGITS:
        raise ValueError(f"sequence number {seq} does not fit a {SEQ_DIGITS}-digit document id")
    return f"{seq:0{SEQ_DIGITS}d}"


def run_path(run_id: str, root: str = RUNS) -> str:
    return f"{root}/{run_id}"


def step_path(run_id: str, seq: int, root: str = RUNS) -> str:
    return f"{run_path(run_id, root)}/steps/{seq_id(seq)}"


def finding_path(run_id: str, clause_id: str, root: str = RUNS) -> str:
    return f"{run_path(run_id, root)}/findings/{clause_id}"


def audit_path(run_id: str, seq: int, root: str = RUNS) -> str:
    return f"{run_path(run_id, root)}/audit/{seq_id(seq)}"


def usage_path(day: str) -> str:
    return f"{USAGE_COLLECTION}/{day}"


def custom_usage_path(day: str) -> str:
    return f"{usage_path(day)}/sources/{CUSTOM_SOURCE}"


def hour_usage_path(day: str, hour: int) -> str:
    return f"{usage_path(day)}/hours/{hour:02d}"


def user_usage_path(day: str, uid: str) -> str:
    return f"{usage_path(day)}/users/{uid}"


RUNS_PAUSED = "Argus is paused: new runs are switched off. Open a saved run instead."
CUSTOM_OFF = "Custom clause review is switched off right now."


def admit_counted(
    store: DocumentStore,
    path: str,
    data: dict[str, Any],
    owner_uid: str,
    at: datetime,
    what: str,
    *,
    custom: bool = False,
) -> None:
    """The one gate for anything that spends model calls: kill switch, then create `path` and count it
    against today's, this hour's and the user's caps in one transaction. A custom run also needs the custom
    switch on and counts against today's custom cap in the same transaction. Raises a Rejected subclass
    (nothing written) when switched off, when `path` exists or when a cap is full."""
    limits = read_limits(store)
    if not limits.enabled:
        raise RunsDisabled(RUNS_PAUSED)
    custom_limits = read_custom_limits(store) if custom else None
    if custom and (custom_limits is None or not custom_limits.custom_enabled):
        raise CustomDisabled(CUSTOM_OFF)
    local = at.astimezone(IST)
    day = local.date().isoformat()
    daily = Counter(usage_path(day), USAGE_FIELD, limits.daily_run_cap)
    hourly = Counter(hour_usage_path(day, local.hour), USAGE_FIELD, limits.hourly_run_cap)
    user = Counter(user_usage_path(day, owner_uid), USAGE_FIELD, limits.per_user_daily_run_cap)
    counters = [daily, hourly, user]
    custom_counter = (
        Counter(custom_usage_path(day), USAGE_FIELD, custom_limits.custom_daily_cap)
        if custom_limits is not None
        else None
    )
    if custom_counter is not None:
        counters.append(custom_counter)
    counted = store.create_counted(path, data, counters)
    if counted.result == CreateResult.EXISTS:
        raise DuplicateRun(f"{what} {path.rsplit('/', 1)[1]} already exists")
    if counted.result == CreateResult.CAP_REACHED:
        if custom_limits is not None and counted.full == custom_counter:
            raise DailyCapReached(
                f"Today's limit of {custom_limits.custom_daily_cap} custom clause reviews is used up. Try a "
                "sample policy, or paste a clause again tomorrow (India time)."
            )
        if counted.full == user:
            raise DailyCapReached(
                f"You have used your {limits.per_user_daily_run_cap} live runs for today. Open a saved run, "
                "or try again tomorrow (India time)."
            )
        if counted.full == hourly:
            raise DailyCapReached(
                f"This hour's limit of {limits.hourly_run_cap} live runs is used up. Open a saved run, or "
                "try again after the hour (India time)."
            )
        raise DailyCapReached(
            f"Today's limit of {limits.daily_run_cap} live runs is used up. Open a saved run, or try "
            "again tomorrow (India time)."
        )


def _json(model: BaseModel) -> dict[str, Any]:
    data: dict[str, Any] = model.model_dump(mode="json")
    return data


def run_json(run: Run) -> dict[str, Any]:
    """The Run as JSON (the API's answer); a sample run leaves out the custom-only fields it does not have."""
    data = _json(run)
    for field in CUSTOM_ONLY_FIELDS:
        if data[field] is None:
            del data[field]
    return data


def run_doc(run: Run) -> dict[str, Any]:
    """The Run as its Firestore document: run_json, with expires_at a real timestamp (TTL ignores strings)."""
    data = run_json(run)
    if run.expires_at is not None:
        data[EXPIRES_AT] = run.expires_at
    return data


def child_doc(run: Run, model: BaseModel) -> dict[str, Any]:
    """A steps/findings/audit document of `run`: the model's JSON, plus the run's expires_at if it has one."""
    data = _json(model)
    if run.expires_at is not None:
        data[EXPIRES_AT] = run.expires_at
    return data


def read_limits(store: DocumentStore) -> Limits:
    doc = store.get(LIMITS_PATH)
    if doc is None:
        raise LimitsMissing(f"Run limits are not configured: {LIMITS_PATH} is missing")
    try:
        return Limits.model_validate(doc)
    except ValidationError as e:
        raise LimitsMissing(f"Run limits in {LIMITS_PATH} are malformed: {e.error_count()} problem(s)") from e


def read_custom_limits(store: DocumentStore) -> CustomLimits | None:
    """config/custom_limits, or None when it is missing or malformed: pasted-clause runs then stay off (fail
    closed) and nothing else is affected."""
    doc = store.get(CUSTOM_LIMITS_PATH)
    if doc is None:
        return None
    try:
        return CustomLimits.model_validate(doc)
    except ValidationError as e:
        log.warning(
            "%s is malformed (%d problem(s)); custom runs stay off", CUSTOM_LIMITS_PATH, e.error_count()
        )
        return None


def llm_cause(e: ClauseReviewError) -> LLMError | None:
    """The model call failure that ended a review (loop.py raises ClauseReviewError from it), else None. A
    monitor model call's LLMError reaches here bare too: monitor/core.py lets it propagate unwrapped."""
    cause = e.__cause__
    return cause if isinstance(cause, LLMError) else None


def busy_cause(e: ClauseReviewError) -> LLMError | None:
    """The provider failure behind a failed review (a 429, 5xx or timeout, on any model), else None."""
    cause = llm_cause(e)
    return cause if cause is not None and cause.capacity else None


def hands_over(e: LLMError) -> bool:
    """A failure the fallback model can help with: the model gave up busy, or ran out of its time."""
    return e.kind in ("throttled", "transient", "deadline")


def fallback_reason(e: LLMError) -> str:
    """Why the fallback model reviewed a clause, in plain English, from the agent model's failure: what the
    provider did on the tries it failed (LLMError.failures) and how many tries were made."""
    name = model_name(e.model or "the agent model")
    share = f"its {PRIMARY_PHASE_S:g} s share of the run budget"
    seen = set(e.failures) or ({e.cause} if e.cause is not None else set())
    if seen == {"throttled"}:
        what = f"{name} was busy (HTTP 429)"
    elif seen == {"transient"}:
        what = f"{name} did not answer (timeouts or HTTP 5xx)"
    elif seen:
        what = f"{name} was busy (HTTP 429) and did not answer (timeouts or HTTP 5xx)"
    else:
        return f"{name} was too slow to finish within {share}"
    if e.kind in ("throttled", "transient"):
        tries = f" after {e.attempts} {'try' if e.attempts == 1 else 'tries'}" if e.attempts else ""
        return what + tries
    return f"{what}{',' if len(seen) > 1 else ''} and no retry fitted in {share}"


def failed_finding(clause: Clause, reason: str) -> Finding:
    return Finding(
        clause_id=clause.id,
        clause_text=clause.text,
        label=FindingLabel.INSUFFICIENT_EVIDENCE,
        rationale=f"Argus could not finish reviewing this clause, so a person must review it. {reason}",
        citation=None,
        monitor_status=MonitorStatus.NEEDS_REVIEW,
        error=reason,
    )


# ---- one run ------------------------------------------------------------------------------------------


class Reviewer(Protocol):
    def __call__(
        self,
        llm: LLM,
        settings: Settings,
        corpus: Corpus,
        clause: Clause,
        emit: Callable[[AgentStep], None],
        *,
        next_seq: Callable[[], int] | None = None,
        run_id: str | None = None,
        hand_off: HandOff | None = None,
    ) -> ReviewOutcome: ...


@dataclass(frozen=True)
class ClauseResult:
    finding: Finding
    revised: bool = False
    stopped: bool = False

    @property
    def failed(self) -> bool:
        return self.finding.error is not None

    @property
    def fallback(self) -> bool:
        """Reviewed by the fallback agent model."""
        return not self.failed and self.finding.fallback_reason is not None


class _RunContext:
    """Shared state of one executing run. Every Firestore write of the run goes through the lock, so once
    the run is closed no worker can add a step or a finding behind the final state."""

    def __init__(self, store: DocumentStore, run: Run, now: Callable[[], datetime]) -> None:
        self.store = store
        self.run = run
        self.now = now
        self.lock = threading.Lock()
        self.closed = False
        self.results: dict[str, ClauseResult] = {}
        self.steps: list[AgentStep] = []
        self.step_seq = SeqCounter()
        self.audit_seq = SeqCounter()

    def audit_write(
        self, actor: str, event: str, detail: str, clause_id: str | None = None, model: str | None = None
    ) -> Write:
        seq = self.audit_seq()
        e = AuditEvent(
            seq=seq, at=self.now(), actor=actor, event=event, detail=detail, clause_id=clause_id, model=model
        )
        return Write("create", audit_path(self.run.run_id, seq), child_doc(self.run, e))

    def _step_writes(self, step: AgentStep) -> list[Write]:
        self.steps.append(step)
        actor = "monitor" if step.kind == StepKind.MONITOR else "agent"
        return [
            Write("create", step_path(self.run.run_id, step.seq), child_doc(self.run, step)),
            self.audit_write(actor, f"step_{step.kind.value}", step.summary, step.clause_id, step.model),
        ]

    def emit_step(self, step: AgentStep) -> None:
        with self.lock:
            if self.closed:
                raise RunClosed(f"run {self.run.run_id} is closed; step {step.seq} not written")
            self.store.commit(self._step_writes(step))

    def _finding_writes(self, result: ClauseResult) -> list[Write]:
        f = result.finding
        if result.failed:
            audit = self.audit_write("agent", "clause_failed", f.error or "", f.clause_id)
        else:
            audit = self.audit_write(
                "agent",
                "finding_recorded",
                f"{f.label.value}, monitor {f.monitor_status.value}"
                + (
                    f"; reviewed by {model_name(f.agent_model or '')}: {f.fallback_reason}"
                    if result.fallback
                    else ""
                ),
                f.clause_id,
                f.agent_model,
            )
        return [Write("set", finding_path(self.run.run_id, f.clause_id), child_doc(self.run, f)), audit]

    def finish_clause(self, result: ClauseResult) -> None:
        """A worker's result; refused once the run is closed (the clause is then saved as timed out)."""
        with self.lock:
            if self.closed:
                raise RunClosed(
                    f"run {self.run.run_id} is closed; finding for {result.finding.clause_id} dropped"
                )
            self.store.commit(self._finding_writes(result))
            self.results[result.finding.clause_id] = result

    def close(self) -> None:
        with self.lock:
            self.closed = True

    def fail_unfinished(self, clause: Clause, reason: str) -> None:
        """After close: record a clause that never finished, with a final trace step saying why."""
        with self.lock:
            if not self.closed or clause.id in self.results:
                raise AssertionError(f"fail_unfinished({clause.id}) on an open run or a finished clause")
            step = AgentStep(
                seq=self.step_seq(),
                clause_id=clause.id,
                kind=StepKind.FINAL,
                summary=f"Review of clause {clause.id} stopped: {reason}",
                at=self.now(),
            )
            result = ClauseResult(failed_finding(clause, reason))
            self.store.commit(self._step_writes(step) + self._finding_writes(result))
            self.results[clause.id] = result


def _summary(clauses: list[Clause], results: dict[str, ClauseResult]) -> RunSummary:
    s = RunSummary(clauses=len(clauses))
    for r in results.values():
        if r.failed:
            s.failed += 1
            continue
        setattr(s, r.finding.label.value, getattr(s, r.finding.label.value) + 1)
        s.monitor_revisions += int(r.revised)
        s.monitor_stops += int(r.stopped)
        s.fallback_clauses += int(r.fallback)
    return s


def _cost(steps: list[AgentStep]) -> float | None:
    """Sum of the recorded model calls' estimates; None when any call's cost is unknown."""
    costs = [s.cost_usd_estimate for s in steps if s.model is not None]
    if any(c is None for c in costs):
        return None
    return sum(c for c in costs if c is not None)


class Runner:
    def __init__(
        self,
        store: DocumentStore,
        llm: LLM,
        settings: Settings,
        corpus: Corpus,
        *,
        reviewer: Reviewer = review_clause,
        budget_s: float = RUN_BUDGET_S,
        parallel: int = PARALLEL_CLAUSES,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.perf_counter,
        breaker: Breaker | None = None,
    ) -> None:
        """`clock` is the deadline clock (llm.call_scope's, time.perf_counter); `breaker` defaults to this
        process's breaker for the agent model, used only when Settings.fallback_model is set."""
        self.store = store
        self.llm = llm
        self.settings = settings
        self.corpus = corpus
        self.reviewer = reviewer
        self.budget_s = budget_s
        self.parallel = parallel
        self.now = now
        self.clock = clock
        self.breaker = breaker if breaker is not None else process_breaker(settings.agent_model)

    def run_review(self, run_id: str, owner_uid: str, policy: Policy, *, enforce_cap: bool = True) -> Run:
        """Admit the run (or raise a Rejected subclass, writing nothing), review it, return the final Run."""
        run = self.admit(run_id, owner_uid, policy, enforce_cap=enforce_cap)
        return self.execute(run, policy)

    def admit(self, run_id: str, owner_uid: str, policy: Policy, *, enforce_cap: bool = True) -> Run:
        if not re.fullmatch(RUN_ID_PATTERN, run_id):
            raise ValueError(f"run_id {run_id!r} is not a uuid4 hex string")
        if len(policy.clauses) > self.parallel:
            raise ValueError(
                f"{policy.policy_id} has {len(policy.clauses)} clauses but the run has {self.parallel} "
                "workers; a clause would wait for a slot and eat into the run budget"
            )
        if policy.custom and not enforce_cap:
            raise ValueError("a custom run always goes through the caps (enforce_cap=True)")
        started = self.now()
        run = Run(
            run_id=run_id,
            owner_uid=owner_uid,
            policy_id=policy.policy_id,
            policy_title=policy.title,
            status=RunStatus.RUNNING,
            started_at=started,
            agent_model=self.settings.agent_model,
            monitor_model=self.settings.monitor_model,
            corpus_as_on=corpus_as_on(self.corpus).isoformat(),
            provider=self.settings.provider,
            rbi_as_on=self.corpus.rbi_as_on().isoformat(),
            clauses=[
                RunClause(clause_id=c.id, title=run_clause_title(policy, c), text=c.text)
                for c in policy.clauses
            ],
            summary=RunSummary(clauses=len(policy.clauses)),
            source=CUSTOM_SOURCE if policy.custom else None,
            expires_at=started + CUSTOM_TTL if policy.custom else None,
        )
        if not enforce_cap:
            if not read_limits(self.store).enabled:
                raise RunsDisabled(RUNS_PAUSED)
            try:
                self.store.create(run_path(run_id), run_doc(run))
            except AlreadyExists as e:
                raise DuplicateRun(f"Run {run_id} already exists") from e
            return run
        admit_counted(
            self.store, run_path(run_id), run_doc(run), owner_uid, started, "Run", custom=policy.custom
        )
        return run

    def execute(self, run: Run, policy: Policy) -> Run:
        ctx = _RunContext(self.store, run, self.now)
        # One instant on the deadline clock for the model calls' deadline, the wait and the latency.
        started = self.clock()
        deadline = started + self.budget_s
        try:
            self.store.commit(
                [
                    ctx.audit_write(
                        f"officer:{run.owner_uid}",
                        "run_started",
                        f"Review of {policy.title}: {len(policy.clauses)} "
                        + ("clause" if len(policy.clauses) == 1 else "clauses"),
                    )
                ]
            )
            pool = ThreadPoolExecutor(max_workers=self.parallel, thread_name_prefix=f"run-{run.run_id[:8]}")
            futures: list[Future[None]] = [
                pool.submit(self._review_one, ctx, c, deadline) for c in policy.clauses
            ]
            wait(futures, timeout=max(0.0, deadline - self.clock()))
            ctx.close()
            # Queued clauses are cancelled; one still waiting on the model ends at its next step (RunClosed).
            pool.shutdown(wait=False, cancel_futures=True)
            for fut in futures:
                if fut.done() and not fut.cancelled() and (err := fut.exception()) is not None:
                    raise err
            for clause in policy.clauses:
                if clause.id not in ctx.results:
                    ctx.fail_unfinished(clause, f"not finished within the {self.budget_s:g} s run budget.")
            return self._finish(ctx, policy, started)
        except Exception as e:
            self._mark_failed(run, ctx, e, started)
            raise

    def _review_one(self, ctx: _RunContext, clause: Clause, deadline: float) -> None:
        """Review one clause on a worker thread and save its finding (or its failure)."""
        try:
            ctx.finish_clause(self._review_clause(ctx, clause, deadline))
        except RunClosed:
            return

    def _review(
        self,
        ctx: _RunContext,
        clause: Clause,
        settings: Settings,
        deadline: float,
        hand_off: HandOff | None = None,
        agent_deadline: float | None = None,
    ) -> ReviewOutcome:
        """One review of `clause` with settings.agent_model. Its model calls end by `deadline` (call_scope): a
        stalled call is cut, or not retried, rather than running past it. The agent model's calls also end by
        `agent_deadline` when given, until its draft is written; the monitor model's do not. Raises
        ClauseReviewError."""
        own = {settings.agent_model: agent_deadline} if agent_deadline is not None else None
        with call_scope(deadline, model_deadlines=own, run_id=ctx.run.run_id, clause_id=clause.id) as scope:

            def emit(step: AgentStep) -> None:
                ctx.emit_step(step)
                # The draft is written: a hand-over would now throw it away, so the agent model's own
                # deadline ends here and the revise step, if the monitor asks for one, keeps the run's.
                if step.kind == StepKind.DRAFT:
                    scope.end_model_deadline(settings.agent_model)

            return self.reviewer(
                self.llm,
                settings,
                self.corpus,
                clause,
                emit,
                next_seq=ctx.step_seq,
                run_id=ctx.run.run_id,
                hand_off=hand_off,
            )

    def _review_clause(self, ctx: _RunContext, clause: Clause, deadline: float) -> ClauseResult:
        """The clause's result: reviewed by the agent model, or by the fallback model when the agent model
        gives up busy or runs out of its share of the time (see the module docstring), or failed."""
        primary, fallback = self.settings.agent_model, self.settings.fallback_model
        if fallback is None:
            try:
                outcome = self._review(ctx, clause, self.settings, deadline)
            except ClauseReviewError as e:
                return ClauseResult(failed_finding(clause, self._failure(e)))
            return ClauseResult(outcome.finding, revised=outcome.revised, stopped=outcome.stopped)

        route = self.breaker.route()
        if route == "fallback":
            busy, backup = model_name(primary), model_name(fallback)
            reason = f"{busy} was busy on recent clauses"  # the web line names the backup model itself
            ctx.emit_step(
                AgentStep(
                    seq=ctx.step_seq(),
                    clause_id=clause.id,
                    kind=StepKind.PLAN,
                    summary=f"{busy} is busy; reviewing this clause with {backup}.",
                    at=self.now(),
                )
            )
            return self._fallback_review(ctx, clause, deadline, reason, provider_failed=False)

        handed_over: list[LLMError] = []  # the agent model's failure, once its review hands over

        def hand_off(e: LLMError) -> str | None:
            if e.model != primary or not hands_over(e) or deadline - self.clock() < MIN_FALLBACK_S:
                return None
            handed_over.append(e)
            what = "is busy" if e.capacity else "ran out of time"
            return f"{model_name(primary)} {what}; handing this clause to {model_name(fallback)}."

        outcome_on_primary: Outcome = None
        try:
            try:
                outcome = self._review(
                    ctx,
                    clause,
                    self.settings,
                    deadline,
                    hand_off,
                    agent_deadline=min(deadline, self.clock() + PRIMARY_PHASE_S),
                )
            except ClauseReviewError as e:
                cause = busy_cause(e)
                outcome_on_primary = "busy" if cause is not None and cause.model == primary else "ok"
                self.breaker.report(route, outcome_on_primary)
                if not handed_over:
                    return ClauseResult(failed_finding(clause, self._failure(e)))
                reason = fallback_reason(handed_over[0])
                log_event(
                    log,
                    "clause_fallback",
                    run_id=ctx.run.run_id,
                    clause_id=clause.id,
                    model=primary,
                    fallback_model=fallback,
                    reason=reason,
                )
                return self._fallback_review(
                    ctx, clause, deadline, reason, provider_failed=handed_over[0].capacity
                )
            outcome_on_primary = "ok"
            self.breaker.report(route, outcome_on_primary)
            return ClauseResult(outcome.finding, revised=outcome.revised, stopped=outcome.stopped)
        finally:
            if outcome_on_primary is None:  # ended some other way (the run closed): outcome unknown
                self.breaker.report(route, None)

    def _fallback_review(
        self, ctx: _RunContext, clause: Clause, deadline: float, reason: str, *, provider_failed: bool
    ) -> ClauseResult:
        """Review `clause` again from the start with the fallback agent model, labelled with `reason`.
        `provider_failed`: the provider failed this clause on the agent model before the hand-over."""
        assert self.settings.fallback_model is not None
        settings = replace(self.settings, agent_model=self.settings.fallback_model, fallback_model=None)
        try:
            outcome = self._review(ctx, clause, settings, deadline)
        except ClauseReviewError as e:
            return ClauseResult(failed_finding(clause, self._failure(e, reason, provider_failed)))
        finding = outcome.finding.model_copy(update={"fallback_reason": reason})
        return ClauseResult(finding, revised=outcome.revised, stopped=outcome.stopped)

    def _failure(
        self, e: ClauseReviewError, fallback_reason: str | None = None, provider_failed: bool = False
    ) -> str:
        """A failed clause's error: what failed, after why the fallback model reviewed it when it did. It
        starts with PROVIDER_BUSY when the provider failed the review, or when our deadline ended a review
        that the provider's failure on the agent model had sent to the fallback model (`provider_failed`);
        else with OUT_OF_TIME when our deadline ended it. Any other failure (a refused request, an unusable
        reply) is stated as it is."""
        text = f"{e}."
        if fallback_reason is not None:
            text = (
                f"{fallback_reason}, so {model_name(self.settings.fallback_model or '')} reviewed it again, "
                f"and that review failed: {text}"
            )
        cause = llm_cause(e)
        out_of_time = cause is not None and cause.out_of_time
        if busy_cause(e) is not None or (out_of_time and provider_failed):
            return f"{PROVIDER_BUSY} {text}"
        return f"{OUT_OF_TIME} {text}" if out_of_time else text

    def _finish(self, ctx: _RunContext, policy: Policy, started: float) -> Run:
        results = [ctx.results[c.id] for c in policy.clauses]
        failed = [r.finding for r in results if r.failed]
        error = (
            f"{len(failed)} of {len(results)} clauses failed: "
            + "; ".join(f"{f.clause_id}: {f.error}" for f in failed)
            if failed
            else None
        )
        run = ctx.run.model_copy(
            update={
                "status": RunStatus.FAILED if failed else RunStatus.DONE,
                "error": error,
                "finished_at": self.now(),
                "summary": _summary(policy.clauses, ctx.results),
                "latency_ms": round((self.clock() - started) * 1000),
                "cost_usd_estimate": _cost(ctx.steps),
            }
        )
        self.store.commit(
            [
                Write("set", run_path(run.run_id), run_doc(run)),
                ctx.audit_write(
                    "agent", "run_finished", f"status {run.status.value}" + (f": {error}" if error else "")
                ),
            ]
        )
        return run

    def _mark_failed(self, run: Run, ctx: _RunContext, e: Exception, started: float) -> None:
        ctx.close()
        failed = run.model_copy(
            update={
                "status": RunStatus.FAILED,
                "error": f"The run stopped on an internal error: {type(e).__name__}: {e}",
                "finished_at": self.now(),
                "latency_ms": round((self.clock() - started) * 1000),
                "cost_usd_estimate": _cost(ctx.steps),
            }
        )
        self.store.set(run_path(run.run_id), run_doc(failed))


# ---- officer sign-off ---------------------------------------------------------------------------------


def _next_audit_seq(store: DocumentStore, run_id: str) -> int:
    seqs = [int(doc_id) for doc_id, _ in store.list_docs(f"{run_path(run_id)}/audit")]
    return max(seqs, default=0) + 1


def sign_off(
    store: DocumentStore,
    run_id: str,
    clause_id: str,
    uid: str,
    decision: SignoffDecision,
    note: str,
    edited_rationale: str | None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Finding:
    """Record the officer's decision on one finding plus an audit row, atomically. Owner only; saved sample
    runs and runs still in progress are refused."""
    if store.get(run_path(run_id, SAMPLES)) is not None:
        raise Forbidden("Saved sample runs are read-only. Start a live run to sign off findings.")
    doc = store.get(run_path(run_id))
    if doc is None:
        raise Missing(f"Run {run_id} not found")
    run = Run.model_validate(doc)
    if run.saved:
        raise Forbidden("Saved sample runs are read-only. Start a live run to sign off findings.")
    if run.owner_uid != uid:
        raise Forbidden("Only the officer who started this run can sign off its findings.")
    if run.status == RunStatus.RUNNING:
        raise Conflict(f"Run {run_id} is still running; sign off when it has finished.")
    if decision == SignoffDecision.EDIT:
        if edited_rationale is None or not edited_rationale.strip():
            raise Invalid("An edit needs the edited rationale.")
    elif edited_rationale is not None:
        raise Invalid(f"edited_rationale is only for decision 'edit', not {decision.value!r}.")
    fdoc = store.get(finding_path(run_id, clause_id))
    if fdoc is None:
        raise Missing(f"Run {run_id} has no finding for clause {clause_id}")
    finding = Finding.model_validate(fdoc)
    signoff = Signoff(
        decision=decision,
        by=uid,
        at=now(),
        note=note,
        edited_rationale=edited_rationale.strip() if edited_rationale is not None else None,
    )
    detail = note + (f" Edited rationale: {signoff.edited_rationale}" if signoff.edited_rationale else "")
    for _ in range(AUDIT_ATTEMPTS):
        seq = _next_audit_seq(store, run_id)
        event = AuditEvent(
            seq=seq,
            at=signoff.at,
            actor=f"officer:{uid}",
            event=f"signoff_{decision.value}",
            detail=detail.strip(),
            clause_id=clause_id,
        )
        try:
            store.commit(
                [
                    Write("update", finding_path(run_id, clause_id), {"signoff": _json(signoff)}),
                    Write("create", audit_path(run_id, seq), child_doc(run, event)),
                ]
            )
        except AlreadyExists:
            continue  # another sign-off took this audit seq; read the log again
        return finding.model_copy(update={"signoff": signoff})
    raise StoreError(
        f"could not allocate an audit sequence number for run {run_id} in {AUDIT_ATTEMPTS} tries"
    )


# ---- saved sample runs --------------------------------------------------------------------------------


class NotASample(Conflict):
    """The run cannot become a public saved sample: it is not a finished, real, pinned-model Vertex review."""


def sample_refusal(run: Run) -> str | None:
    """Why `run` may not be shown publicly as a saved run, or None when it may. A saved run is presented as
    a real review, so it must be one: finished, recorded on Vertex AI with the pinned model ids, not written
    by a seed script, reviewed wholly by the pinned agent model (no clause handed to the fallback model), and
    dated with the RBI as-on date of its library. A pasted clause (source custom) is the user's own text,
    kept only until its private run expires (CUSTOM_TTL): never published. No override.
    """
    if run.source == CUSTOM_SOURCE:
        return "it reviews a clause a user pasted (source custom); only a sample policy run can be saved"
    if run.status != RunStatus.DONE or run.finished_at is None:
        return f"it is {run.status.value}; only a finished (done) run can be saved"
    if run.seeded:
        return "it was written by a seed script (seeded), not recorded from a real review"
    if run.provider != SAMPLE_PROVIDER:
        return f"its provider is {run.provider!r}; a saved run must be recorded on {SAMPLE_PROVIDER!r}"
    pinned = PINNED_MODELS[SAMPLE_PROVIDER]
    if (run.agent_model, run.monitor_model) != pinned:
        return (
            f"its models are {run.agent_model!r} / {run.monitor_model!r}, not the pinned {SAMPLE_PROVIDER} "
            f"models {pinned[0]!r} / {pinned[1]!r} (config.PINNED_MODELS)"
        )
    if run.rbi_as_on is None:
        return "it has no rbi_as_on date, so the app could not say how current its RBI rules are"
    if run.summary.fallback_clauses > 0:
        return (
            f"{run.summary.fallback_clauses} of its clauses were reviewed by the fallback model because "
            f"{model_name(run.agent_model)} was busy; a saved run shows the pinned models only"
        )
    return None


def save_sample(store: DocumentStore, run_id: str) -> Run:
    """Copy finished run runs/{id} with its steps, findings and audit to the public samples/{id}, saved=true.
    Timestamps are kept, so the sample is labelled with the time it was recorded. Never overwrites a sample.
    Refuses (NotASample) any run sample_refusal names, before writing anything.
    """
    doc = store.get(run_path(run_id))
    if doc is None:
        raise Missing(f"Run {run_id} not found")
    run = Run.model_validate(doc)
    refusal = sample_refusal(run)
    if refusal is not None:
        raise NotASample(f"Run {run_id} cannot be saved as a sample: {refusal}")
    if store.get(run_path(run_id, SAMPLES)) is not None:
        raise DuplicateRun(f"samples/{run_id} already exists")
    writes = [
        Write("set", f"{run_path(run_id, SAMPLES)}/{child}/{doc_id}", data)
        for child in ("steps", "findings", "audit")
        for doc_id, data in store.list_docs(f"{run_path(run_id)}/{child}")
    ]
    for start in range(0, len(writes), FIRESTORE_BATCH_LIMIT):
        store.commit(writes[start : start + FIRESTORE_BATCH_LIMIT])
    saved = run.model_copy(update={"saved": True})
    store.create(run_path(run_id, SAMPLES), _json(saved))  # last, so a listener never sees a half copy
    return saved
