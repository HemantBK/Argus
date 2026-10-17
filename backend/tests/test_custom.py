"""Paste-a-clause offline: custom.check_clause (normalisation, lengths, attestation, identifier prescan), the
custom run through the real agent loop and the HTTP endpoint against the in-memory store, the custom switch
and cap, expires_at on every document, save_sample's refusal, sample runs left as they were, the operator
commands, and that the clause text never reaches a log line or an error answer.

Expected values come from the production constants (custom.MIN_CHARS, runs.CUSTOM_TTL, ...), the sample
policies and the gold findings."""

import logging
import subprocess
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from argus_api import admin, custom, main, purge_custom, runs
from argus_api import save_sample as save_sample_cli
from argus_api.agent.loop import IST
from argus_api.config import Settings
from argus_api.corpus.store import Corpus
from argus_api.logs import JsonLineFormatter
from argus_api.schema import (
    CUSTOM_SOURCE,
    AuditEvent,
    CustomLimits,
    Finding,
    Limits,
    Run,
    RunStatus,
    SignoffDecision,
)
from argus_api.store_fake import MemoryStore
from tests.run_support import (
    ClauseRoutedLLM,
    failing_clause_llm,
    fixture_corpus,
    gold_clause_llm,
    policy_llm,
    settings,
    vertex_settings,
)

OWNER, OTHER = "uid-owner", "uid-other"
CAP = 5
# A real clause from sample policy A (the one the web form starts with): the gold finding scripts its review.
SOURCE_POLICY, SOURCE_CLAUSE = "policy_a", "c2"
TEXT = next(c.text for c in runs.load_policy(SOURCE_POLICY).clauses if c.id == SOURCE_CLAUSE)
FILLER = "The lender shall record the borrower's consent before any disbursal is made; contact: "
CHILDREN = ("steps", "findings", "audit")
BACKEND = Path(__file__).resolve().parents[1]


MISSING: dict[str, Any] = {}  # set_limits(custom=MISSING): no config/custom_limits document


def set_limits(
    store: MemoryStore,
    daily: int = CAP,
    *,
    enabled: bool = True,
    custom: dict[str, Any] | None = None,
    custom_cap: int = CAP,
    user: int | None = None,
) -> None:
    """config/limits, and config/custom_limits switched on with custom_cap unless `custom` gives the stored
    document (MISSING: no document)."""
    limits = Limits(
        daily_run_cap=daily,
        hourly_run_cap=daily,
        per_user_daily_run_cap=daily if user is None else user,
        enabled=enabled,
    )
    store.set(runs.LIMITS_PATH, limits.model_dump())
    doc = custom if custom is not None else {"custom_enabled": True, "custom_daily_cap": custom_cap}
    if doc is MISSING:
        store.docs.pop(runs.CUSTOM_LIMITS_PATH, None)
    else:
        store.set(runs.CUSTOM_LIMITS_PATH, doc)


def new_run_id() -> str:
    return uuid.uuid4().hex


def custom_llm(text: str = TEXT) -> ClauseRoutedLLM:
    policy = custom.custom_policy(custom.check_clause(text, True))
    return ClauseRoutedLLM(policy, {custom.CUSTOM_CLAUSE_ID: gold_clause_llm(SOURCE_POLICY, SOURCE_CLAUSE)})


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return fixture_corpus()


@pytest.fixture()
def store() -> MemoryStore:
    s = MemoryStore()
    set_limits(s)
    return s


def child_docs(store: MemoryStore, run_id: str, root: str = runs.RUNS) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"{child}/{doc_id}", data)
        for child in CHILDREN
        for doc_id, data in store.list_docs(f"{runs.run_path(run_id, root)}/{child}")
    ]


def custom_count(store: MemoryStore, at: datetime) -> int:
    doc = store.get(runs.custom_usage_path(at.astimezone(IST).date().isoformat()))
    return 0 if doc is None else int(doc[runs.USAGE_FIELD])


# ---- normalisation and lengths ---------------------------------------------------------------------------


def test_normalise_is_nfc_without_control_or_format_characters() -> None:
    decomposed = "Café loans"  # e + combining acute: one character after NFC
    assert custom.normalise(decomposed) == "Café loans"
    assert custom.normalise("a\x00b\x07c\x1bd\x7fe") == "abcde"
    assert custom.normalise("line one\r\nline two\rthree\tfour") == "line one\nline two\nthree\tfour"
    assert custom.normalise("PAN​⁠﻿ here") == "PAN here"
    assert custom.normalise("  \n padded \t\n ") == "padded"


@pytest.mark.parametrize(
    ("length", "code"),
    [
        (custom.MIN_CHARS - 1, custom.TOO_SHORT),
        (custom.MIN_CHARS, None),
        (custom.MAX_CHARS, None),
        (custom.MAX_CHARS + 1, custom.TOO_LONG),
    ],
)
def test_length_bounds_come_from_the_constants(length: int, code: str | None) -> None:
    text = "x" * length
    if code is None:
        assert custom.check_clause(text, True) == text
        return
    with pytest.raises(custom.ClauseRejected) as e:
        custom.check_clause(text, True)
    assert e.value.code == code and e.value.status_code == 400
    assert str(custom.MIN_CHARS if code == custom.TOO_SHORT else custom.MAX_CHARS) in e.value.message


def test_length_is_counted_after_normalisation() -> None:
    short = "y" * (custom.MIN_CHARS - 1)
    for padded in (short + "\x00" * 10, short + "​" * 10, " " * 10 + short + "\n" * 10):
        with pytest.raises(custom.ClauseRejected) as e:
            custom.check_clause(padded, True)
        assert e.value.code == custom.TOO_SHORT
    long_ok = "z" * custom.MAX_CHARS
    assert custom.check_clause(long_ok + "\x00" * 50, True) == long_ok
    assert custom.check_clause("é" * custom.MAX_CHARS, True) == "é" * custom.MAX_CHARS
    with pytest.raises(custom.ClauseRejected):
        custom.check_clause(None, True)


@pytest.mark.parametrize("attest", [False, None])
def test_not_attested_comes_first(attest: bool | None) -> None:
    with pytest.raises(custom.ClauseRejected) as e:
        custom.check_clause(FILLER + "ABCDE1234F", attest)
    assert e.value.code == custom.NOT_ATTESTED and e.value.identifier_type is None


# ---- identifier prescan -----------------------------------------------------------------------------------

KINDS = {kind for kind, _ in custom.IDENTIFIERS}

IDENTIFIER_CASES = [
    ("email address", "officer.name+loans@examplebank.co.in", "the grievance@branch desk"),
    ("mobile number", "+91 98765 43210", "98765 4321"),
    ("mobile number", "09876543210", "a 5876-54321 reference"),
    ("mobile number", "98765-43210", "98765-432"),
    ("PAN", "ABCDE1234F", "ABCDE1234"),
    ("PAN", "abcde1234f", "ABCD1234E"),
    ("Aadhaar number", "2345 6789 0123", "2345 6789 012"),
    ("Aadhaar number", "2345-6789-0123", "2345-6789"),
    ("card number", "4242 4242 4242 4242", "4242 4242 4242 424"),
    ("card number", "4242424242424242", "a card ending 4242"),
    ("account number", "123456789", "12345678"),
    ("account number", "123456789012345678", "Rs 10,00,00,000"),
    ("IFSC code", "SBIN0001234", "SBIN1001234"),
]


def test_cases_cover_every_identifier_type() -> None:
    assert {kind for kind, _, _ in IDENTIFIER_CASES} == KINDS


@pytest.mark.parametrize(("kind", "value", "near_miss"), IDENTIFIER_CASES)
def test_identifier_is_found_and_its_near_miss_passes(kind: str, value: str, near_miss: str) -> None:
    with pytest.raises(custom.ClauseRejected) as e:
        custom.check_clause(FILLER + value + ".", True)
    assert (e.value.code, e.value.identifier_type) == (custom.IDENTIFIER_FOUND, kind)
    assert kind in e.value.message
    assert value not in e.value.message and value not in str(e.value.detail())
    passing = FILLER + near_miss + "."
    assert custom.check_clause(passing, True) == passing


def test_hidden_characters_do_not_hide_an_identifier() -> None:
    with pytest.raises(custom.ClauseRejected) as e:
        custom.check_clause(FILLER + "ABCDE​1234⁠F", True)
    assert e.value.identifier_type == "PAN"


@pytest.mark.parametrize("policy_id", runs.POLICY_IDS)
def test_every_sample_clause_passes_the_checks(policy_id: str) -> None:
    """Real policy wording (instrument refs, paragraph numbers, years, amounts) is not an identifier."""
    for c in runs.load_policy(policy_id).clauses:
        if custom.MIN_CHARS <= len(c.text) <= custom.MAX_CHARS:
            assert custom.check_clause(c.text, True) == c.text


def test_custom_policy_is_one_clause() -> None:
    p = custom.custom_policy(TEXT)
    assert p.policy_id == runs.CUSTOM_POLICY_ID and p.custom and p.title == custom.CUSTOM_POLICY_TITLE
    assert [(c.id, c.text) for c in p.clauses] == [(custom.CUSTOM_CLAUSE_ID, TEXT)]


# ---- a custom run, in Python ------------------------------------------------------------------------------


def run_custom(store: MemoryStore, corpus: Corpus, now: datetime, text: str = TEXT, **kw: Any) -> Run:
    policy = custom.custom_policy(custom.check_clause(text, True))
    llm = kw.pop("llm", None) or custom_llm(text)
    r = runs.Runner(store, llm, kw.pop("settings", settings()), corpus, now=lambda: now, **kw)
    return r.run_review(new_run_id(), OWNER, policy)


def test_custom_run_documents_and_expiry(store: MemoryStore, corpus: Corpus) -> None:
    now = datetime(2026, 10, 17, 18, 30, tzinfo=UTC)
    run = run_custom(store, corpus, now)
    assert run.status == RunStatus.DONE
    assert (run.policy_id, run.policy_title, run.source) == (
        runs.CUSTOM_POLICY_ID,
        custom.CUSTOM_POLICY_TITLE,
        CUSTOM_SOURCE,
    )
    assert [(c.clause_id, c.title, c.text) for c in run.clauses] == [
        (custom.CUSTOM_CLAUSE_ID, TEXT[: runs.CUSTOM_TITLE_CHARS], TEXT)
    ]
    expires = now + runs.CUSTOM_TTL
    assert run.expires_at == expires
    # The notice says "deleted within 48 hours": expiry plus Firestore TTL's deletion lag must fit in it.
    assert runs.CUSTOM_TTL + runs.TTL_DELETION_LAG == runs.CUSTOM_DELETED_WITHIN == timedelta(hours=48)

    doc = store.get(runs.run_path(run.run_id))
    assert doc is not None and Run.model_validate(doc) == run
    children = child_docs(store, run.run_id)
    assert {p.split("/")[0] for p, _ in children} == set(CHILDREN)
    # A Firestore TTL policy acts only on a timestamp value, so expires_at is a datetime, never a string.
    for path, data in [("run", doc), *children]:
        assert type(data[runs.EXPIRES_AT]) is datetime and data[runs.EXPIRES_AT] == expires, path
    findings = [Finding.model_validate(d) for p, d in children if p.startswith("findings/")]
    assert [f.clause_text for f in findings] == [TEXT]
    started = AuditEvent.model_validate(store.get(runs.audit_path(run.run_id, 1)))
    assert started.event == "run_started" and started.detail.endswith("1 clause")


def test_custom_run_with_a_failed_clause_still_expires(store: MemoryStore, corpus: Corpus) -> None:
    now = datetime.now(UTC)
    policy = custom.custom_policy(TEXT)
    llm = ClauseRoutedLLM(policy, {custom.CUSTOM_CLAUSE_ID: failing_clause_llm()})
    run = run_custom(store, corpus, now, llm=llm)
    assert run.status == RunStatus.FAILED and run.summary.failed == 1
    for _, data in [("run", store.get(runs.run_path(run.run_id)) or {}), *child_docs(store, run.run_id)]:
        assert data[runs.EXPIRES_AT] == now + runs.CUSTOM_TTL


def test_internal_error_keeps_expires_at_on_the_run(store: MemoryStore, corpus: Corpus) -> None:
    now = datetime.now(UTC)
    store.fail_on = lambda path: "/findings/" in path
    with pytest.raises(Exception, match="scripted failure"):
        run_custom(store, corpus, now)
    run_id = next(p for p in store.docs if p.startswith(f"{runs.RUNS}/")).split("/")[1]
    doc = store.get(runs.run_path(run_id))
    assert doc is not None and doc["status"] == RunStatus.FAILED.value
    assert doc[runs.EXPIRES_AT] == now + runs.CUSTOM_TTL


def test_sign_off_audit_row_on_a_custom_run_expires(store: MemoryStore, corpus: Corpus) -> None:
    now = datetime.now(UTC)
    run = run_custom(store, corpus, now)
    before = {p for p, _ in child_docs(store, run.run_id)}
    runs.sign_off(store, run.run_id, custom.CUSTOM_CLAUSE_ID, OWNER, SignoffDecision.ACCEPT, "Fine.", None)
    new = [(p, d) for p, d in child_docs(store, run.run_id) if p not in before]
    assert [p.split("/")[0] for p, _ in new] == ["audit"]
    assert all(d[runs.EXPIRES_AT] == now + runs.CUSTOM_TTL for _, d in child_docs(store, run.run_id))


def test_custom_runs_cannot_bypass_the_caps(store: MemoryStore, corpus: Corpus) -> None:
    r = runs.Runner(store, custom_llm(), settings(), corpus)
    with pytest.raises(ValueError, match="caps"):
        r.admit(new_run_id(), OWNER, custom.custom_policy(TEXT), enforce_cap=False)
    assert not [p for p in store.docs if p.startswith(f"{runs.RUNS}/")]


def test_run_model_ties_expires_at_to_custom() -> None:
    base = Run.model_validate(
        {
            "run_id": new_run_id(),
            "owner_uid": OWNER,
            "policy_id": runs.CUSTOM_POLICY_ID,
            "policy_title": custom.CUSTOM_POLICY_TITLE,
            "status": "running",
            "started_at": datetime.now(UTC),
            "agent_model": "a",
            "monitor_model": "m",
            "corpus_as_on": "2026-10-16",
        }
    ).model_dump()
    with pytest.raises(ValueError, match="expires_at"):
        Run.model_validate({**base, "source": CUSTOM_SOURCE})
    with pytest.raises(ValueError, match="expires_at"):
        Run.model_validate({**base, "expires_at": datetime.now(UTC)})


# ---- save_sample ------------------------------------------------------------------------------------------


def test_save_sample_refuses_a_custom_run(store: MemoryStore, corpus: Corpus) -> None:
    """Recorded the way the deployed app records a sample (Vertex, pinned models, done): still refused."""
    run = run_custom(store, corpus, datetime.now(UTC), settings=vertex_settings())
    assert run.status == RunStatus.DONE
    assert runs.sample_refusal(run.model_copy(update={"source": None, "expires_at": None})) is None
    with pytest.raises(runs.NotASample, match="source custom"):
        runs.save_sample(store, run.run_id)
    assert store.get(runs.run_path(run.run_id, runs.SAMPLES)) is None
    assert child_docs(store, run.run_id, runs.SAMPLES) == []


def test_save_sample_command_refuses_a_custom_run(
    store: MemoryStore, corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_custom(store, corpus, datetime.now(UTC), settings=vertex_settings())
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr("argus_api.save_sample.firestore.Client", lambda project: object())
    monkeypatch.setattr(save_sample_cli, "FirestoreStore", lambda client: store)
    with pytest.raises(SystemExit, match="save-sample refused: .*source custom"):
        save_sample_cli.main([run.run_id])
    assert store.get(runs.run_path(run.run_id, runs.SAMPLES)) is None


# ---- HTTP -------------------------------------------------------------------------------------------------


@pytest.fixture()
def llm_factory() -> dict[str, Callable[[], Any]]:
    return {"make": custom_llm}


@pytest.fixture()
def api(
    store: MemoryStore,
    corpus: Corpus,
    monkeypatch: pytest.MonkeyPatch,
    llm_factory: dict[str, Callable[[], Any]],
) -> Iterator[TestClient]:
    s = Settings(**{**settings().__dict__, "allowed_origins": ("https://argus-test.web.app",)})
    monkeypatch.setattr(main, "settings", lambda: s)
    monkeypatch.setattr(main, "store", lambda: store)
    monkeypatch.setattr(main, "corpus", lambda: corpus)
    monkeypatch.setattr(main, "llm", lambda: llm_factory["make"]())
    main.app.dependency_overrides[main.token_verifier] = lambda: lambda token: {"uid": token}
    with TestClient(main.app) as client:
        yield client
    main.app.dependency_overrides.clear()


def bearer(uid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {uid}"}


def post_custom(api: TestClient, text: Any = TEXT, attest: Any = True, uid: str = OWNER, **extra: Any) -> Any:
    body = {"run_id": new_run_id(), "policy_id": runs.CUSTOM_POLICY_ID, "clause_text": text, "attest": attest}
    return api.post("/api/runs", json={**body, **extra}, headers=bearer(uid))


def run_writes(store: MemoryStore) -> list[str]:
    return [p for p in store.docs if p.startswith(f"{runs.RUNS}/") or p.startswith(runs.USAGE_COLLECTION)]


def test_endpoint_runs_a_pasted_clause(api: TestClient, store: MemoryStore) -> None:
    r = post_custom(api)
    assert r.status_code == 200, r.text
    body = r.json()
    run = Run.model_validate(body)
    assert run.source == CUSTOM_SOURCE and run.expires_at == run.started_at + runs.CUSTOM_TTL
    assert Run.model_validate(store.get(runs.run_path(run.run_id))) == run
    assert datetime.fromisoformat(body[runs.EXPIRES_AT]) == run.expires_at
    assert custom_count(store, run.started_at) == 1
    url = f"/api/runs/{run.run_id}/findings/{custom.CUSTOM_CLAUSE_ID}/signoff"
    assert api.post(url, json={"decision": "accept", "note": ""}, headers=bearer(OTHER)).status_code == 403
    assert api.post(url, json={"decision": "accept", "note": ""}, headers=bearer(OWNER)).status_code == 200


def test_endpoint_normalises_before_review(api: TestClient, store: MemoryStore) -> None:
    r = post_custom(api, text="​  " + TEXT.replace(" ", " \x00", 3) + "\r\n")
    assert r.status_code == 200, r.text
    assert [c["text"] for c in r.json()["clauses"]] == [TEXT]


@pytest.mark.parametrize(
    ("text", "attest", "code"),
    [
        (TEXT, False, custom.NOT_ATTESTED),
        (TEXT, None, custom.NOT_ATTESTED),
        ("w" * (custom.MIN_CHARS - 1), True, custom.TOO_SHORT),
        (None, True, custom.TOO_SHORT),
        ("w" * (custom.MAX_CHARS + 1), True, custom.TOO_LONG),
        (FILLER + "ABCDE1234F", True, custom.IDENTIFIER_FOUND),
    ],
)
def test_endpoint_refusal_codes_never_echo_the_text(
    api: TestClient, store: MemoryStore, text: str | None, attest: bool | None, code: str
) -> None:
    body: dict[str, Any] = {"run_id": new_run_id(), "policy_id": runs.CUSTOM_POLICY_ID}
    if text is not None:
        body["clause_text"] = text
    if attest is not None:
        body["attest"] = attest
    r = api.post("/api/runs", json=body, headers=bearer(OWNER))
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == code and detail["message"]
    assert set(detail) == (
        {"code", "message", "identifier_type"}
        if code == custom.IDENTIFIER_FOUND
        else {
            "code",
            "message",
        }
    )
    if code == custom.IDENTIFIER_FOUND:
        assert detail["identifier_type"] == "PAN" and "ABCDE1234F" not in r.text
    if text:
        assert text not in r.text and text[:30] not in r.text
    assert run_writes(store) == []


@pytest.mark.parametrize(
    "stored",
    [
        MISSING,  # no config/custom_limits: off
        {"custom_daily_cap": CAP},  # cap but no switch: malformed, off
        {"custom_enabled": True},  # switch but no cap: malformed, off
        {"custom_enabled": True, "custom_daily_cap": CAP, "surprise": 1},  # unknown field: malformed, off
        {"custom_enabled": "true", "custom_daily_cap": CAP},  # not a real boolean: malformed, off
        {"custom_enabled": False, "custom_daily_cap": CAP},
    ],
)
def test_custom_switch_missing_malformed_or_false_is_503(
    api: TestClient, store: MemoryStore, stored: dict[str, Any]
) -> None:
    set_limits(store, custom=stored)
    r = post_custom(api)
    assert r.status_code == 503 and r.json() == {"detail": runs.CUSTOM_OFF}
    assert run_writes(store) == []
    # Sample runs are not affected by the custom switch.
    sample = {"run_id": new_run_id(), "policy_id": SOURCE_POLICY}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(main, "llm", lambda: policy_llm(SOURCE_POLICY))
        assert api.post("/api/runs", json=sample, headers=bearer(OWNER)).status_code == 200


def test_global_kill_switch_wins(api: TestClient, store: MemoryStore) -> None:
    set_limits(store, enabled=False)
    r = post_custom(api)
    assert r.status_code == 503 and r.json() == {"detail": runs.RUNS_PAUSED}


def test_custom_daily_cap_is_429_and_counts_only_custom_runs(api: TestClient, store: MemoryStore) -> None:
    set_limits(store, daily=10, custom_cap=1)
    first = post_custom(api)
    assert first.status_code == 200, first.text
    at = Run.model_validate(first.json()).started_at
    assert custom_count(store, at) == 1
    capped = post_custom(api)
    assert capped.status_code == 429
    assert "1 custom clause reviews" in capped.json()["detail"]
    assert custom_count(store, at) == 1
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(main, "llm", lambda: policy_llm(SOURCE_POLICY))
        sample = {"run_id": new_run_id(), "policy_id": SOURCE_POLICY}
        assert api.post("/api/runs", json=sample, headers=bearer(OWNER)).status_code == 200
    assert custom_count(store, at) == 1


def test_per_user_and_global_caps_apply_to_custom_runs(api: TestClient, store: MemoryStore) -> None:
    set_limits(store, daily=10, user=1, custom_cap=10)
    assert post_custom(api).status_code == 200
    mine = post_custom(api)
    assert mine.status_code == 429 and "your 1 live runs" in mine.json()["detail"]
    assert post_custom(api, uid=OTHER).status_code == 200
    set_limits(store, daily=2, custom_cap=10)
    full = post_custom(api, uid="uid-third")
    assert full.status_code == 429 and "Today's limit of 2 live runs" in full.json()["detail"]


@pytest.mark.parametrize(
    "extra",
    [{"clause_text": TEXT}, {"attest": True}, {"clause_text": TEXT, "attest": True}],
)
def test_sample_runs_refuse_custom_fields(api: TestClient, store: MemoryStore, extra: dict[str, Any]) -> None:
    body = {"run_id": new_run_id(), "policy_id": SOURCE_POLICY, **extra}
    r = api.post("/api/runs", json=body, headers=bearer(OWNER))
    assert r.status_code == 422 and TEXT not in r.text
    assert run_writes(store) == []


@pytest.mark.parametrize("attest", ["true", 1, "yes"])
def test_attest_must_be_a_real_boolean(api: TestClient, store: MemoryStore, attest: Any) -> None:
    r = post_custom(api, attest=attest)
    assert r.status_code == 422 and TEXT not in r.text
    assert run_writes(store) == []


def test_sample_runs_are_unchanged(api: TestClient, store: MemoryStore) -> None:
    """A sample run's request, response and documents carry none of the custom-only fields."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(main, "llm", lambda: policy_llm(SOURCE_POLICY))
        r = api.post(
            "/api/runs", json={"run_id": new_run_id(), "policy_id": SOURCE_POLICY}, headers=bearer(OWNER)
        )
    assert r.status_code == 200, r.text
    expected_fields = set(Run.model_fields) - set(runs.CUSTOM_ONLY_FIELDS)
    assert set(r.json()) == expected_fields
    run = Run.model_validate(r.json())
    assert run.source is None and run.expires_at is None
    doc = store.get(runs.run_path(run.run_id))
    assert doc is not None and set(doc) == expected_fields
    assert all(runs.EXPIRES_AT not in d for _, d in child_docs(store, run.run_id))
    titles = [c.title for c in run.clauses]
    assert titles == [runs.clause_title(c.text) for c in runs.load_policy(SOURCE_POLICY).clauses]
    assert custom_count(store, run.started_at) == 0


# ---- the clause text never reaches a log line --------------------------------------------------------------


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []
        self._fmt = JsonLineFormatter()

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self._fmt.format(record))
        self.lines.append(repr(record.__dict__))


@pytest.fixture()
def captured() -> Iterator[_Capture]:
    """Every record at any level, from the "argus" tree (which does not propagate) and from the root."""
    handler = _Capture()
    root, ours = logging.getLogger(), logging.getLogger("argus")
    levels = root.level, ours.level
    root.setLevel(logging.DEBUG)
    ours.setLevel(logging.DEBUG)
    root.addHandler(handler)
    ours.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        ours.removeHandler(handler)
        root.setLevel(levels[0])
        ours.setLevel(levels[1])


LEAK_WINDOW = 20  # any run of this many consecutive characters of the clause counts as a leak


def _fragments(text: str) -> set[str]:
    """Every LEAK_WINDOW-character window of the text, so a log line holding any part of it is caught."""
    return {text[i : i + LEAK_WINDOW] for i in range(max(1, len(text) - LEAK_WINDOW + 1))}


def assert_absent(text: str, haystacks: list[str]) -> None:
    for frag in _fragments(text):
        for line in haystacks:
            assert frag not in line, f"clause text {frag!r} found in log output"


def test_logs_never_hold_the_clause_text(
    api: TestClient,
    store: MemoryStore,
    captured: _Capture,
    capfd: pytest.CaptureFixture[str],
    llm_factory: dict[str, Callable[[], Any]],
) -> None:
    ok = post_custom(api)
    assert ok.status_code == 200
    policy = custom.custom_policy(TEXT)
    llm_factory["make"] = lambda: ClauseRoutedLLM(
        policy, {custom.CUSTOM_CLAUSE_ID: failing_clause_llm("provider returned HTTP 500 three times")}
    )
    failed = post_custom(api)
    assert failed.status_code == 200 and failed.json()["status"] == RunStatus.FAILED.value
    secret = FILLER + "ABCDE1234F and more words here"
    assert post_custom(api, text=secret).status_code == 400
    assert post_custom(api, text="v" * (custom.MAX_CHARS + 1)).status_code == 400
    set_limits(store, custom={"custom_enabled": False, "custom_daily_cap": CAP})
    assert post_custom(api).status_code == 503
    set_limits(store)
    store.fail_on = lambda path: path.startswith(f"{runs.RUNS}/")
    llm_factory["make"] = custom_llm
    assert post_custom(api).status_code == 503  # Firestore down: logged as a store error

    assert any('"agent_call"' in line for line in captured.lines)  # the review's own logging was captured
    assert any("store error" in line for line in captured.lines)
    out, err = capfd.readouterr()
    haystacks = [*captured.lines, out, err]
    assert_absent(TEXT, haystacks)
    assert_absent(secret, haystacks)
    assert all("ABCDE1234F" not in h for h in haystacks)


# ---- operator commands ------------------------------------------------------------------------------------


@pytest.fixture()
def admin_store(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    s = MemoryStore()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(admin, "_client", lambda: object())
    monkeypatch.setattr(admin, "FirestoreStore", lambda client: s)
    return s


def test_admin_custom_switches_only_config_custom_limits(
    admin_store: MemoryStore, capsys: pytest.CaptureFixture[str]
) -> None:
    assert admin.main(["custom"]) == 0
    assert f"{runs.CUSTOM_LIMITS_PATH}: missing, so custom runs are off" in capsys.readouterr().out
    assert (
        admin.main(["limits", "--cap", "60", "--hourly-cap", "20", "--user-cap", "5", "--enabled", "true"])
        == 0
    )
    base = admin_store.get(runs.LIMITS_PATH)
    assert base is not None and set(base) == set(Limits.model_fields)

    admin.main(["custom", "--enabled", "true", "--cap", "20"])
    assert admin_store.get(runs.CUSTOM_LIMITS_PATH) == {"custom_enabled": True, "custom_daily_cap": 20}
    admin.main(["custom", "--enabled", "false"])  # off keeps the cap
    assert admin_store.get(runs.CUSTOM_LIMITS_PATH) == {"custom_enabled": False, "custom_daily_cap": 20}
    # config/limits is never touched, so a revision that validates it strictly (extra="forbid") keeps working.
    assert admin_store.get(runs.LIMITS_PATH) == base
    admin.main(["limits", "--cap", "40", "--hourly-cap", "10", "--user-cap", "3", "--enabled", "true"])
    assert admin_store.get(runs.CUSTOM_LIMITS_PATH) == {"custom_enabled": False, "custom_daily_cap": 20}


def test_admin_custom_off_works_before_any_cap_and_on_needs_a_cap(admin_store: MemoryStore) -> None:
    admin.main(["custom", "--enabled", "false"])
    assert admin_store.get(runs.CUSTOM_LIMITS_PATH) == {"custom_enabled": False, "custom_daily_cap": 0}
    for argv in (["custom", "--enabled", "true"], ["custom", "--cap", "5"]):
        with pytest.raises(SystemExit):
            admin.main(argv)
    assert admin_store.get(runs.CUSTOM_LIMITS_PATH) == {"custom_enabled": False, "custom_daily_cap": 0}
    assert admin_store.get(runs.LIMITS_PATH) is None


def test_custom_switch_lives_outside_config_limits() -> None:
    """Every revision validates config/limits with extra="forbid" and refuses all runs on a field it does not
    know, so the custom switch must never be a config/limits field."""
    assert Limits.model_config.get("extra") == "forbid"
    assert not set(CustomLimits.model_fields) & set(Limits.model_fields)
    assert runs.CUSTOM_LIMITS_PATH != runs.LIMITS_PATH


def test_a_custom_run_leaves_config_limits_as_it_was(store: MemoryStore, corpus: Corpus) -> None:
    before = store.get(runs.LIMITS_PATH)
    run_custom(store, corpus, datetime.now(UTC))
    assert store.get(runs.LIMITS_PATH) == before
    assert [w.path for w in store.writes].count(runs.LIMITS_PATH) == 1  # the fixture's own write only


def test_admin_ttl_prints_a_policy_for_every_collection_group_a_custom_run_writes(
    store: MemoryStore, corpus: Corpus, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_custom(store, corpus, datetime.now(UTC))
    written = {
        part
        for path in store.docs
        if path.startswith(runs.run_path(run.run_id))
        for i, part in enumerate(path.split("/"))
        if i % 2 == 0
    }
    assert written == set(admin.TTL_COLLECTION_GROUPS)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "argus-test")
    monkeypatch.setattr(admin, "_client", lambda: pytest.fail("ttl must not touch Firestore"))
    assert admin.main(["ttl"]) == 0
    out = capsys.readouterr().out
    for group in admin.TTL_COLLECTION_GROUPS:
        assert (
            f"gcloud firestore fields ttls update {runs.EXPIRES_AT} --collection-group={group} --enable-ttl"
            in out
        )
    assert out.count("--project=argus-test") == len(admin.TTL_COLLECTION_GROUPS) + 1


# ---- purge_custom: deletion of expired pasted-clause runs ------------------------------------------------


class MemoryPurgeTarget:
    """purge_custom.PurgeTarget over a MemoryStore, where a run id can outlive its run document (as in
    Firestore, when TTL deletes the run before its children)."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def run_ids(self) -> list[str]:
        return sorted({p.split("/")[1] for p in self.store.docs if p.startswith(f"{runs.RUNS}/")})

    def get(self, path: str) -> dict[str, Any] | None:
        return self.store.get(path)

    def list_docs(self, collection_path: str) -> list[tuple[str, dict[str, Any]]]:
        return self.store.list_docs(collection_path)

    def delete(self, paths: list[str]) -> None:
        for path in paths:
            del self.store.docs[path]


def run_paths(store: MemoryStore, run_id: str) -> set[str]:
    base = runs.run_path(run_id)
    return {p for p in store.docs if p == base or p.startswith(base + "/")}


def sample_run(store: MemoryStore, corpus: Corpus, now: datetime) -> Run:
    r = runs.Runner(store, policy_llm(SOURCE_POLICY), settings(), corpus, now=lambda: now)
    return r.run_review(new_run_id(), OWNER, runs.load_policy(SOURCE_POLICY))


def test_purge_deletes_expired_custom_runs_and_nothing_else(store: MemoryStore, corpus: Corpus) -> None:
    started = datetime(2026, 10, 17, 18, 30, tzinfo=UTC)
    set_limits(store, daily=10)
    old = run_custom(store, corpus, started)
    fresh = run_custom(store, corpus, started + timedelta(hours=1))
    sample = sample_run(store, corpus, started - timedelta(days=3))
    old_paths = run_paths(store, old.run_id)
    assert len(old_paths) > 1  # the run and its steps, findings and audit
    target = MemoryPurgeTarget(store)

    early = purge_custom.purge_expired(target, started + runs.CUSTOM_TTL - timedelta(seconds=1))
    assert (early.runs, early.orphans, early.documents) == ([], [], 0)

    untouched = {p: d for p, d in store.docs.items() if p not in old_paths}
    report = purge_custom.purge_expired(target, started + runs.CUSTOM_TTL)
    assert (report.runs, report.orphans, report.documents) == ([old.run_id], [], len(old_paths))
    assert run_paths(store, old.run_id) == set()
    assert store.docs == untouched  # the newer custom run, the sample run, limits and usage counters
    assert run_paths(store, fresh.run_id) and run_paths(store, sample.run_id)


def test_purge_deletes_children_left_after_ttl_removed_the_run(store: MemoryStore, corpus: Corpus) -> None:
    started = datetime(2026, 10, 17, 18, 30, tzinfo=UTC)
    run = run_custom(store, corpus, started)
    del store.docs[runs.run_path(run.run_id)]  # TTL deleted the run document first
    children = run_paths(store, run.run_id)
    assert children
    report = purge_custom.purge_expired(MemoryPurgeTarget(store), started + runs.CUSTOM_TTL)
    assert (report.runs, report.orphans, report.documents) == ([], [run.run_id], len(children))
    assert run_paths(store, run.run_id) == set()


def test_purge_dry_run_deletes_nothing(store: MemoryStore, corpus: Corpus) -> None:
    started = datetime(2026, 10, 17, 18, 30, tzinfo=UTC)
    run = run_custom(store, corpus, started)
    before = dict(store.docs)
    report = purge_custom.purge_expired(MemoryPurgeTarget(store), started + runs.CUSTOM_TTL, dry_run=True)
    assert report.runs == [run.run_id] and report.documents == len(run_paths(store, run.run_id))
    assert store.docs == before


def test_purge_command_prints_paths_and_counts_never_the_clause(
    store: MemoryStore, corpus: Corpus, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    started = datetime(2026, 10, 17, 18, 30, tzinfo=UTC)
    run = run_custom(store, corpus, started)
    n = len(run_paths(store, run.run_id))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(purge_custom, "_client", lambda: object())
    monkeypatch.setattr(purge_custom, "FirestorePurgeTarget", lambda client: MemoryPurgeTarget(store))
    assert purge_custom.main([], now=started + runs.CUSTOM_TTL) == 0
    out = capsys.readouterr().out
    assert f"deleted {n} document(s)" in out and runs.run_path(run.run_id) in out
    assert_absent(TEXT, [out])
    assert run_paths(store, run.run_id) == set()


# ---- the plan's boundary: no change to the agent or the monitor -------------------------------------------


def test_agent_and_monitor_code_is_untouched() -> None:
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=BACKEND, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    paths = [str(BACKEND / "argus_api" / d) for d in ("agent", "monitor")]
    diff = subprocess.run(
        ["git", "diff", "--stat", "HEAD", "--", *paths], cwd=top, capture_output=True, text=True, check=True
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *paths],
        cwd=top,
        capture_output=True,
        text=True,
        check=True,
    )
    assert diff.stdout == "" and untracked.stdout == ""
