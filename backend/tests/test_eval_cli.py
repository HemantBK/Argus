"""The eval CLI offline: results are written locally, Firestore only with --publish, bad cases stop."""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from argus_api.config import PINNED_MODELS, Settings
from argus_api.corpus.store import Corpus
from argus_api.eval.dataset import load_cases
from argus_api.eval.report import FIRESTORE_COLLECTION, FIRESTORE_DOC
from argus_api.eval.run import main
from argus_api.eval.runner import EvalError
from argus_api.eval.settings import EvalConfigError, EvalSettings, load_env_file
from argus_api.llm_fake import FakeLLM

FIXTURES = Path(__file__).parent / "fixtures" / "eval"
CORPUS_DIR = Path(__file__).parent / "fixtures" / "corpus"
CASE_FILES = [FIXTURES / "clauses.json", FIXTURES / "attacks.json"]


def settings() -> EvalSettings:
    agent, monitor = PINNED_MODELS["openrouter"]
    return EvalSettings.from_settings(
        Settings(
            mode="local",
            provider="openrouter",
            project="test",
            location=None,
            api_key="unused-by-fake",
            agent_model=agent,
            monitor_model=monitor,
        )
    )


def replies() -> list[dict[str, Any]]:
    out = []
    for path in CASE_FILES:
        for c in load_cases(path):
            g = c.gold_citation
            assert g is not None
            out.append(
                {
                    "label": c.gold_label.value,
                    "rationale": "r",
                    "citation_instrument_ref": g.instrument_ref,
                    "citation_para": g.para,
                    "citation_quote": g.quote,
                    "policy_cited_instrument_ref": c.policy_cited_instrument_ref,
                    "policy_cited_para": c.policy_cited_para,
                }
            )
    return out


class FakeDoc:
    def __init__(self, store: dict[str, Any], path: str) -> None:
        self.store, self.path = store, path

    def set(self, data: dict[str, Any]) -> None:
        self.store[self.path] = data


class FakeFirestore:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    def collection(self, name: str) -> Any:
        store = self.store

        class Col:
            def document(self, doc: str) -> FakeDoc:
                return FakeDoc(store, f"{name}/{doc}")

        return Col()


def run(tmp_path: Path, extra: list[str], factory_calls: list[FakeFirestore]) -> Path:
    def factory(es: EvalSettings) -> FakeFirestore:
        fs = FakeFirestore()
        factory_calls.append(fs)
        return fs

    argv = ["--conditions", "plain_gemini", "--cases", *map(str, CASE_FILES), "--results-dir", str(tmp_path)]
    return main(
        argv + extra,
        settings=settings(),
        llm=FakeLLM(json_replies=replies()),
        corpus=Corpus.load(CORPUS_DIR),
        firestore_factory=factory,
    )


def test_nothing_is_published_without_the_flag(tmp_path: Path) -> None:
    calls: list[FakeFirestore] = []
    out = run(tmp_path, [], calls)
    assert calls == []
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    n_cases = len(replies())
    assert results["conditions"]["plain_gemini"]["records"] == n_cases
    assert len(results["records"]) == n_cases
    assert results["corpus"]["snapshot_date"]
    assert results["git"]["sha"]
    assert results["models"]["pinned"] is True
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "benign_utility" in report and results["git"]["sha"] in report
    for name, m in results["conditions"]["plain_gemini"]["metrics"].items():
        assert "n" in m, name


def test_publish_writes_the_summary_only(tmp_path: Path) -> None:
    calls: list[FakeFirestore] = []
    out = run(tmp_path, ["--publish"], calls)
    assert len(calls) == 1
    doc = calls[0].store[f"{FIRESTORE_COLLECTION}/{FIRESTORE_DOC}"]
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert "records" not in doc
    assert doc == {k: v for k, v in results.items() if k != "records"}


def test_limit_takes_the_first_cases(tmp_path: Path) -> None:
    out = run(tmp_path, ["--limit", "2"], [])
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    first_two = [c.case_id for p in CASE_FILES for c in load_cases(p)][:2]
    assert [r["case_id"] for r in results["records"]] == first_two


def test_invalid_cases_stop_the_run_before_any_model_call(tmp_path: Path) -> None:
    rows = json.loads(CASE_FILES[0].read_text(encoding="utf-8"))
    rows[0]["gold_citation"]["para"] = "10(99)"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(rows), encoding="utf-8")
    llm = FakeLLM()
    with pytest.raises(EvalError, match="missing_paragraph"):
        main(
            ["--conditions", "plain_gemini", "--cases", str(bad), "--results-dir", str(tmp_path / "out")],
            settings=settings(),
            llm=llm,
            corpus=Corpus.load(CORPUS_DIR),
        )
    assert llm.calls == []
    assert not (tmp_path / "out").exists()


def test_settings_fail_loudly_and_env_file_does_not_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A private copy of the environment, so load_env_file cannot leak into other tests.
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("ARGUS_", "OPENROUTER_"))}
    monkeypatch.setattr(os, "environ", clean)
    with pytest.raises(EvalConfigError, match="ARGUS_LLM_PROVIDER"):
        EvalSettings.from_env()
    monkeypatch.setenv("ARGUS_LLM_PROVIDER", "openrouter")
    with pytest.raises(EvalConfigError, match="OPENROUTER_API_KEY"):
        EvalSettings.from_env()
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nOPENROUTER_API_KEY='from-file'\nARGUS_LLM_PROVIDER=aistudio\n", encoding="utf-8"
    )
    assert load_env_file(env) == ["OPENROUTER_API_KEY"]
    s = EvalSettings.from_env()
    assert s.provider == "openrouter" and s.settings.api_key == "from-file"
    assert (s.agent_model, s.monitor_model) == PINNED_MODELS["openrouter"] and s.pinned
    monkeypatch.setenv("ARGUS_AGENT_MODEL", "google/some-other-model")
    assert not EvalSettings.from_env().pinned
