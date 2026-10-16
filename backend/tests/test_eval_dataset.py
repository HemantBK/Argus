"""Loading and validating eval case files: each problem type is caught, valid fixtures pass."""

import json
from pathlib import Path
from typing import Any

import pytest

from argus_api.corpus.store import Corpus
from argus_api.eval.dataset import (
    BadDraft,
    EvalCase,
    EvalCaseError,
    GoldCitation,
    GoldReference,
    ProblemKind,
    load_bad_drafts,
    load_cases,
    validate_bad_drafts,
    validate_cases,
)
from argus_api.schema import CheckName, FindingLabel

FIXTURES = Path(__file__).parent / "fixtures" / "eval"
CORPUS_DIR = Path(__file__).parent / "fixtures" / "corpus"


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return Corpus.load(CORPUS_DIR)


def _cases() -> list[EvalCase]:
    return load_cases(FIXTURES / "clauses.json") + load_cases(FIXTURES / "attacks.json")


def _by_id(case_id: str) -> EvalCase:
    return next(c for c in _cases() if c.case_id == case_id)


def _raw(name: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _kinds(problems: list[Any]) -> list[tuple[str, ProblemKind]]:
    return [(p.case_id, p.kind) for p in problems]


def test_fixtures_load_and_validate_clean(corpus: Corpus) -> None:
    cases = _cases()
    assert [c.case_id for c in cases] == [r["case_id"] for r in _raw("clauses.json") + _raw("attacks.json")]
    assert validate_cases(cases, corpus) == []
    drafts = load_bad_drafts(FIXTURES / "bad_drafts.json")
    assert [d.case_id for d in drafts] == [r["case_id"] for r in _raw("bad_drafts.json")]
    assert validate_bad_drafts(drafts, corpus) == []


def test_duplicate_ids_are_reported(corpus: Corpus) -> None:
    cases = _cases()
    dup = cases + [cases[0]]
    assert _kinds(validate_cases(dup, corpus)) == [(cases[0].case_id, ProblemKind.DUPLICATE_ID)]
    drafts = load_bad_drafts(FIXTURES / "bad_drafts.json")
    assert _kinds(validate_bad_drafts(drafts + [drafts[-1]], corpus)) == [
        (drafts[-1].case_id, ProblemKind.DUPLICATE_ID)
    ]


def _with_citation(case: EvalCase, **change: str) -> EvalCase:
    assert case.gold_citation is not None
    return case.model_copy(update={"gold_citation": case.gold_citation.model_copy(update=change)})


def test_missing_paragraph_is_reported(corpus: Corpus) -> None:
    case = _with_citation(_by_id("fx-c1"), para="10(99)")
    assert corpus.paragraph(case.gold_citation.instrument_ref, "10(99)") is None  # type: ignore[union-attr]
    assert _kinds(validate_cases([case], corpus)) == [("fx-c1", ProblemKind.MISSING_PARAGRAPH)]


def test_unknown_instrument_is_reported(corpus: Corpus) -> None:
    case = _with_citation(_by_id("fx-c1"), instrument_ref="RBI/1999-00/1")
    assert _kinds(validate_cases([case], corpus)) == [("fx-c1", ProblemKind.UNKNOWN_INSTRUMENT)]


def test_unverified_quote_is_reported(corpus: Corpus) -> None:
    base = _by_id("fx-c1")
    assert base.gold_citation is not None
    altered = base.gold_citation.quote.replace("directly", "promptly")
    case = _with_citation(base, quote=altered)
    assert _kinds(validate_cases([case], corpus)) == [("fx-c1", ProblemKind.QUOTE_NOT_VERIFIED)]


def test_gold_citation_not_in_force_is_reported(corpus: Corpus) -> None:
    ref, para = "RBI/2025-26/36", "9(iv)"
    assert not corpus.in_force(ref)
    p = corpus.paragraph(ref, para)
    assert p is not None
    quote = p.text.split(" ", 1)[1]  # drop the "iv." list marker; the rest is verbatim
    case = _by_id("fx-c1").model_copy(
        update={"gold_citation": GoldCitation(instrument_ref=ref, para=para, quote=quote)}
    )
    assert _kinds(validate_cases([case], corpus)) == [("fx-c1", ProblemKind.CITATION_NOT_IN_FORCE)]


def test_replaced_citation_in_force_and_mismatch_are_reported(corpus: Corpus) -> None:
    base = _by_id("fx-c2")
    assert base.gold_citation is not None
    in_force = base.model_copy(
        update={
            "gold_replaced_citation": GoldReference(
                instrument_ref=base.gold_citation.instrument_ref, para=base.gold_citation.para
            )
        }
    )
    assert _kinds(validate_cases([in_force], corpus)) == [
        ("fx-c2", ProblemKind.REPLACED_IN_FORCE),
        ("fx-c2", ProblemKind.POLICY_CITE_MISMATCH),  # the policy still cites the old instrument
    ]
    missing = base.model_copy(
        update={"gold_replaced_citation": GoldReference(instrument_ref="RBI/2025-26/36", para="99")}
    )
    assert _kinds(validate_cases([missing], corpus)) == [("fx-c2", ProblemKind.MISSING_PARAGRAPH)]


@pytest.mark.parametrize(
    ("case_id", "change"),
    [
        ("fx-b1", {"citation_para": "10(4)"}),  # expected paragraph_exists, but the paragraph exists
        ("fx-b2", {"citation_instrument_ref": "RBI/DOR/2025-26/347", "citation_para": "10(4)"}),  # in force
        ("fx-b3", {"citation_para": "10(9)"}),  # expected continue, but the citation is broken
    ],
)
def test_bad_draft_whose_citation_lacks_its_fault_is_reported(
    corpus: Corpus, case_id: str, change: dict[str, str]
) -> None:
    d = next(x for x in load_bad_drafts(FIXTURES / "bad_drafts.json") if x.case_id == case_id)
    changed = d.model_copy(update={"draft": d.draft.model_copy(update=change)})
    assert _kinds(validate_bad_drafts([changed], corpus)) == [(case_id, ProblemKind.DRAFT_FAULT_MISMATCH)]


def _write(tmp_path: Path, rows: Any) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(extra_field=1),
        lambda r: r.pop("rationale"),
        lambda r: r.update(gold_label="partly_compliant"),
        lambda r: r.update(gold_citation=None),  # only insufficient_evidence may lack a citation
        lambda r: r.update(policy_cited_instrument_ref=None, policy_cited_para="10(i)"),
        lambda r: r.update(kind="attack"),  # an attack needs attack_type
        lambda r: r.update(attack_type="injection"),  # a clause must not have one
        lambda r: r.update(gold_label="outdated_reference"),  # needs gold_replaced_citation
    ],
)
def test_malformed_case_fails_to_load(tmp_path: Path, mutate: Any) -> None:
    row = _raw("clauses.json")[0]
    assert row["gold_label"] != FindingLabel.OUTDATED_REFERENCE and row["gold_replaced_citation"] is None
    mutate(row)
    with pytest.raises(EvalCaseError, match=row["case_id"]):
        load_cases(_write(tmp_path, [row]))


def test_insufficient_evidence_may_have_no_citation(tmp_path: Path) -> None:
    row = _raw("clauses.json")[0]
    row.update(gold_label=FindingLabel.INSUFFICIENT_EVIDENCE.value, gold_citation=None)
    assert load_cases(_write(tmp_path, [row]))[0].gold_citation is None


def test_case_file_must_be_a_list(tmp_path: Path) -> None:
    with pytest.raises(EvalCaseError, match="JSON list"):
        load_cases(_write(tmp_path, {"cases": []}))
    with pytest.raises(EvalCaseError, match="not found"):
        load_cases(tmp_path / "absent.json")


def test_bad_draft_continue_iff_no_failed_check(tmp_path: Path) -> None:
    rows = _raw("bad_drafts.json")
    cont = next(r for r in rows if r["expected_failed_check"] is None)
    cont["expected_failed_check"] = CheckName.SUPPORTED.value
    with pytest.raises(EvalCaseError, match=cont["case_id"]):
        load_bad_drafts(_write(tmp_path, [cont]))
    assert BadDraft.model_fields.keys() == {
        "case_id",
        "clause_text",
        "draft",
        "expected_monitor_action",
        "expected_failed_check",
    }
