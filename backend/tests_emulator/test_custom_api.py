"""Paste-a-clause against the Firebase emulators with real anonymous Auth ID tokens.

A pasted clause runs end to end through POST /api/runs into the Firestore emulator; the stored documents are
read back over the emulator's REST API to prove expires_at is a Firestore timestamp (TTL ignores strings) on
the run and on every steps/findings/audit document, that firestore.rules keep the run private to its owner,
and that the custom switch and cap refuse before anything is written. config/limits is shared emulator
state, so it is saved before and restored after each test. These tests fail, never skip, when the emulators
are unreachable.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from argus_api import custom, main, purge_custom, runs
from argus_api.agent.loop import IST
from argus_api.config import AUTH_EMULATOR_ENV
from argus_api.docstore import FirestoreStore
from argus_api.llm_fake import FakeLLM
from argus_api.schema import CUSTOM_SOURCE, CustomLimits, Limits, Run, RunStatus
from tests.run_support import ClauseRoutedLLM, fixture_corpus, gold_clause_llm

PROJECT = "argus-local"
CAP = 1000
SOURCE_POLICY, SOURCE_CLAUSE = "policy_a", "c2"
TEXT = next(c.text for c in runs.load_policy(SOURCE_POLICY).clauses if c.id == SOURCE_CLAUSE)
CHILDREN = ("steps", "findings", "audit")


LIMITS = Limits(daily_run_cap=CAP, hourly_run_cap=CAP, per_user_daily_run_cap=CAP, enabled=True).model_dump()
CONFIG_PATHS = (runs.LIMITS_PATH, runs.CUSTOM_LIMITS_PATH)


def custom_limits(cap: int = CAP, enabled: bool = True) -> dict[str, Any]:
    return CustomLimits(custom_enabled=enabled, custom_daily_cap=cap).model_dump()


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must point at the running emulator (see `make up`)")
    return value


FIRESTORE = f"http://{_env('FIRESTORE_EMULATOR_HOST')}"
AUTH = f"http://{_env(AUTH_EMULATOR_ENV)}"
DOCS = f"{FIRESTORE}/v1/projects/{PROJECT}/databases/(default)/documents"
ADMIN = {"Authorization": "Bearer owner"}  # the Firestore emulator's rules bypass, for reading raw values


def sign_in() -> tuple[str, str]:
    r = httpx.post(
        f"{AUTH}/identitytoolkit.googleapis.com/v1/accounts:signUp?key=emulator",
        json={"returnSecureToken": True},
    )
    r.raise_for_status()
    body = r.json()
    return body["localId"], body["idToken"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def custom_llm() -> ClauseRoutedLLM:
    policy = custom.custom_policy(TEXT)
    return ClauseRoutedLLM(policy, {custom.CUSTOM_CLAUSE_ID: gold_clause_llm(SOURCE_POLICY, SOURCE_CLAUSE)})


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, FirestoreStore]]:
    monkeypatch.setenv("ARGUS_MODE", "local")
    monkeypatch.setenv("ARGUS_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "unused-by-fake-llm")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    monkeypatch.setenv("ARGUS_AGENT_MODEL", "agent-model")
    monkeypatch.setenv("ARGUS_MONITOR_MODEL", "monitor-model")
    for cached in (main.settings, main.db, main._token_verifier):
        cached.cache_clear()
    corpus = fixture_corpus()
    monkeypatch.setattr(main, "corpus", lambda: corpus)
    monkeypatch.setattr(main, "llm", custom_llm)
    store = FirestoreStore(main.db())
    saved = {path: store.get(path) for path in CONFIG_PATHS}
    store.set(runs.LIMITS_PATH, LIMITS)
    store.set(runs.CUSTOM_LIMITS_PATH, custom_limits())
    try:
        with TestClient(main.app) as client:
            yield client, store
    finally:
        for path, doc in saved.items():
            if doc is not None:
                store.set(path, doc)
            else:
                main.db().document(path).delete()
        for cached in (main.settings, main.db, main._token_verifier):
            cached.cache_clear()


def start(client: TestClient, token: str, text: str = TEXT, run_id: str | None = None) -> httpx.Response:
    body = {
        "run_id": run_id or uuid.uuid4().hex,
        "policy_id": runs.CUSTOM_POLICY_ID,
        "clause_text": text,
        "attest": True,
    }
    return client.post("/api/runs", json=body, headers=bearer(token))


def raw_fields(path: str) -> dict[str, Any]:
    r = httpx.get(f"{DOCS}/{path}", headers=ADMIN)
    r.raise_for_status()
    fields: dict[str, Any] = r.json()["fields"]
    return fields


def child_paths(store: FirestoreStore, run_id: str) -> list[str]:
    return [
        f"{runs.run_path(run_id)}/{child}/{doc_id}"
        for child in CHILDREN
        for doc_id, _ in store.list_docs(f"{runs.run_path(run_id)}/{child}")
    ]


def test_pasted_clause_runs_and_every_document_carries_a_ttl_timestamp(api: Any) -> None:
    client, store = api
    (owner_uid, owner_token), (_, other_token) = sign_in(), sign_in()
    r = start(client, owner_token)
    assert r.status_code == 200, r.text
    run = Run.model_validate(r.json())
    assert run.owner_uid == owner_uid and run.status == RunStatus.DONE and run.source == CUSTOM_SOURCE
    assert run.expires_at == run.started_at + runs.CUSTOM_TTL
    assert [c.text for c in run.clauses] == [TEXT]
    assert Run.model_validate(store.get(runs.run_path(run.run_id))) == run

    paths = child_paths(store, run.run_id)
    assert {p.split("/")[2] for p in paths} == set(CHILDREN)
    for path in [runs.run_path(run.run_id), *paths]:
        value = raw_fields(path)[runs.EXPIRES_AT]
        assert set(value) == {"timestampValue"}, (path, value)
        assert datetime.fromisoformat(value["timestampValue"]) == run.expires_at, path

    # firestore.rules on what the API wrote: the owner's listeners see it, nobody else does.
    first_step = next(p for p in paths if "/steps/" in p)
    for path, token, status in [
        (runs.run_path(run.run_id), owner_token, 200),
        (first_step, owner_token, 200),
        (runs.run_path(run.run_id), other_token, 403),
        (first_step, other_token, 403),
    ]:
        assert httpx.get(f"{DOCS}/{path}", headers=bearer(token)).status_code == status, path

    # The owner's sign-off adds an audit row that expires with the run.
    url = f"/api/runs/{run.run_id}/findings/{custom.CUSTOM_CLAUSE_ID}/signoff"
    assert (
        client.post(url, json={"decision": "accept", "note": ""}, headers=bearer(owner_token)).status_code
        == 200
    )
    last_audit = store.list_docs(f"{runs.run_path(run.run_id)}/audit")[-1][0]
    stamp = raw_fields(f"{runs.run_path(run.run_id)}/audit/{last_audit}")[runs.EXPIRES_AT]
    assert datetime.fromisoformat(stamp["timestampValue"]) == run.expires_at

    with pytest.raises(runs.NotASample, match="source custom"):
        runs.save_sample(store, run.run_id)
    assert store.get(runs.run_path(run.run_id, runs.SAMPLES)) is None


@pytest.mark.parametrize(
    "stored",
    [None, custom_limits(enabled=False), {"custom_enabled": True}],  # missing, off, malformed
)
def test_custom_switch_off_missing_or_malformed_is_503(
    api: Any, stored: dict[str, Any] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = api
    if stored is None:
        main.db().document(runs.CUSTOM_LIMITS_PATH).delete()
    else:
        store.set(runs.CUSTOM_LIMITS_PATH, stored)
    untouched = FakeLLM()
    monkeypatch.setattr(main, "llm", lambda: untouched)
    (_, token) = sign_in()
    run_id = uuid.uuid4().hex
    r = start(client, token, run_id=run_id)
    assert r.status_code == 503 and r.json() == {"detail": runs.CUSTOM_OFF}
    assert store.get(runs.run_path(run_id)) is None and untouched.calls == []


def test_custom_daily_cap_is_429(api: Any) -> None:
    client, store = api
    (_, token) = sign_in()
    assert start(client, token).status_code == 200
    today = datetime.now(IST).date().isoformat()
    usage = store.get(runs.custom_usage_path(today))
    assert usage is not None
    used = usage[runs.USAGE_FIELD]
    store.set(runs.CUSTOM_LIMITS_PATH, custom_limits(cap=used))
    run_id = uuid.uuid4().hex
    r = start(client, token, run_id=run_id)
    assert r.status_code == 429 and f"{used} custom clause reviews" in r.json()["detail"]
    assert store.get(runs.run_path(run_id)) is None
    assert store.get(runs.custom_usage_path(today)) == {runs.USAGE_FIELD: used}


def test_an_identifier_is_refused_before_anything_is_written(api: Any) -> None:
    client, store = api
    (_, token) = sign_in()
    run_id = uuid.uuid4().hex
    text = TEXT + " Borrower PAN ABCDE1234F."
    r = start(client, token, text=text, run_id=run_id)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == custom.IDENTIFIER_FOUND
    assert r.json()["detail"]["identifier_type"] == "PAN"
    assert "ABCDE1234F" not in r.text and TEXT[:40] not in r.text
    assert store.get(runs.run_path(run_id)) is None


def test_purge_deletes_an_expired_custom_run_and_children_whose_run_is_gone(api: Any) -> None:
    """purge_custom against real Firestore: an expired run goes with every child, and children whose run
    document TTL already removed are still found (list_documents lists such missing parents)."""
    client, store = api
    (_, token) = sign_in()
    expired_run = Run.model_validate(start(client, token).json())
    orphaned = Run.model_validate(start(client, token).json())
    kept = Run.model_validate(start(client, token).json())
    db = main.db()
    db.document(runs.run_path(orphaned.run_id)).delete()  # as TTL may do before the children
    target = purge_custom.FirestorePurgeTarget(db)
    assert {expired_run.run_id, orphaned.run_id, kept.run_id} <= set(target.run_ids())

    at = expired_run.expires_at
    assert at is not None and orphaned.expires_at is not None and kept.expires_at is not None
    # Only the three runs above count: other tests share the emulator, so filter the report to them.
    now = max(at, orphaned.expires_at)
    assert now < kept.expires_at
    report = purge_custom.purge_expired(target, now)
    assert expired_run.run_id in report.runs and orphaned.run_id in report.orphans
    assert kept.run_id not in report.runs + report.orphans
    for run in (expired_run, orphaned):
        assert store.get(runs.run_path(run.run_id)) is None and child_paths(store, run.run_id) == []
    assert store.get(runs.run_path(kept.run_id)) is not None and child_paths(store, kept.run_id)
