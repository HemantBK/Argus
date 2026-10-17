"""Argus API on Cloud Run.

  GET  /health                                          liveness and what is deployed: git sha, mode,
                                                        provider, pinned models, location, corpus hash;
                                                        no calls to Firestore, Auth or the model
  POST /api/smoke                                       Firebase Auth -> Gemini -> Firestore round trip; one
                                                        model call, admitted like a run (kill switch, caps)
  POST /api/runs {run_id, policy_id}                    review a sample policy; the work is done inside the
                                                        request and the final Run is returned, while the
                                                        browser follows runs/{run_id} through listeners
  POST /api/runs/{run_id}/findings/{clause_id}/signoff  the run owner's accept / reject / edit

At startup the settings and the RBI library (data/corpus) are loaded; either missing stops the server. CORS
allows only the origins in ARGUS_ALLOWED_ORIGINS. Refusals carry a plain-English `detail` (runs.Rejected).

Request bodies over MAX_BODY_BYTES are refused with 413 before they are read in full, and validation errors
(422) name the field and the problem without echoing the submitted value, so a request can never make the
server hold or send back more than a few kilobytes. Logging is configured at import (logs.configure_logging),
so every AI call reaches Cloud Logging as a JSON line.
"""

import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from google.cloud import firestore
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from argus_api import runs
from argus_api.auth import TokenVerifier, current_uid_with, firebase_verifier
from argus_api.config import Settings
from argus_api.corpus.common import DEFAULT_OUT_DIR
from argus_api.corpus.store import Corpus
from argus_api.docstore import Busy, DocumentStore, FirestoreStore, StoreError
from argus_api.llm import LLM, LLMError, build_llm
from argus_api.logs import configure_logging
from argus_api.schema import SignoffDecision

NOTE_MAX_CHARS = 2000
RATIONALE_MAX_CHARS = 4000
# The largest legitimate body is a sign-off at both text limits with every character JSON-escaped as
# \uXXXX (6 bytes each), plus the field names: about 36 KB. 64 KiB leaves room and nothing more.
MAX_BODY_BYTES = 64 * 1024
# The commit the image was built from, set at deploy time (--set-env-vars ARGUS_GIT_SHA=<sha>).
GIT_SHA_ENV = "ARGUS_GIT_SHA"
GIT_SHA_UNKNOWN = "unknown"

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings()
    corpus()  # CorpusError when data/corpus is missing: the server does not start without its library
    yield


class _SettingsCORS:
    """CORS from the settings, built on the first cross-origin request (settings load lazily from the env).
    Requests without an Origin header (health checks, server-to-server) pass straight through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._cors: dict[tuple[str, ...], CORSMiddleware] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not any(k == b"origin" for k, _ in scope["headers"]):
            await self.app(scope, receive, send)
            return
        origins = settings().allowed_origins
        if origins not in self._cors:
            self._cors[origins] = CORSMiddleware(
                self.app,
                allow_origins=list(origins),
                allow_methods=["GET", "POST"],
                allow_headers=["Authorization", "Content-Type"],
            )
        await self._cors[origins](scope, receive, send)


class _BodyLimit:
    """Refuse (413) a request body over `limit` bytes: by Content-Length when given, else while it streams.
    The body is read at most to the limit, then replayed to the app."""

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        detail = f"Request body is larger than {self.limit} bytes."
        await JSONResponse({"detail": detail}, status_code=413)(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for key, value in scope["headers"]:
            if key == b"content-length":
                if not value.isdigit() or int(value) > self.limit:
                    await self._refuse(scope, receive, send)
                    return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.limit:
                await self._refuse(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay, send)


log = logging.getLogger("argus.api")

app = FastAPI(title="Argus API", lifespan=lifespan)
# The last one added runs first: CORS wraps everything (so a 413 still carries CORS headers for the browser),
# then the body limit, before any body is parsed.
app.add_middleware(_BodyLimit)
app.add_middleware(_SettingsCORS)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """422 naming each bad field and the problem, never echoing the submitted value back."""
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return JSONResponse({"detail": errors}, status_code=422)


@app.exception_handler(StoreError)
async def _store_error(_: Request, exc: StoreError) -> JSONResponse:
    """503 with a plain-English reason instead of a bare 500 (which the browser would also see as a CORS
    failure, because it carries no CORS headers)."""
    if isinstance(exc, Busy):
        detail = "Argus is busy with other reviews right now. Please try again in a few seconds."
    else:
        detail = "The database is not available right now. Please try again shortly."
    log.error("store error: %s", exc)
    return JSONResponse({"detail": detail}, status_code=503)


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=1)
def llm() -> LLM:
    return build_llm(settings())


@lru_cache(maxsize=1)
def db() -> firestore.Client:
    return firestore.Client(project=settings().project)


def store() -> DocumentStore:
    return FirestoreStore(db())


@lru_cache(maxsize=1)
def corpus() -> Corpus:
    return Corpus.load(DEFAULT_OUT_DIR)


def runner() -> runs.Runner:
    return runs.Runner(store(), llm(), settings(), corpus())


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


def git_sha() -> str:
    """ARGUS_GIT_SHA as deployed, or "unknown" when it is not set (a local server): never an error."""
    return os.environ.get(GIT_SHA_ENV, "").strip() or GIT_SHA_UNKNOWN


@app.get("/health")
def health() -> dict[str, str | None]:
    """What this server is running. Settings and the corpus (with its hash) are loaded once at startup by the
    lifespan, so this reads cached values and calls no other service. location is null in local mode."""
    s = settings()
    return {
        "status": "ok",
        "git_sha": git_sha(),
        "mode": s.mode,
        "provider": s.provider,
        "agent_model": s.agent_model,
        "monitor_model": s.monitor_model,
        "location": s.location,
        "corpus_hash": corpus().corpus_hash,
    }


@app.post("/api/smoke")
def smoke(uid: Annotated[str, Depends(current_uid)]) -> dict[str, Any]:
    """Proves the deployed path works: Firebase Auth -> Cloud Run -> Gemini -> Firestore.

    It spends a model call, so it passes the same gate as a run (kill switch, daily, hourly and per-user
    caps) and its record is created, and counted, before the call. A failed call is saved on the record as
    status failed and answered with 502."""
    s = settings()
    st = store()
    run_id = uuid.uuid4().hex
    path = f"{runs.SMOKE}/{run_id}"
    created = datetime.now(UTC)
    record: dict[str, Any] = {
        "owner_uid": uid,
        "model": s.agent_model,
        "mode": s.mode,
        "provider": s.provider,
        "status": "running",
        "created_at": created,
    }
    try:
        runs.admit_counted(st, path, record, uid, created, "Smoke check")
    except runs.Rejected as e:
        raise _refuse(e) from e
    started = time.perf_counter()
    try:
        response = llm().complete(s.agent_model, "Reply with the single word: ready")
    except LLMError as e:
        st.update(path, {"status": "failed", "error": f"{type(e).__name__}: {e}"})
        raise HTTPException(502, f"The model call failed: {e}") from e
    latency_ms = round((time.perf_counter() - started) * 1000)
    st.update(
        path,
        {
            "status": "done",
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "text": response.text,
            "latency_ms": latency_ms,
        },
    )
    return {
        "run_id": run_id,
        "owner_uid": uid,
        "provider": s.provider,
        "model": response.model,
        "text": response.text,
        "latency_ms": latency_ms,
    }


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=runs.RUN_ID_PATTERN, description="client-generated uuid4().hex")
    policy_id: str


class SignoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: SignoffDecision
    note: str = Field(max_length=NOTE_MAX_CHARS)
    edited_rationale: str | None = Field(default=None, max_length=RATIONALE_MAX_CHARS)


def _refuse(e: runs.Rejected) -> HTTPException:
    return HTTPException(e.status_code, str(e))


@app.post("/api/runs")
def start_run(body: StartRunRequest, uid: Annotated[str, Depends(current_uid)]) -> dict[str, Any]:
    """Review a sample policy. The Run doc (status running) exists before any clause is reviewed."""
    try:
        policy = runs.load_policy(body.policy_id)
        run = runner().run_review(body.run_id, uid, policy)
    except runs.Rejected as e:
        raise _refuse(e) from e
    result: dict[str, Any] = run.model_dump(mode="json")
    return result


@app.post("/api/runs/{run_id}/findings/{clause_id}/signoff")
def signoff(
    run_id: str, clause_id: str, body: SignoffRequest, uid: Annotated[str, Depends(current_uid)]
) -> dict[str, Any]:
    try:
        finding = runs.sign_off(
            store(), run_id, clause_id, uid, body.decision, body.note, body.edited_rationale
        )
    except runs.Rejected as e:
        raise _refuse(e) from e
    result: dict[str, Any] = finding.model_dump(mode="json")
    return result
