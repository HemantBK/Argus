"""Firebase Auth and Firestore rules against the Firebase Local Emulator Suite (`make up`, then
`make test-emulator`). These tests need the emulators and fail, never skip, when they are unreachable.

What they prove:
- an anonymous Firebase Auth ID token is accepted by the API and its uid becomes the record's owner_uid;
- a forged, unsigned-but-wrong-project or missing token is refused with 401;
- firestore.rules: a run is readable only by its owner, samples and eval results are public, the RBI library
  and everything else is closed, and no client can write anywhere.
"""

import base64
import json
import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from argus_api import main
from argus_api.auth import uid_from_token
from argus_api.config import AUTH_EMULATOR_ENV
from argus_api.llm import Completion

PROJECT = "argus-local"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must point at the running emulator (see `make up`)")
    return value


FIRESTORE = f"http://{_env('FIRESTORE_EMULATOR_HOST')}"
AUTH = f"http://{_env(AUTH_EMULATOR_ENV)}"
DOCS = f"{FIRESTORE}/v1/projects/{PROJECT}/databases/(default)/documents"
ADMIN = {"Authorization": "Bearer owner"}  # the emulator's admin bypass, standing in for the API's Admin SDK


def _sign_in_anonymously() -> tuple[str, str]:
    r = httpx.post(
        f"{AUTH}/identitytoolkit.googleapis.com/v1/accounts:signUp?key=emulator",
        json={"returnSecureToken": True},
    )
    r.raise_for_status()
    body = r.json()
    return body["localId"], body["idToken"]


def _bearer(token: str | None) -> dict[str, str]:
    return {} if token is None else {"Authorization": f"Bearer {token}"}


def _fields(data: dict[str, str]) -> dict[str, Any]:
    return {"fields": {k: {"stringValue": v} for k, v in data.items()}}


def _admin_set(path: str, data: dict[str, str]) -> None:
    httpx.patch(f"{DOCS}/{path}", headers=ADMIN, json=_fields(data)).raise_for_status()


def _get(path: str, token: str | None) -> int:
    return httpx.get(f"{DOCS}/{path}", headers=_bearer(token)).status_code


def _write(path: str, token: str | None) -> int:
    return httpx.patch(f"{DOCS}/{path}", headers=_bearer(token), json=_fields({"x": "y"})).status_code


def _list_runs(token: str, owner_filter: str | None) -> int:
    query: dict[str, Any] = {"from": [{"collectionId": "runs"}]}
    if owner_filter is not None:
        query["where"] = {
            "fieldFilter": {
                "field": {"fieldPath": "owner_uid"},
                "op": "EQUAL",
                "value": {"stringValue": owner_filter},
            }
        }
    r = httpx.post(f"{DOCS}:runQuery", headers=_bearer(token), json={"structuredQuery": query})
    return r.status_code


@pytest.fixture(scope="module")
def users() -> tuple[tuple[str, str], tuple[str, str]]:
    return _sign_in_anonymously(), _sign_in_anonymously()


@pytest.fixture(scope="module")
def seeded(users: tuple[tuple[str, str], tuple[str, str]]) -> dict[str, str]:
    (owner_uid, _), _ = users
    ids = {"run": f"test-run-{uuid.uuid4().hex}", "sample": f"test-sample-{uuid.uuid4().hex}"}
    _admin_set(f"runs/{ids['run']}", {"owner_uid": owner_uid})
    _admin_set(f"runs/{ids['run']}/steps/0001", {"kind": "agent"})
    _admin_set(f"samples/{ids['sample']}", {"owner_uid": "sample"})
    _admin_set(f"samples/{ids['sample']}/steps/0001", {"kind": "agent"})
    _admin_set("eval_results/latest", {"status": "not measured yet"})
    _admin_set("rbi_paragraphs/test-para", {"text": "library text"})
    return ids


# ---- Firestore rules ------------------------------------------------------------------------------------


def test_owner_reads_own_run_and_its_steps(users: Any, seeded: dict[str, str]) -> None:
    (_, owner_token), _ = users
    assert _get(f"runs/{seeded['run']}", owner_token) == 200
    assert _get(f"runs/{seeded['run']}/steps/0001", owner_token) == 200


def test_other_user_and_anonymous_caller_cannot_read_a_run(users: Any, seeded: dict[str, str]) -> None:
    _, (_, other_token) = users
    for token in (other_token, None):
        assert _get(f"runs/{seeded['run']}", token) == 403
        assert _get(f"runs/{seeded['run']}/steps/0001", token) == 403


def test_run_queries_must_be_scoped_to_the_caller(users: Any, seeded: dict[str, str]) -> None:
    (owner_uid, owner_token), _ = users
    assert _list_runs(owner_token, owner_uid) == 200
    assert _list_runs(owner_token, None) == 403


def test_samples_and_eval_results_are_public(seeded: dict[str, str]) -> None:
    assert _get(f"samples/{seeded['sample']}", None) == 200
    assert _get(f"samples/{seeded['sample']}/steps/0001", None) == 200
    assert _get("eval_results/latest", None) == 200


def test_library_is_not_readable_by_clients(users: Any, seeded: dict[str, str]) -> None:
    (_, owner_token), _ = users
    assert _get("rbi_paragraphs/test-para", owner_token) == 403


def test_no_client_writes_anywhere(users: Any, seeded: dict[str, str]) -> None:
    (_, owner_token), _ = users
    for path in (
        f"runs/{seeded['run']}",
        f"runs/{seeded['run']}/steps/0002",
        f"runs/new-{uuid.uuid4().hex}",
        f"samples/{seeded['sample']}",
        "eval_results/latest",
        "rbi_paragraphs/test-para",
    ):
        for token in (owner_token, None):
            assert _write(path, token) == 403, path


# ---- Firebase Auth on the API ---------------------------------------------------------------------------


class _FakeLLM:
    def complete(self, model: str, prompt: str) -> Completion:
        return Completion(text="ready", model=model, input_tokens=1, output_tokens=1)


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ARGUS_MODE", "local")
    monkeypatch.setenv("ARGUS_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "unused-by-fake-llm")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    monkeypatch.setenv("ARGUS_AGENT_MODEL", "agent-model")
    monkeypatch.setenv("ARGUS_MONITOR_MODEL", "monitor-model")
    for cached in (main.settings, main.db, main._token_verifier):
        cached.cache_clear()
    monkeypatch.setattr(main, "llm", lambda: _FakeLLM())
    yield TestClient(main.app)
    for cached in (main.settings, main.db, main._token_verifier):
        cached.cache_clear()


def test_api_records_the_verified_uid_as_owner(api: TestClient) -> None:
    uid, token = _sign_in_anonymously()
    r = api.post("/api/smoke", headers=_bearer(token))
    assert r.status_code == 200, r.text
    assert r.json()["owner_uid"] == uid
    stored = httpx.get(f"{DOCS}/smoke/{r.json()['run_id']}", headers=ADMIN).json()
    assert stored["fields"]["owner_uid"]["stringValue"] == uid


def _unsigned_token(audience: str) -> str:
    def part(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    now = int(time.time())
    claims = {
        "iss": f"https://securetoken.google.com/{audience}",
        "aud": audience,
        "sub": "intruder",
        "iat": now,
        "exp": now + 3600,
        "auth_time": now,
    }
    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": f"Bearer {_unsigned_token('some-other-project')}"},
    ],
    ids=["missing", "not-bearer", "empty", "garbage", "wrong-project"],
)
def test_api_refuses_requests_without_a_valid_token(api: TestClient, headers: dict[str, str]) -> None:
    assert api.post("/api/smoke", headers=headers).status_code == 401


def test_verifier_returns_the_emulator_uid(api: TestClient) -> None:
    uid, token = _sign_in_anonymously()
    assert uid_from_token(token, main.token_verifier()) == uid
