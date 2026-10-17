"""Firebase Auth on the API without the emulator: header parsing and how verifier errors map to HTTP codes.
The end-to-end path with real emulator tokens is in tests_emulator/test_firebase.py."""

from typing import Any

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from firebase_admin import auth

from argus_api import main
from argus_api.auth import BEARER, TokenVerifier, bearer_token, uid_from_token


def _raises(error: Exception) -> TokenVerifier:
    def verify(token: str) -> dict[str, Any]:
        raise error

    return verify


def _returns(claims: dict[str, Any]) -> TokenVerifier:
    return lambda token: claims


@pytest.mark.parametrize("header", [None, "Basic abc", "bearer abc", BEARER, BEARER + "   "])
def test_malformed_authorization_header_is_401(header: str | None) -> None:
    with pytest.raises(HTTPException) as e:
        bearer_token(header)
    assert e.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_bearer_token_is_extracted() -> None:
    assert bearer_token(BEARER + "abc.def.ghi") == "abc.def.ghi"


@pytest.mark.parametrize(
    "error",
    [
        auth.InvalidIdTokenError("bad"),
        auth.ExpiredIdTokenError("expired", cause=None),
        auth.RevokedIdTokenError("revoked"),
        auth.UserDisabledError("disabled"),
    ],
    ids=["invalid", "expired", "revoked", "disabled"],
)
def test_rejected_tokens_are_401(error: Exception) -> None:
    with pytest.raises(HTTPException) as e:
        uid_from_token("t", _raises(error))
    assert e.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_unreachable_signing_keys_fail_closed_with_503() -> None:
    with pytest.raises(HTTPException) as e:
        uid_from_token("t", _raises(auth.CertificateFetchError("down", cause=None)))
    assert e.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_auth_backend_refusals_fail_closed_with_503() -> None:
    """The live failure of 2026-10-17: the revocation lookup was refused for lack of a role."""
    from firebase_admin import _auth_utils

    refused = _auth_utils.InsufficientPermissionError(
        "no firebaseauth.viewer", cause=None, http_response=None
    )
    with pytest.raises(HTTPException) as e:
        uid_from_token("t", _raises(refused))
    assert e.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.parametrize("claims", [{}, {"uid": ""}, {"uid": 7}])
def test_token_without_a_uid_is_401(claims: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as e:
        uid_from_token("t", _returns(claims))
    assert e.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_unexpected_verifier_errors_are_not_swallowed() -> None:
    with pytest.raises(RuntimeError):
        uid_from_token("t", _raises(RuntimeError("boom")))


def test_smoke_endpoint_requires_a_token() -> None:
    main.app.dependency_overrides[main.token_verifier] = lambda: _raises(auth.InvalidIdTokenError("bad"))
    try:
        client = TestClient(main.app)
        assert client.post("/api/smoke").status_code == status.HTTP_401_UNAUTHORIZED
        assert client.post("/api/smoke", headers={"Authorization": BEARER + "x"}).status_code == 401
    finally:
        main.app.dependency_overrides.clear()
