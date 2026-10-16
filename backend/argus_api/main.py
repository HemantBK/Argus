"""Argus API on Cloud Run. Skeleton: health check and one Firebase Auth + Gemini + Firestore round trip."""

import time
import uuid
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header
from google.cloud import firestore

from argus_api.auth import TokenVerifier, current_uid_with, firebase_verifier
from argus_api.config import Settings
from argus_api.llm import LLM, build_llm

app = FastAPI(title="Argus API")


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=1)
def llm() -> LLM:
    return build_llm(settings())


@lru_cache(maxsize=1)
def db() -> firestore.Client:
    return firestore.Client(project=settings().project)


def token_verifier() -> TokenVerifier:
    return _token_verifier()


@lru_cache(maxsize=1)
def _token_verifier() -> TokenVerifier:
    return firebase_verifier(settings())


def current_uid(
    verify: Annotated[TokenVerifier, Depends(token_verifier)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Firebase Auth: the uid of the signed-in (anonymous) user who sent the request."""
    return current_uid_with(verify, authorization)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/smoke")
def smoke(uid: Annotated[str, Depends(current_uid)]) -> dict[str, Any]:
    """Proves the deployed path works: Firebase Auth -> Cloud Run -> Gemini -> Firestore."""
    s = settings()
    started = time.perf_counter()
    response = llm().complete(s.agent_model, "Reply with the single word: ready")
    latency_ms = round((time.perf_counter() - started) * 1000)
    run_id = uuid.uuid4().hex
    record = {
        "owner_uid": uid,
        "model": s.agent_model,
        "mode": s.mode,
        "provider": s.provider,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "text": response.text,
        "latency_ms": latency_ms,
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    db().collection("smoke").document(run_id).set(record)
    return {
        "run_id": run_id,
        "owner_uid": uid,
        "provider": s.provider,
        "model": response.model,
        "text": response.text,
        "latency_ms": latency_ms,
    }
