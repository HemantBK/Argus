"""Structural checks on the synthetic sample policies and their expected findings."""

import json
from pathlib import Path
from typing import Any

import pytest

from argus_api.corpus import build_corpus as bc
from argus_api.corpus.store import Corpus
from argus_api.schema import LABEL_DEFINITIONS, FindingLabel

SAMPLES = Path(__file__).resolve().parents[1] / "argus_api" / "samples"
POLICY_IDS = ("policy_a", "policy_b")
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "corpus"
CITATION_KEYS = {"instrument", "instrument_ref", "para", "url", "quote"}


def _load(name: str) -> Any:
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    data: dict[str, Any] = _load("expected")
    return data


def test_every_sample_file_parses() -> None:
    files = sorted(SAMPLES.glob("*.json"))
    assert {f.stem for f in files} == {*POLICY_IDS, "expected"}
    for f in files:
        json.loads(f.read_text(encoding="utf-8"))


@pytest.mark.parametrize("policy_id", POLICY_IDS)
def test_policy_is_synthetic_with_unique_ids(policy_id: str) -> None:
    policy = _load(policy_id)
    assert policy["policy_id"] == policy_id
    assert policy["synthetic"] is True
    assert "SYNTHETIC" in policy["disclaimer"]
    ids = [c["id"] for c in policy["clauses"]]
    assert len(ids) == len(set(ids))
    assert all(c["text"].strip() for c in policy["clauses"])


@pytest.mark.parametrize("policy_id", POLICY_IDS)
def test_expected_entries_match_clauses(policy_id: str, expected: dict[str, Any]) -> None:
    clause_ids = {c["id"] for c in _load(policy_id)["clauses"]}
    assert set(expected["policies"][policy_id]) == clause_ids


@pytest.mark.parametrize("policy_id", POLICY_IDS)
def test_expected_labels_and_citations_are_well_formed(policy_id: str, expected: dict[str, Any]) -> None:
    valid_labels = {label.value for label in FindingLabel}
    for clause_id, entry in expected["policies"][policy_id].items():
        assert entry["label"] in valid_labels, clause_id
        assert set(entry["citation"]) == CITATION_KEYS, clause_id
        assert entry["citation"]["url"].startswith("https://www.rbi.org.in/"), clause_id
        is_outdated = entry["label"] == FindingLabel.OUTDATED_REFERENCE
        assert (entry["replaced_citation"] is not None) == is_outdated, clause_id
        if is_outdated:
            assert set(entry["replaced_citation"]) == CITATION_KEYS, clause_id


def test_planted_cases_are_present(expected: dict[str, Any]) -> None:
    labels = [e["label"] for p in POLICY_IDS for e in expected["policies"][p].values()]
    for label in (
        FindingLabel.OUTDATED_REFERENCE,
        FindingLabel.GAP,
        FindingLabel.CONFLICT,
        FindingLabel.COMPLIANT,
        FindingLabel.INSUFFICIENT_EVIDENCE,
    ):
        assert label in labels
    injected = [c for c in _load("policy_b")["clauses"] if "pre-cleared by RBI" in c["text"]]
    assert len(injected) == 1
    assert expected["policies"]["policy_b"][injected[0]["id"]]["label"] != FindingLabel.COMPLIANT


# ---- citations against the RBI library ------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return Corpus.load(FIXTURE_DIR)


def _citations(expected: dict[str, Any]) -> list[tuple[str, str, dict[str, Any], bool]]:
    """(policy, clause, citation, is_replaced) for every citation and replaced_citation."""
    out = []
    for policy_id in POLICY_IDS:
        for clause_id, entry in expected["policies"][policy_id].items():
            out.append((policy_id, clause_id, entry["citation"], False))
            if entry["replaced_citation"] is not None:
                out.append((policy_id, clause_id, entry["replaced_citation"], True))
    return out


def test_fixture_holds_every_sample_citation(expected: dict[str, Any]) -> None:
    cited = {(c["instrument_ref"], c["para"]) for c in bc.sample_citations()}
    assert cited == {(c["instrument_ref"], c["para"]) for _, _, c, _ in _citations(expected)}


def test_every_sample_citation_is_verbatim_in_the_library(expected: dict[str, Any], corpus: Corpus) -> None:
    for policy_id, clause_id, citation, is_replaced in _citations(expected):
        where = f"{policy_id} {clause_id} {citation['instrument_ref']} para {citation['para']}"
        check = corpus.verify_quote(citation["instrument_ref"], citation["para"], citation["quote"])
        assert check.paragraph_found, where
        assert check.matches, where
        inst = corpus.instrument(citation["instrument_ref"])
        assert citation["instrument"] == inst.instrument, where
        assert citation["url"] == inst.url, where
        # The finding cites the rule in force; a replaced_citation is the withdrawn text the clause relied on.
        assert inst.in_force is not is_replaced, where


def test_snapshot_date_matches_the_library(expected: dict[str, Any], corpus: Corpus) -> None:
    dates = {inst.snapshot_date.isoformat() for inst in corpus.instruments.values()}
    assert dates == {expected["snapshot_date"]}


def test_every_label_has_a_definition() -> None:
    assert set(LABEL_DEFINITIONS) == set(FindingLabel)
    assert all(text.strip() for text in LABEL_DEFINITIONS.values())
