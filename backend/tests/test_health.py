"""GET /health: liveness plus what is deployed (git sha, mode, provider, pinned models, location, corpus
hash), and scripts/corpus_hash.py, which prints the same corpus hash for a library directory."""

import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus_api import breaker, main
from argus_api.config import PINNED_MODELS, Settings
from argus_api.corpus import store as store_module
from argus_api.corpus.common import DEFAULT_OUT_DIR
from argus_api.corpus.store import Corpus, corpus_hash
from tests.run_support import FIXTURE_DIR, fixture_corpus, settings

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "corpus_hash.py"


def independent_hash(directory: Path) -> str:
    """The corpus hash recomputed straight from paragraphs.json, without argus_api: sha256 over the lines
    "<id> <content_hash>\\n" sorted by id."""
    rows = json.loads((directory / "paragraphs.json").read_text(encoding="utf-8"))
    lines = sorted(f"{r['id']} {r['content_hash']}\n" for r in rows)
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def vertex_settings() -> Settings:
    agent, monitor = PINNED_MODELS["vertex"]
    return Settings(
        mode="cloud",
        provider="vertex",
        project="test-project",
        location="asia-south1",
        api_key=None,
        agent_model=agent,
        monitor_model=monitor,
        allowed_origins=("https://argus-test.web.app",),
    )


@pytest.fixture()
def health_api(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    corpus = fixture_corpus()
    monkeypatch.setattr(breaker, "_breakers", {})  # a fresh process: every breaker starts closed
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "corpus", lambda: corpus)
    monkeypatch.delenv(main.GIT_SHA_ENV, raising=False)
    return TestClient(main.app)


def test_health_reports_what_is_running_without_calling_any_service(
    health_api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_service() -> None:
        raise AssertionError("/health must not touch Firestore, Auth or the model")

    for name in ("db", "store", "llm", "token_verifier"):
        monkeypatch.setattr(main, name, no_service)
    s = settings()
    body = health_api.get("/health").json()
    assert body == {
        "status": "ok",
        "git_sha": main.GIT_SHA_UNKNOWN,
        "mode": s.mode,
        "provider": s.provider,
        "agent_model": s.agent_model,
        "monitor_model": s.monitor_model,
        "location": s.location,
        "corpus_hash": independent_hash(FIXTURE_DIR),
        "agent_breaker": "closed",
    }


def test_health_reports_cloud_settings(health_api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    s = vertex_settings()
    monkeypatch.setattr(main, "settings", lambda: s)
    body = health_api.get("/health").json()
    assert (body["mode"], body["provider"], body["location"]) == ("cloud", "vertex", s.location)
    assert (body["agent_model"], body["monitor_model"]) == PINNED_MODELS["vertex"]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ("0123456789abcdef0123456789abcdef01234567", "0123456789abcdef0123456789abcdef01234567"),
        ("  abc1234\n", "abc1234"),
        ("", main.GIT_SHA_UNKNOWN),
        ("   ", main.GIT_SHA_UNKNOWN),
    ],
)
def test_health_git_sha_comes_from_the_deploy_env(
    health_api: TestClient, monkeypatch: pytest.MonkeyPatch, env: str, expected: str
) -> None:
    monkeypatch.setenv(main.GIT_SHA_ENV, env)
    r = health_api.get("/health")
    assert r.status_code == 200
    assert r.json()["git_sha"] == expected


def test_corpus_hash_is_computed_once_when_the_library_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = fixture_corpus()
    calls: list[int] = []

    def counting(paragraphs: object) -> str:
        calls.append(1)
        return "never"

    monkeypatch.setattr(store_module, "corpus_hash", counting)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "corpus", lambda: corpus)
    client = TestClient(main.app)
    hashes = {client.get("/health").json()["corpus_hash"] for _ in range(3)}
    assert hashes == {independent_hash(FIXTURE_DIR)}
    assert calls == []


def test_corpus_hash_ignores_file_order_and_sees_every_change() -> None:
    corpus = fixture_corpus()
    paragraphs = list(corpus.paragraphs.values())
    shuffled = paragraphs[:]
    random.Random(7).shuffle(shuffled)
    assert shuffled != paragraphs
    assert corpus_hash(shuffled) == corpus_hash(paragraphs) == corpus.corpus_hash
    first = paragraphs[0]
    changed = [first.model_copy(update={"content_hash": "0" * 64}), *paragraphs[1:]]
    assert corpus_hash(changed) != corpus.corpus_hash
    assert corpus_hash(paragraphs[1:]) != corpus.corpus_hash
    renumbered = [first.model_copy(update={"id": first.id + "x"}), *paragraphs[1:]]
    assert corpus_hash(renumbered) != corpus.corpus_hash


@pytest.mark.parametrize("directory", [None, FIXTURE_DIR], ids=["data/corpus (default)", "test fixture"])
def test_corpus_hash_script_prints_what_health_reports(
    directory: Path | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = [sys.executable, str(SCRIPT)] + ([] if directory is None else [str(directory)])
    out = subprocess.run(args, capture_output=True, text=True, check=True, cwd=REPO)
    printed = out.stdout.strip()
    library = DEFAULT_OUT_DIR if directory is None else directory
    assert printed == independent_hash(library)

    loaded = Corpus.load(library)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "corpus", lambda: loaded)
    assert TestClient(main.app).get("/health").json()["corpus_hash"] == printed


def test_corpus_hash_script_refuses_a_missing_library(tmp_path: Path) -> None:
    out = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True)
    assert out.returncode != 0
    assert "not found" in out.stderr


class Ticks:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_health_shows_the_agent_models_breaker_state_read_only(health_api: TestClient) -> None:
    """A release smoke that opened the breaker shows in /health before a demo run is sent to the backup."""
    s = settings()
    ticks = Ticks()
    b = breaker.Breaker(s.agent_model, clock=ticks)
    breaker._breakers[s.agent_model] = b
    assert breaker.process_breaker(s.agent_model) is b
    assert health_api.get("/health").json()["agent_breaker"] == "closed"
    for _ in range(breaker.OPEN_AFTER):
        b.report("primary", "busy")
    body = health_api.get("/health").json()
    assert body["agent_breaker"] == "open"
    assert set(body) == {
        "status",
        "git_sha",
        "mode",
        "provider",
        "agent_model",
        "monitor_model",
        "location",
        "corpus_hash",
        "agent_breaker",
    }  # the state, and nothing else about the breaker
    ticks.now += breaker.OPEN_S
    assert health_api.get("/health").json()["agent_breaker"] == "half_open"
    health_api.get("/health")
    assert b.route() == "trial"  # reading /health took no trial slot: the next clause still gets it
