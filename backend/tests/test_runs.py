"""Runs end to end offline: the real agent loop and monitor over scripted per-clause FakeLLM conversations,
persisted to the in-memory store that implements the Firestore adapter's contract. Expected values come from
the sample policies, the gold findings, the corpus fixture and the production constants."""

import json
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from argus_api import main, runs
from argus_api.agent import AgentStep, Clause, ClauseReviewError, ReviewOutcome
from argus_api.agent.loop import IST
from argus_api.config import PINNED_MODELS, Settings
from argus_api.corpus.store import Corpus
from argus_api.docstore import Counted, CreateResult, StoreError
from argus_api.docstore import Counter as UsageCounter
from argus_api.llm import LLM
from argus_api.llm_fake import FakeLLM, turn
from argus_api.schema import (
    SCHEMA_VERSION,
    AuditEvent,
    Finding,
    FindingLabel,
    Limits,
    MonitorStatus,
    Run,
    RunStatus,
    RunSummary,
    SignoffDecision,
    Step,
    StepKind,
)
from argus_api.store_fake import MemoryStore
from tests.run_support import (
    MONITOR_MODEL,
    draft_reply,
    failing_clause_llm,
    fixture_corpus,
    gold,
    judgement,
    policy_llm,
    settings,
    vertex_settings,
)

OWNER, OTHER = "uid-owner", "uid-other"
CAP = 3


def limits(
    daily: int = CAP, enabled: bool = True, *, hourly: int | None = None, user: int | None = None
) -> dict[str, Any]:
    """config/limits; the hourly and per-user caps default to the daily one, so only the daily cap binds."""
    return Limits(
        daily_run_cap=daily,
        hourly_run_cap=daily if hourly is None else hourly,
        per_user_daily_run_cap=daily if user is None else user,
        enabled=enabled,
    ).model_dump()


def new_run_id() -> str:
    import uuid

    return uuid.uuid4().hex


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return fixture_corpus()


@pytest.fixture()
def store() -> MemoryStore:
    s = MemoryStore()
    s.set(runs.LIMITS_PATH, limits())
    return s


def runner(store: MemoryStore, llm: LLM, corpus: Corpus, **kw: Any) -> runs.Runner:
    return runs.Runner(store, llm, settings(), corpus, **kw)


def docs(
    store: MemoryStore, run_id: str, child: str, root: str = runs.RUNS
) -> list[tuple[str, dict[str, Any]]]:
    return store.list_docs(f"{runs.run_path(run_id, root)}/{child}")


def findings(store: MemoryStore, run_id: str) -> dict[str, Finding]:
    return {i: Finding.model_validate(d) for i, d in docs(store, run_id, "findings")}


def today_count(store: MemoryStore) -> int:
    """Admissions counted on the day documents (usage/{day}); hour and user counters live below them."""
    days = [
        d for p, d in store.docs.items() if p.startswith(f"{runs.USAGE_COLLECTION}/") and p.count("/") == 1
    ]
    return sum(d[runs.USAGE_FIELD] for d in days)


# ---- a whole run ----------------------------------------------------------------------------------------


def test_full_run_writes_the_documented_layout(store: MemoryStore, corpus: Corpus) -> None:
    policy = runs.load_policy("policy_a")
    llm = policy_llm("policy_a")
    run_id = new_run_id()
    run = runner(store, llm, corpus).run_review(run_id, OWNER, policy)

    assert all(r == {"complete": 0, "complete_json": 0, "chat": 0} for r in llm.remaining().values())
    assert run.status == RunStatus.DONE and run.error is None
    assert Run.model_validate(store.get(runs.run_path(run_id))) == run
    assert run.schema_version == SCHEMA_VERSION
    assert run.owner_uid == OWNER and run.policy_title == policy.title
    assert [c.clause_id for c in run.clauses] == [c.id for c in policy.clauses]
    assert [c.text for c in run.clauses] == [c.text for c in policy.clauses]
    assert all(c.title == runs.clause_title(c.text) for c in run.clauses)
    assert run.finished_at is not None and run.latency_ms is not None

    # Steps: zero-padded seq ids, unique seqs shared across the parallel clauses, each one trace line.
    steps = docs(store, run_id, "steps")
    assert [doc_id for doc_id, _ in steps] == [runs.seq_id(s["seq"]) for _, s in steps]
    seqs = sorted(s["seq"] for _, s in steps)
    assert seqs == list(range(1, len(steps) + 1))
    parsed = [AgentStep.model_validate(s) for _, s in steps]
    assert {s.clause_id for s in parsed} == {c.id for c in policy.clauses}
    assert all(Step.model_validate(s) for _, s in steps)

    # Findings: one per clause, labels as the gold set (the scripted drafts), citations from the corpus.
    found = findings(store, run_id)
    assert set(found) == {c.id for c in policy.clauses}
    for cid, f in found.items():
        assert f.error is None
        assert f.label == FindingLabel(gold("policy_a", cid)["label"])
        assert f.citation is not None
        p = corpus.paragraph(f.citation.instrument_ref, f.citation.para)
        assert p is not None and f.citation.content_hash == p.content_hash

    # Summary counts are the findings' own.
    labels = Counter(f.label.value for f in found.values())
    expected = RunSummary(clauses=len(policy.clauses), **labels)
    assert run.summary.model_dump(exclude={"monitor_revisions", "monitor_stops"}) == expected.model_dump(
        exclude={"monitor_revisions", "monitor_stops"}
    )
    assert run.summary.monitor_stops == sum(
        1 for s in parsed if s.verdict is not None and s.verdict.action.value == "stop"
    )

    # Audit: run start by the officer, one row per step, one per finding, run end; contiguous seqs.
    audit = [AuditEvent.model_validate(d) for _, d in docs(store, run_id, "audit")]
    assert [a.seq for a in audit] == list(range(1, len(audit) + 1))
    events = Counter(a.event for a in audit)
    assert events["run_started"] == 1 and events["run_finished"] == 1
    assert events["finding_recorded"] == len(policy.clauses)
    assert sum(n for e, n in events.items() if e.startswith("step_")) == len(steps)
    assert audit[0].actor == f"officer:{OWNER}"
    monitor_rows = [a for a in audit if a.event == f"step_{StepKind.MONITOR.value}"]
    assert monitor_rows and all(a.actor == "monitor" and a.model == MONITOR_MODEL for a in monitor_rows)


def test_outdated_reference_carries_the_withdrawal_date(store: MemoryStore, corpus: Corpus) -> None:
    run_id = new_run_id()
    runner(store, policy_llm("policy_a"), corpus).run_review(run_id, OWNER, runs.load_policy("policy_a"))
    outdated = [f for f in findings(store, run_id).values() if f.label == FindingLabel.OUTDATED_REFERENCE]
    assert outdated
    for f in outdated:
        assert f.replaced_citation is not None and not f.replaced_citation.in_force
        valid_to = corpus.instrument(f.replaced_citation.instrument_ref).valid_to
        assert valid_to is not None and f.replaced_citation.withdrawn_on == valid_to.isoformat()
        withdrawn_by = corpus.instrument(f.replaced_citation.instrument_ref).withdrawn_by
        assert withdrawn_by is not None and f.replaced_citation.withdrawn_by == withdrawn_by
        assert f.replaced_citation.updated_as_on is None  # RBI shows no "updated as on" for a withdrawn one
        assert f.citation is not None and f.citation.in_force and f.citation.withdrawn_on is None
        assert f.citation.withdrawn_by is None
        as_on = corpus.instrument(f.citation.instrument_ref).updated_as_on
        assert as_on is not None and f.citation.updated_as_on == as_on.isoformat()


def test_run_records_provider_and_rbi_as_on(store: MemoryStore, corpus: Corpus) -> None:
    run_id = new_run_id()
    run = runner(store, policy_llm("policy_a"), corpus).run_review(
        run_id, OWNER, runs.load_policy("policy_a")
    )
    [current] = [i for i in corpus.instruments.values() if i.in_force]
    assert current.updated_as_on is not None
    assert run.provider == settings().provider and run.seeded is False
    assert run.rbi_as_on == current.updated_as_on.isoformat() == corpus.rbi_as_on().isoformat()
    assert run.corpus_as_on == current.snapshot_date.isoformat()  # the fetch date, kept apart
    assert Run.model_validate(store.get(runs.run_path(run_id))) == run


def test_llm_error_fails_one_clause_and_the_run_still_finishes(store: MemoryStore, corpus: Corpus) -> None:
    policy = runs.load_policy("policy_b")
    bad = policy.clauses[2].id
    run_id = new_run_id()
    run = runner(store, policy_llm("policy_b", [(bad, failing_clause_llm())]), corpus).run_review(
        run_id, OWNER, policy
    )

    assert run.status == RunStatus.FAILED
    assert run.summary.failed == 1 and run.summary.clauses == len(policy.clauses)
    label_total = sum(getattr(run.summary, label.value) for label in FindingLabel)
    assert label_total == len(policy.clauses) - 1
    assert run.error is not None and f"{bad}:" in run.error and "LLMError" in run.error

    found = findings(store, run_id)
    assert set(found) == {c.id for c in policy.clauses}  # nothing dropped
    f = found[bad]
    assert f.error is not None and "LLMError" in f.error
    assert (f.label, f.citation, f.monitor_status) == (
        FindingLabel.INSUFFICIENT_EVIDENCE,
        None,
        MonitorStatus.NEEDS_REVIEW,
    )
    assert all(found[c.id].error is None for c in policy.clauses if c.id != bad)
    steps = [AgentStep.model_validate(d) for _, d in docs(store, run_id, "steps")]
    last = [s for s in steps if s.clause_id == bad][-1]
    assert last.kind == StepKind.FINAL and "failed" in last.summary
    audit = [AuditEvent.model_validate(d) for _, d in docs(store, run_id, "audit")]
    assert [a.clause_id for a in audit if a.event == "clause_failed"] == [bad]


# ---- the run budget, with a scripted reviewer ---------------------------------------------------------


def scripted_reviewer(
    corpus: Corpus, slow: set[str], release: threading.Event, started: list[str] | None = None
) -> Callable[..., ReviewOutcome]:
    """A reviewer that emits a plan step, blocks on `release` for clauses in `slow`, then emits a final step
    and returns a compliant finding citing a real in-force paragraph."""
    para = next(p for p in corpus.paragraphs.values() if p.in_force)

    def review(
        llm: LLM,
        s: Settings,
        c: Corpus,
        clause: Clause,
        emit: Callable[[AgentStep], None],
        *,
        next_seq: Callable[[], int] | None = None,
        run_id: str | None = None,
    ) -> ReviewOutcome:
        assert next_seq is not None
        if started is not None:
            started.append(clause.id)
        now = datetime.now(UTC)
        emit(AgentStep(seq=next_seq(), clause_id=clause.id, kind=StepKind.PLAN, summary="Started.", at=now))
        if clause.id in slow:
            release.wait(timeout=10)
        emit(AgentStep(seq=next_seq(), clause_id=clause.id, kind=StepKind.FINAL, summary="Done.", at=now))
        from argus_api.schema import Citation

        finding = Finding(
            clause_id=clause.id,
            clause_text=clause.text,
            label=FindingLabel.COMPLIANT,
            rationale="Scripted.",
            citation=Citation(
                instrument=para.instrument,
                instrument_ref=para.instrument_ref,
                para=para.para,
                url=para.url,
                quote=para.text,
                snapshot_date=para.snapshot_date.isoformat(),
                content_hash=para.content_hash,
                in_force=True,
            ),
            monitor_status=MonitorStatus.PASSED,
        )
        return ReviewOutcome(finding, [], False, False, 0, None, None, None, 0)

    return review


def test_an_injected_compliant_draft_is_counted_as_insufficient_evidence(
    store: MemoryStore, corpus: Corpus
) -> None:
    injected = "c2"
    assert "inject" in gold("policy_b", injected)["rationale"].lower()
    draft = {**draft_reply("policy_b", injected), "label": FindingLabel.COMPLIANT.value}
    obeying = FakeLLM(turns=[turn(text="Ready to draft.")], json_replies=[draft, judgement()])
    llm = policy_llm("policy_b", [(injected, obeying)])
    run_id = new_run_id()
    run = runner(store, llm, corpus).run_review(run_id, OWNER, runs.load_policy("policy_b"))

    assert findings(store, run_id)[injected].label == FindingLabel.INSUFFICIENT_EVIDENCE
    gold_labels = Counter(gold("policy_b", c.id)["label"] for c in runs.load_policy("policy_b").clauses)
    assert run.summary.compliant == gold_labels[FindingLabel.COMPLIANT.value]
    assert run.summary.monitor_stops == 1


def test_clauses_unfinished_at_the_budget_end_as_failed_and_late_steps_are_refused(
    store: MemoryStore, corpus: Corpus
) -> None:
    policy = runs.load_policy("policy_a")
    slow = {policy.clauses[0].id}
    release = threading.Event()
    budget = 0.3
    r = runner(
        store,
        policy_llm("policy_a"),
        corpus,
        reviewer=scripted_reviewer(corpus, slow, release),
        budget_s=budget,
    )
    run_id = new_run_id()
    t0 = time.perf_counter()
    run = r.run_review(run_id, OWNER, policy)
    assert time.perf_counter() - t0 < budget + 2.0  # the request does not wait for the stuck clause

    assert run.status == RunStatus.FAILED and run.summary.failed == len(slow)
    found = findings(store, run_id)
    assert set(found) == {c.id for c in policy.clauses}
    for cid in slow:
        assert found[cid].error is not None and f"{budget:g} s run budget" in found[cid].error
    written = len(store.writes)
    release.set()  # the stuck clause wakes up and tries to emit its final step and finding
    time.sleep(0.2)
    assert len(store.writes) == written
    assert all(
        found[c.id] == f for c, f in zip(policy.clauses, findings(store, run_id).values(), strict=True)
    )


def test_a_policy_larger_than_the_pool_is_refused_before_anything_is_written(
    store: MemoryStore, corpus: Corpus
) -> None:
    policy = runs.load_policy("policy_b")
    before = dict(store.docs)
    r = runner(store, policy_llm("policy_b"), corpus, parallel=len(policy.clauses) - 1)
    with pytest.raises(ValueError, match="workers"):
        r.run_review(new_run_id(), OWNER, policy)
    assert store.docs == before


def test_sample_policies_fit_the_clause_limit() -> None:
    assert runs.PARALLEL_CLAUSES >= runs.MAX_CLAUSES
    for pid in runs.POLICY_IDS:
        assert len(runs.load_policy(pid).clauses) <= runs.MAX_CLAUSES


@pytest.mark.parametrize("policy_id", runs.POLICY_IDS)
def test_every_clause_starts_at_once(store: MemoryStore, corpus: Corpus, policy_id: str) -> None:
    policy = runs.load_policy(policy_id)
    # Each clause waits until all have started; a clause queued behind a busy worker would break the barrier.
    barrier = threading.Barrier(len(policy.clauses), timeout=5)
    inner = scripted_reviewer(corpus, set(), threading.Event())

    def review(*args: Any, **kw: Any) -> ReviewOutcome:
        barrier.wait()
        return inner(*args, **kw)

    run = runner(store, policy_llm(policy_id), corpus, reviewer=review).run_review(
        new_run_id(), OWNER, policy
    )
    assert run.status == RunStatus.DONE and not barrier.broken


def test_cost_is_summed_from_the_recorded_model_calls(store: MemoryStore, corpus: Corpus) -> None:
    costs = [0.25, 0.5]
    inner = scripted_reviewer(corpus, set(), threading.Event())

    def review(
        llm: LLM, s: Settings, c: Corpus, clause: Clause, emit: Callable[[AgentStep], None], **kw: Any
    ) -> ReviewOutcome:
        nxt = kw["next_seq"]
        for cost in costs:
            emit(
                AgentStep(
                    seq=nxt(),
                    clause_id=clause.id,
                    kind=StepKind.DRAFT,
                    summary="Drafted.",
                    at=datetime.now(UTC),
                    model=s.agent_model,
                    input_tokens=1,
                    output_tokens=1,
                    cost_usd_estimate=cost,
                )
            )
        return inner(llm, s, c, clause, emit, **kw)

    policy = runs.load_policy("policy_a")
    run = runner(store, policy_llm("policy_a"), corpus, reviewer=review).run_review(
        new_run_id(), OWNER, policy
    )
    assert run.cost_usd_estimate == pytest.approx(sum(costs) * len(policy.clauses))


def test_firestore_failure_marks_the_run_failed_and_surfaces(store: MemoryStore, corpus: Corpus) -> None:
    run_id = new_run_id()
    store.fail_on = lambda path: "/steps/" in path
    with pytest.raises(StoreError):
        runner(store, policy_llm("policy_a"), corpus).run_review(run_id, OWNER, runs.load_policy("policy_a"))
    run = Run.model_validate(store.get(runs.run_path(run_id)))
    assert run.status == RunStatus.FAILED and run.error is not None and "StoreError" in run.error


def test_clause_review_error_from_the_reviewer_is_a_failed_clause(store: MemoryStore, corpus: Corpus) -> None:
    policy = runs.load_policy("policy_a")
    bad = policy.clauses[-1].id
    inner = scripted_reviewer(corpus, set(), threading.Event())

    def review(
        llm: LLM, s: Settings, c: Corpus, clause: Clause, emit: Callable[[AgentStep], None], **kw: Any
    ) -> ReviewOutcome:
        if clause.id == bad:
            raise ClauseReviewError(clause.id, StepKind.MONITOR, "MonitorError: no verdict")
        return inner(llm, s, c, clause, emit, **kw)

    run_id = new_run_id()
    run = runner(store, policy_llm("policy_a"), corpus, reviewer=review).run_review(run_id, OWNER, policy)
    assert run.summary.failed == 1
    assert "monitor step" in (findings(store, run_id)[bad].error or "")


def test_every_model_call_of_a_clause_ends_by_the_run_deadline(store: MemoryStore, corpus: Corpus) -> None:
    """The runner scopes each clause's calls to the run budget, so a provider attempt is cut to the time
    left (llm.call_scope), and the scope carries the run and clause for the retry logs."""
    from argus_api.llm import REQUEST_TIMEOUT_S, OpenRouterLLM
    from tests.test_llm import FakeOpenAI, or_reply

    policy = runs.load_policy("policy_a")
    budget = REQUEST_TIMEOUT_S / 3
    client = FakeOpenAI([or_reply("ok") for _ in policy.clauses])
    provider = OpenRouterLLM(None, client=client)  # type: ignore[arg-type]
    inner = scripted_reviewer(corpus, set(), threading.Event())

    def review(
        llm: LLM, s: Settings, c: Corpus, clause: Clause, emit: Callable[[AgentStep], None], **kw: Any
    ) -> ReviewOutcome:
        provider.complete(s.agent_model, clause.text)
        return inner(llm, s, c, clause, emit, **kw)

    run = runner(store, policy_llm("policy_a"), corpus, reviewer=review, budget_s=budget).run_review(
        new_run_id(), OWNER, policy
    )
    assert run.status == RunStatus.DONE
    timeouts = [r["timeout"] for r in client.completions.requests]
    assert len(timeouts) == len(policy.clauses)
    assert all(0 < t <= budget for t in timeouts)


# ---- admission: kill switch, daily cap, duplicates ---------------------------------------------------


def test_kill_switch_refuses_and_writes_nothing(store: MemoryStore, corpus: Corpus) -> None:
    store.set(runs.LIMITS_PATH, limits(enabled=False))
    before = dict(store.docs)
    with pytest.raises(runs.RunsDisabled) as e:
        runner(store, policy_llm("policy_a"), corpus).run_review(
            new_run_id(), OWNER, runs.load_policy("policy_a")
        )
    assert e.value.status_code == 503
    assert store.docs == before
    # The kill switch holds for eval runs too.
    with pytest.raises(runs.RunsDisabled):
        runner(store, policy_llm("policy_a"), corpus).run_review(
            new_run_id(), OWNER, runs.load_policy("policy_a"), enforce_cap=False
        )


@pytest.mark.parametrize(
    "doc",
    [
        None,
        {"enabled": True},
        {"daily_run_cap": 5, "enabled": True},  # written before the hourly and per-user caps existed
        {"daily_run_cap": "5", "hourly_run_cap": 5, "per_user_daily_run_cap": 5, "enabled": True},
        {"daily_run_cap": 5, "hourly_run_cap": 5, "per_user_daily_run_cap": 5, "enabled": "yes"},
    ],
)
def test_missing_or_malformed_limits_refuse(
    store: MemoryStore, corpus: Corpus, doc: dict[str, Any] | None
) -> None:
    store.docs.pop(runs.LIMITS_PATH)
    if doc is not None:
        store.set(runs.LIMITS_PATH, doc)
    with pytest.raises(runs.LimitsMissing) as e:
        runner(store, policy_llm("policy_a"), corpus).admit(new_run_id(), OWNER, runs.load_policy("policy_a"))
    assert e.value.status_code == 503


def test_daily_cap_counts_runs_and_refuses_at_the_cap(store: MemoryStore, corpus: Corpus) -> None:
    r = runner(store, policy_llm("policy_a"), corpus)
    policy = runs.load_policy("policy_a")
    for n in range(1, CAP + 1):
        r.admit(new_run_id(), OWNER, policy)
        assert today_count(store) == n
    refused = new_run_id()
    with pytest.raises(runs.DailyCapReached) as e:
        r.admit(refused, OWNER, policy)
    assert e.value.status_code == 429 and str(CAP) in str(e.value)
    assert store.get(runs.run_path(refused)) is None and today_count(store) == CAP
    # Server-side eval bypass: not capped and not counted.
    bypass = new_run_id()
    r.admit(bypass, OWNER, policy, enforce_cap=False)
    assert store.get(runs.run_path(bypass)) is not None and today_count(store) == CAP


def test_hourly_cap_refuses_with_its_own_message(store: MemoryStore, corpus: Corpus) -> None:
    store.set(runs.LIMITS_PATH, limits(hourly=1))
    r = runner(store, policy_llm("policy_a"), corpus)
    policy = runs.load_policy("policy_a")
    r.admit(new_run_id(), OWNER, policy)
    with pytest.raises(runs.DailyCapReached, match="hour") as e:
        r.admit(new_run_id(), OTHER, policy)
    assert e.value.status_code == 429 and today_count(store) == 1


def test_per_user_cap_stops_one_user_but_not_the_next(store: MemoryStore, corpus: Corpus) -> None:
    per_user = CAP - 1
    store.set(runs.LIMITS_PATH, limits(user=per_user))
    r = runner(store, policy_llm("policy_a"), corpus)
    policy = runs.load_policy("policy_a")
    for _ in range(per_user):
        r.admit(new_run_id(), OWNER, policy)
    refused = new_run_id()
    with pytest.raises(runs.DailyCapReached, match="your") as e:
        r.admit(refused, OWNER, policy)
    assert str(per_user) in str(e.value) and store.get(runs.run_path(refused)) is None
    r.admit(new_run_id(), OTHER, policy)
    day = datetime.now(IST).date().isoformat()
    assert store.get(runs.user_usage_path(day, OWNER)) == {runs.USAGE_FIELD: per_user}
    assert store.get(runs.user_usage_path(day, OTHER)) == {runs.USAGE_FIELD: 1}
    assert today_count(store) == per_user + 1


def test_duplicate_run_id_is_refused_without_counting(store: MemoryStore, corpus: Corpus) -> None:
    r = runner(store, policy_llm("policy_a"), corpus)
    run_id = new_run_id()
    first = r.admit(run_id, OWNER, runs.load_policy("policy_a"))
    for enforce in (True, False):
        with pytest.raises(runs.DuplicateRun) as e:
            r.admit(run_id, OTHER, runs.load_policy("policy_b"), enforce_cap=enforce)
        assert e.value.status_code == 409
    assert Run.model_validate(store.get(runs.run_path(run_id))) == first and today_count(store) == 1


def test_run_doc_is_running_before_any_clause_is_reviewed(store: MemoryStore, corpus: Corpus) -> None:
    run_id = new_run_id()
    seen: list[RunStatus] = []
    inner = scripted_reviewer(corpus, set(), threading.Event())

    def review(*args: Any, **kw: Any) -> ReviewOutcome:
        seen.append(Run.model_validate(store.get(runs.run_path(run_id))).status)
        return inner(*args, **kw)

    runner(store, policy_llm("policy_a"), corpus, reviewer=review).run_review(
        run_id, OWNER, runs.load_policy("policy_a")
    )
    assert seen and set(seen) == {RunStatus.RUNNING}


def test_memory_store_create_counted_contract() -> None:
    s = MemoryStore()
    day, user = UsageCounter("usage/d", "runs", 5), UsageCounter("usage/d/users/u", "runs", 1)
    assert s.create_counted("runs/a", {"x": 1}, [day, user]) == Counted(CreateResult.CREATED)
    assert s.create_counted("runs/a", {"x": 2}, [day, user]) == Counted(CreateResult.EXISTS)
    assert s.create_counted("runs/b", {"x": 3}, [day, user]) == Counted(CreateResult.CAP_REACHED, user)
    assert s.get("runs/a") == {"x": 1} and s.get("runs/b") is None
    assert s.get("usage/d") == {"runs": 1} and s.get("usage/d/users/u") == {"runs": 1}
    with pytest.raises(ValueError):
        s.create_counted("runs/c", {}, [day, day])
    with pytest.raises(ValueError):
        s.create_counted("runs/c", {}, [])


def test_policies_load_from_the_sample_files() -> None:
    for pid in runs.POLICY_IDS:
        p = runs.load_policy(pid)
        assert p.policy_id == pid and p.clauses
    with pytest.raises(runs.UnknownPolicy) as e:
        runs.load_policy("policy_z")
    assert e.value.status_code == 404


# ---- sign-off -------------------------------------------------------------------------------------------


@pytest.fixture()
def finished(store: MemoryStore, corpus: Corpus) -> tuple[str, str]:
    """A finished run recorded as the deployed app records one (Vertex, pinned models), so it may be saved."""
    run_id = new_run_id()
    policy = runs.load_policy("policy_a")
    runs.Runner(store, policy_llm("policy_a"), vertex_settings(), corpus).run_review(run_id, OWNER, policy)
    return run_id, policy.clauses[0].id


def test_owner_sign_off_writes_signoff_and_an_audit_row(
    store: MemoryStore, finished: tuple[str, str]
) -> None:
    run_id, cid = finished
    before = findings(store, run_id)[cid]
    last_seq = max(int(i) for i, _ in docs(store, run_id, "audit"))
    f = runs.sign_off(store, run_id, cid, OWNER, SignoffDecision.EDIT, "Tightened.", "  Officer's wording.  ")
    stored = findings(store, run_id)[cid]
    assert stored == f and stored.signoff is not None
    assert stored.signoff.by == OWNER and stored.signoff.edited_rationale == "Officer's wording."
    assert stored.rationale == before.rationale  # the agent's rationale is kept
    row = AuditEvent.model_validate(store.get(runs.audit_path(run_id, last_seq + 1)))
    assert (row.actor, row.event, row.clause_id) == (f"officer:{OWNER}", "signoff_edit", cid)
    assert "Officer's wording." in row.detail and "Tightened." in row.detail
    # A second decision replaces the sign-off; the audit log keeps both.
    runs.sign_off(store, run_id, cid, OWNER, SignoffDecision.ACCEPT, "", None)
    assert findings(store, run_id)[cid].signoff.decision == SignoffDecision.ACCEPT  # type: ignore[union-attr]
    assert store.get(runs.audit_path(run_id, last_seq + 2)) is not None


@pytest.mark.parametrize(
    ("uid", "decision", "rationale", "error"),
    [
        (OTHER, SignoffDecision.ACCEPT, None, runs.Forbidden),
        (OWNER, SignoffDecision.EDIT, None, runs.Invalid),
        (OWNER, SignoffDecision.EDIT, "   ", runs.Invalid),
        (OWNER, SignoffDecision.REJECT, "text", runs.Invalid),
    ],
)
def test_sign_off_refusals(
    store: MemoryStore,
    finished: tuple[str, str],
    uid: str,
    decision: SignoffDecision,
    rationale: str | None,
    error: type[runs.Rejected],
) -> None:
    run_id, cid = finished
    before = dict(store.docs)
    with pytest.raises(error):
        runs.sign_off(store, run_id, cid, uid, decision, "", rationale)
    assert store.docs == before


def test_sign_off_on_missing_running_or_sample_runs(
    store: MemoryStore, finished: tuple[str, str], corpus: Corpus
) -> None:
    run_id, cid = finished
    with pytest.raises(runs.Missing):
        runs.sign_off(store, run_id, "no-such-clause", OWNER, SignoffDecision.ACCEPT, "", None)
    with pytest.raises(runs.Missing):
        runs.sign_off(store, new_run_id(), cid, OWNER, SignoffDecision.ACCEPT, "", None)
    running = new_run_id()
    runner(store, policy_llm("policy_a"), corpus).admit(running, OWNER, runs.load_policy("policy_a"))
    with pytest.raises(runs.Conflict):
        runs.sign_off(store, running, cid, OWNER, SignoffDecision.ACCEPT, "", None)
    runs.save_sample(store, run_id)
    with pytest.raises(runs.Forbidden, match="read-only"):
        runs.sign_off(store, run_id, cid, OWNER, SignoffDecision.ACCEPT, "", None)


# ---- saved sample runs ------------------------------------------------------------------------------


def test_save_sample_copies_everything_with_saved_true(store: MemoryStore, finished: tuple[str, str]) -> None:
    run_id, _ = finished
    original = Run.model_validate(store.get(runs.run_path(run_id)))
    saved = runs.save_sample(store, run_id)
    assert saved.saved and saved == original.model_copy(update={"saved": True})
    assert Run.model_validate(store.get(runs.run_path(run_id, runs.SAMPLES))) == saved
    for child in ("steps", "findings", "audit"):
        assert docs(store, run_id, child, runs.SAMPLES) == docs(store, run_id, child)
    with pytest.raises(runs.DuplicateRun):
        runs.save_sample(store, run_id)


def test_save_sample_refuses_unfinished_runs(store: MemoryStore, corpus: Corpus) -> None:
    running = new_run_id()
    runs.Runner(store, policy_llm("policy_a"), vertex_settings(), corpus).admit(
        running, OWNER, runs.load_policy("policy_a")
    )
    with pytest.raises(runs.NotASample, match="only a finished"):
        runs.save_sample(store, running)
    assert store.get(runs.run_path(running, runs.SAMPLES)) is None
    with pytest.raises(runs.Missing):
        runs.save_sample(store, new_run_id())


def _pinned_vertex() -> dict[str, Any]:
    agent, monitor = PINNED_MODELS[runs.SAMPLE_PROVIDER]
    return {"agent_model": agent, "monitor_model": monitor}


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        ({"status": RunStatus.FAILED}, "only a finished"),
        ({"seeded": True}, "seed script"),
        ({"provider": "openrouter"}, "provider is 'openrouter'"),
        ({"provider": "aistudio"}, "provider is 'aistudio'"),
        ({"provider": None}, "provider is None"),
        ({"agent_model": PINNED_MODELS["openrouter"][0]}, "pinned"),
        ({"monitor_model": PINNED_MODELS["openrouter"][1]}, "pinned"),
        ({"agent_model": _pinned_vertex()["monitor_model"]}, "pinned"),  # both ids pinned, wrong roles
        ({"rbi_as_on": None}, "rbi_as_on"),
    ],
)
def test_save_sample_refuses_anything_but_a_real_vertex_review(
    store: MemoryStore, finished: tuple[str, str], update: dict[str, Any], reason: str
) -> None:
    run_id, _ = finished
    good = Run.model_validate(store.get(runs.run_path(run_id)))
    assert runs.sample_refusal(good) is None
    store.set(runs.run_path(run_id), good.model_copy(update=update).model_dump(mode="json"))
    with pytest.raises(runs.NotASample, match=reason):
        runs.save_sample(store, run_id)
    assert store.get(runs.run_path(run_id, runs.SAMPLES)) is None
    assert docs(store, run_id, "findings", runs.SAMPLES) == []  # nothing copied before the refusal


def test_save_sample_refuses_a_run_from_the_local_stack(store: MemoryStore, corpus: Corpus) -> None:
    """The real path, not a doctored document: a run reviewed with the local settings is refused."""
    run_id = new_run_id()
    run = runner(store, policy_llm("policy_a"), corpus).run_review(
        run_id, OWNER, runs.load_policy("policy_a")
    )
    assert run.status == RunStatus.DONE and run.provider == settings().provider != runs.SAMPLE_PROVIDER
    with pytest.raises(runs.NotASample, match="provider"):
        runs.save_sample(store, run_id)


# ---- HTTP layer -------------------------------------------------------------------------------------------


@pytest.fixture()
def api(store: MemoryStore, corpus: Corpus, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    origin = "https://argus-test.web.app"
    s = Settings(**{**settings().__dict__, "allowed_origins": (origin,)})
    monkeypatch.setattr(main, "settings", lambda: s)
    monkeypatch.setattr(main, "store", lambda: store)
    monkeypatch.setattr(main, "corpus", lambda: corpus)
    monkeypatch.setattr(main, "llm", lambda: policy_llm("policy_a"))
    main.app.dependency_overrides[main.token_verifier] = lambda: lambda token: {"uid": token}
    with TestClient(main.app) as client:
        yield client
    main.app.dependency_overrides.clear()


def bearer(uid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {uid}"}


def test_endpoint_runs_and_signs_off(api: TestClient, store: MemoryStore) -> None:
    run_id = new_run_id()
    r = api.post("/api/runs", json={"run_id": run_id, "policy_id": "policy_a"}, headers=bearer(OWNER))
    assert r.status_code == 200, r.text
    assert Run.model_validate(r.json()) == Run.model_validate(store.get(runs.run_path(run_id)))
    cid = runs.load_policy("policy_a").clauses[0].id
    url = f"/api/runs/{run_id}/findings/{cid}/signoff"
    assert api.post(url, json={"decision": "accept", "note": ""}, headers=bearer(OTHER)).status_code == 403
    ok = api.post(url, json={"decision": "accept", "note": "Fine."}, headers=bearer(OWNER))
    assert ok.status_code == 200 and Finding.model_validate(ok.json()).signoff is not None
    assert api.post(url, json={"decision": "edit", "note": ""}, headers=bearer(OWNER)).status_code == 422


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"run_id": "not-a-uuid", "policy_id": "policy_a"}, 422),
        ({"run_id": "0" * 32, "policy_id": "policy_a"}, 422),  # hex, but not a uuid4
        ({"run_id": None, "policy_id": "policy_z"}, 404),
        ({"run_id": None, "policy_id": "policy_a", "extra": 1}, 422),
    ],
)
def test_endpoint_validates_requests(api: TestClient, body: dict[str, Any], status: int) -> None:
    body = {**body, "run_id": body["run_id"] or new_run_id()}
    assert api.post("/api/runs", json=body, headers=bearer(OWNER)).status_code == status


def test_endpoint_maps_refusals(api: TestClient, store: MemoryStore) -> None:
    run_id = new_run_id()
    assert (
        api.post(
            "/api/runs", json={"run_id": run_id, "policy_id": "policy_a"}, headers=bearer(OWNER)
        ).status_code
        == 200
    )
    dup = api.post("/api/runs", json={"run_id": run_id, "policy_id": "policy_a"}, headers=bearer(OWNER))
    assert dup.status_code == 409
    store.set(runs.LIMITS_PATH, limits(today_count(store)))
    capped = api.post(
        "/api/runs", json={"run_id": new_run_id(), "policy_id": "policy_a"}, headers=bearer(OWNER)
    )
    assert capped.status_code == 429 and "limit" in capped.json()["detail"]
    store.set(runs.LIMITS_PATH, limits(enabled=False))
    off = api.post("/api/runs", json={"run_id": new_run_id(), "policy_id": "policy_a"}, headers=bearer(OWNER))
    assert off.status_code == 503 and "paused" in off.json()["detail"]
    assert api.post("/api/runs", json={"run_id": new_run_id(), "policy_id": "policy_a"}).status_code == 401


def test_cors_allows_only_configured_origins(api: TestClient) -> None:
    allowed = main.settings().allowed_origins[0]
    pre = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    ok = api.options("/api/runs", headers={"Origin": allowed, **pre})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == allowed
    bad = api.options("/api/runs", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in bad.headers
    assert api.get("/health").json()["status"] == "ok"


def test_startup_fails_loudly_without_the_corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from argus_api.corpus.store import CorpusError

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "corpus", lambda: Corpus.load(tmp_path))
    with pytest.raises(CorpusError, match="not found"), TestClient(main.app):
        pass


# ---- request size, validation echo, smoke admission ------------------------------------------------------


def test_largest_legitimate_sign_off_fits_the_body_limit() -> None:
    worst = " "  # json.dumps escapes it as  : six bytes per character
    body = {
        "decision": "edit",
        "note": worst * main.NOTE_MAX_CHARS,
        "edited_rationale": worst * main.RATIONALE_MAX_CHARS,
    }
    assert len(json.dumps(body).encode()) <= main.MAX_BODY_BYTES


def test_oversized_body_is_413_before_it_is_parsed(api: TestClient, store: MemoryStore) -> None:
    before = dict(store.docs)
    big = json.dumps({"run_id": "a" * (main.MAX_BODY_BYTES + 1), "policy_id": "policy_a"})
    r = api.post("/api/runs", content=big, headers={**bearer(OWNER), "Content-Type": "application/json"})
    assert r.status_code == 413 and len(r.content) < 1000
    assert store.docs == before


def test_oversized_streamed_body_without_length_is_413(api: TestClient) -> None:
    chunk = b"a" * 4096

    def stream() -> Iterator[bytes]:
        for _ in range(main.MAX_BODY_BYTES // len(chunk) + 2):
            yield chunk

    r = api.post("/api/runs", content=stream(), headers={**bearer(OWNER), "Content-Type": "application/json"})
    assert r.status_code == 413


def test_validation_errors_do_not_echo_the_input(api: TestClient) -> None:
    marker = "Z" * 5000
    r = api.post("/api/runs", json={"run_id": marker, "policy_id": "policy_a"}, headers=bearer(OWNER))
    assert r.status_code == 422
    assert marker not in r.text and len(r.content) < 1000
    assert r.json()["detail"][0]["loc"] == ["body", "run_id"]


@pytest.fixture()
def smoke_api(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    llm = FakeLLM(completions=["ready"] * 10)
    monkeypatch.setattr(main, "llm", lambda: llm)
    return llm


def test_smoke_is_admitted_and_counted_like_a_run(
    api: TestClient, store: MemoryStore, smoke_api: FakeLLM
) -> None:
    r = api.post("/api/smoke", headers=bearer(OWNER))
    assert r.status_code == 200, r.text
    record = store.get(f"{runs.SMOKE}/{r.json()['run_id']}")
    assert record is not None and record["status"] == "done" and record["owner_uid"] == OWNER
    assert today_count(store) == 1


def test_smoke_respects_the_kill_switch_and_the_caps(
    api: TestClient, store: MemoryStore, smoke_api: FakeLLM
) -> None:
    store.set(runs.LIMITS_PATH, limits(enabled=False))
    assert api.post("/api/smoke", headers=bearer(OWNER)).status_code == 503
    store.set(runs.LIMITS_PATH, limits(user=1))
    assert api.post("/api/smoke", headers=bearer(OWNER)).status_code == 200
    assert api.post("/api/smoke", headers=bearer(OWNER)).status_code == 429
    assert len(smoke_api.calls) == 1  # refused requests never reach the model


def test_smoke_model_failure_is_saved_and_502(api: TestClient, store: MemoryStore, monkeypatch: Any) -> None:
    from argus_api.llm import LLMError

    monkeypatch.setattr(main, "llm", lambda: FakeLLM(completions=[LLMError("provider down")]))
    r = api.post("/api/smoke", headers=bearer(OWNER))
    assert r.status_code == 502
    smoke = store.list_docs(runs.SMOKE)
    assert len(smoke) == 1 and smoke[0][1]["status"] == "failed" and "provider down" in smoke[0][1]["error"]
