"""Argus API on Cloud Run. Skeleton: health check and one end-to-end Gemini + Firestore round trip."""

import time
import uuid
from functools import lru_cache
from typing import Any

from fastapi import FastAPI
from google import genai
from google.cloud import firestore

from argus_api.config import Settings

app = FastAPI(title="Argus API")


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=1)
def gemini() -> genai.Client:
    s = settings()
    return genai.Client(vertexai=True, project=s.project, location=s.location)


@lru_cache(maxsize=1)
def db() -> firestore.Client:
    return firestore.Client(project=settings().project)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/smoke")
def smoke() -> dict[str, Any]:
    """Proves the deployed path works: Cloud Run -> Gemini -> Firestore."""
    s = settings()
    started = time.perf_counter()
    response = gemini().models.generate_content(
        model=s.agent_model, contents="Reply with the single word: ready"
    )
    latency_ms = round((time.perf_counter() - started) * 1000)
    run_id = uuid.uuid4().hex
    record = {
        "model": s.agent_model,
        "text": (response.text or "").strip(),
        "latency_ms": latency_ms,
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    db().collection("smoke").document(run_id).set(record)
    return {"run_id": run_id, "model": s.agent_model, "text": record["text"], "latency_ms": latency_ms}
