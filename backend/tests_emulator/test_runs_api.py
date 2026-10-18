"""The run and sign-off endpoints against the Firebase emulators with real anonymous Auth ID tokens.

The LLM is scripted per clause (tests/run_support.py) and the corpus is the committed fixture; Firestore and
Firebase Auth are the emulators. config/limits is shared emulator state, so it is saved before and restored
after this module. These tests fail, never skip, when the emulators are unreachable.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from argus_api import main, runs
from argus_api.agent.loop import IST
from argus_api.config import AUTH_EMULATOR_ENV, PINNED_MODELS
from argus_api.docstore import FirestoreStore
from argus_api.schema import AuditEvent, Finding, Limits, Run, RunStatus
from tests.run_support import failing_clause_llm, fixture_corpus, policy_llm

PROJECT = "argus-local"
CAP = 1000


def limits(daily: int, enabled: bool = True) -> dict[str, Any]:
    """config/limits with only the daily cap binding (the hourly and per-user caps set to the same value)."""
    return Limits(
        daily_run_cap=daily, hourly_run_cap=daily, per_user_daily_run_cap=daily, enabled=enabled
    ).model_dump()


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must point at the running emulator (see `make up`)")
    return value


FIRESTORE = f"http://{_env('FIRESTORE_EMULATOR_HOST')}"
AUTH = f"http://{_env(AUTH_EMULATOR_ENV)}"
DOCS = f"{FIRESTORE}/v1/projects/{PROJECT}/databases/(default)/documents"


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


@pytest.fixture(scope="module")
def users() -> tuple[tuple[str, str], tuple[str, str]]:
    return sign_in(), sign_in()


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
    monkeypatch.setattr(main, "llm", lambda: policy_llm("policy_a"))
    store = FirestoreStore(main.db())
    saved_limits = store.get(runs.LIMITS_PATH)
    store.set(runs.LIMITS_PATH, limits(CAP))
    try:
        with TestClient(main.app) as client:
            yield client, store
    finally:
        if saved_limits is not None:
            store.set(runs.LIMITS_PATH, saved_limits)
        else:
            main.db().document(runs.LIMITS_PATH).delete()
        for cached in (main.settings, main.db, main._token_verifier):
            cached.cache_clear()


def start(
    client: TestClient, token: str, run_id: str | None = None, policy_id: str = "policy_a"
) -> httpx.Response:
    body = {"run_id": run_id or uuid.uuid4().hex, "policy_id": policy_id}
    return client.post("/api/runs", json=body, headers=bearer(token))


def client_read(path: str, token: str) -> int:
    return httpx.get(f"{DOCS}/{path}", headers=bearer(token)).status_code


def test_owner_runs_a_policy_and_only_the_owner_can_read_it(api: Any, users: Any) -> None:
    client, store = api
    (owner_uid, owner_token), (_, other_token) = users
    r = start(client, owner_token)
    assert r.status_code == 200, r.text
    run = Run.model_validate(r.json())
    assert run.owner_uid == owner_uid and run.status == RunStatus.DONE
    assert Run.model_validate(store.get(runs.run_path(run.run_id))) == run
    steps = store.list_docs(f"{runs.run_path(run.run_id)}/steps")
    assert steps and steps[0][0] == runs.seq_id(1)
    clauses = runs.load_policy("policy_a").clauses
    assert [i for i, _ in store.list_docs(f"{runs.run_path(run.run_id)}/findings")] == sorted(
        c.id for c in clauses
    )
    # firestore.rules on what the API wrote: the owner's listeners see it, nobody else does.
    first_step = f"runs/{run.run_id}/steps/{steps[0][0]}"
    assert client_read(f"runs/{run.run_id}", owner_token) == 200
    assert client_read(first_step, owner_token) == 200
    assert client_read(f"runs/{run.run_id}", other_token) == 403
    assert client_read(first_step, other_token) == 403


def test_duplicate_run_id_is_409(api: Any, users: Any) -> None:
    client, _ = api
    (_, token), (_, other_token) = users
    run_id = uuid.uuid4().hex
    assert start(client, token, run_id).status_code == 200
    assert start(client, token, run_id).status_code == 409
    assert start(client, other_token, run_id).status_code == 409


def test_daily_cap_is_429(api: Any, users: Any) -> None:
    client, store = api
    (_, token), _ = users
    assert start(client, token).status_code == 200
    today = datetime.now(IST).date().isoformat()
    usage = store.get(runs.usage_path(today))
    assert usage is not None
    used = usage[runs.USAGE_FIELD]
    store.set(runs.LIMITS_PATH, limits(used))
    run_id = uuid.uuid4().hex
    r = start(client, token, run_id)
    assert r.status_code == 429 and str(used) in r.json()["detail"]
    assert store.get(runs.run_path(run_id)) is None


def test_kill_switch_is_503(api: Any, users: Any) -> None:
    client, store = api
    (_, token), _ = users
    store.set(runs.LIMITS_PATH, limits(CAP, enabled=False))
    run_id = uuid.uuid4().hex
    r = start(client, token, run_id)
    assert r.status_code == 503 and "paused" in r.json()["detail"]
    assert store.get(runs.run_path(run_id)) is None


def test_sign_off_by_owner_writes_an_audit_row_and_others_are_refused(api: Any, users: Any) -> None:
    client, store = api
    (owner_uid, owner_token), (_, other_token) = users
    run_id = Run.model_validate(start(client, owner_token).json()).run_id
    cid = runs.load_policy("policy_a").clauses[0].id
    url = f"/api/runs/{run_id}/findings/{cid}/signoff"
    body = {"decision": "edit", "note": "Reworded for the board.", "edited_rationale": "Officer text."}
    assert client.post(url, json=body, headers=bearer(other_token)).status_code == 403

    audit_before = store.list_docs(f"{runs.run_path(run_id)}/audit")
    r = client.post(url, json=body, headers=bearer(owner_token))
    assert r.status_code == 200, r.text
    stored = Finding.model_validate(store.get(runs.finding_path(run_id, cid)))
    assert stored.signoff is not None and stored.signoff.by == owner_uid
    assert stored.signoff.edited_rationale == body["edited_rationale"]
    audit_after = store.list_docs(f"{runs.run_path(run_id)}/audit")
    assert len(audit_after) == len(audit_before) + 1
    row = AuditEvent.model_validate(audit_after[-1][1])
    assert audit_after[-1][0] == runs.seq_id(len(audit_after))
    assert (row.actor, row.event, row.clause_id) == (f"officer:{owner_uid}", "signoff_edit", cid)


def test_sign_off_on_a_saved_sample_is_refused(api: Any, users: Any) -> None:
    client, store = api
    (_, owner_token), _ = users
    run = Run.model_validate(start(client, owner_token).json())
    run_id = run.run_id
    # This API runs on the local stack, which save_sample refuses (test_runs.py pins that). Record the run
    # as the deployed app would (Vertex, pinned models): what is tested here is the sample's sign-off.
    with pytest.raises(runs.NotASample, match="provider"):
        runs.save_sample(store, run_id)
    agent, monitor = PINNED_MODELS[runs.SAMPLE_PROVIDER]
    as_vertex = {"provider": runs.SAMPLE_PROVIDER, "agent_model": agent, "monitor_model": monitor}
    store.set(runs.run_path(run_id), run.model_copy(update=as_vertex).model_dump(mode="json"))
    sample = runs.save_sample(store, run_id)
    assert sample.saved
    assert client_read(f"samples/{run_id}", owner_token) == 200  # public read
    cid = runs.load_policy("policy_a").clauses[0].id
    r = client.post(
        f"/api/runs/{run_id}/findings/{cid}/signoff",
        json={"decision": "accept", "note": ""},
        headers=bearer(owner_token),
    )
    assert r.status_code == 403 and "read-only" in r.json()["detail"]


def test_a_clause_whose_llm_call_errors_fails_and_the_run_finishes(
    api: Any, users: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = api
    (_, token), _ = users
    policy = runs.load_policy("policy_b")
    bad = policy.clauses[1].id
    monkeypatch.setattr(main, "llm", lambda: policy_llm("policy_b", [(bad, failing_clause_llm())]))
    r = start(client, token, policy_id="policy_b")
    assert r.status_code == 200, r.text
    run = Run.model_validate(r.json())
    # One failed clause of several: the run is done with the others' findings (runs.py _finish, 18 Oct).
    assert run.status == RunStatus.DONE and run.finished_at is not None
    assert run.summary.failed == 1 and run.error is not None and bad in run.error
    found = {
        i: Finding.model_validate(d) for i, d in store.list_docs(f"{runs.run_path(run.run_id)}/findings")
    }
    assert set(found) == {c.id for c in policy.clauses}
    assert found[bad].error is not None
    assert all(f.error is None for cid, f in found.items() if cid != bad)


def test_per_user_cap_is_429_for_that_user_only(api: Any) -> None:
    client, store = api
    (uid, token), (_, other_token) = sign_in(), sign_in()  # fresh users: nothing counted for them yet
    store.set(
        runs.LIMITS_PATH,
        Limits(daily_run_cap=CAP, hourly_run_cap=CAP, per_user_daily_run_cap=1, enabled=True).model_dump(),
    )
    assert start(client, token).status_code == 200
    run_id = uuid.uuid4().hex
    r = start(client, token, run_id)
    assert r.status_code == 429 and "your" in r.json()["detail"]
    assert store.get(runs.run_path(run_id)) is None
    assert start(client, other_token).status_code == 200
    today = datetime.now(IST).date().isoformat()
    assert store.get(runs.user_usage_path(today, uid)) == {runs.USAGE_FIELD: 1}
