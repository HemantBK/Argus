"""Firebase Auth on the API: every write endpoint needs a Firebase ID token from the web app.

The browser signs in anonymously with Firebase Auth and sends `Authorization: Bearer <ID token>` to Cloud
Run. The uid in the verified token becomes the run's `owner_uid`, which the Firestore rules (firestore.rules)
compare with `request.auth.uid`, so a run is readable only by the person who started it.

Failures are loud and closed: a missing, malformed, expired, revoked or forged token is 401; when Google's
signing keys cannot be fetched the request is refused with 503, never let through.
"""

from collections.abc import Callable
from functools import lru_cache
from typing import Any

import firebase_admin
from fastapi import HTTPException, status
from firebase_admin import auth

from argus_api.config import Settings

BEARER = "Bearer "
FIREBASE_APP_NAME = "argus"

TokenVerifier = Callable[[str], dict[str, Any]]


@lru_cache(maxsize=1)
def _app(project: str) -> firebase_admin.App:
    # Credentials come from Application Default Credentials (the Cloud Run service account); the Auth emulator
    # needs none. projectId pins the expected token audience to this project.
    return firebase_admin.initialize_app(options={"projectId": project}, name=FIREBASE_APP_NAME)


def firebase_verifier(settings: Settings) -> TokenVerifier:
    app = _app(settings.project)

    def verify(token: str) -> dict[str, Any]:
        claims: dict[str, Any] = auth.verify_id_token(token, app=app, check_revoked=settings.mode == "cloud")
        return claims

    return verify


def bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Authorization header")
    if not authorization.startswith(BEARER):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authorization header must be 'Bearer <ID token>'")
    token = authorization[len(BEARER) :].strip()
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Empty bearer token")
    return token


def uid_from_token(token: str, verify: TokenVerifier) -> str:
    try:
        claims = verify(token)
    except (auth.InvalidIdTokenError, auth.UserDisabledError) as e:
        # InvalidIdTokenError covers expired and revoked tokens too.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid Firebase ID token: {e}") from e
    except auth.CertificateFetchError as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"Cannot fetch Firebase signing keys: {e}"
        ) from e
    uid = claims.get("uid")
    if not isinstance(uid, str) or not uid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Firebase ID token has no uid")
    return uid


def current_uid_with(verify: TokenVerifier, authorization: str | None) -> str:
    """The verified caller's uid, or an HTTP 401/503 error."""
    return uid_from_token(bearer_token(authorization), verify)
