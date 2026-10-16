"""The evaluation case files, as strict models, and their validation against the RBI corpus.

cases/clauses.json and cases/attacks.json hold EvalCase lists; cases/bad_drafts.json holds BadDraft lists.
Loading rejects any unknown or missing field and any internal inconsistency (EvalCaseError). validate_cases
and validate_bad_drafts then check the cases against the corpus and return every Problem found; the CLI
refuses to run while any problem remains.
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from argus_api.corpus.store import Corpus
from argus_api.schema import CheckName, FindingLabel, MonitorAction

CASES_DIR = Path(__file__).resolve().parent / "cases"
CLAUSES_FILE = "clauses.json"
ATTACKS_FILE = "attacks.json"
BAD_DRAFTS_FILE = "bad_drafts.json"

# Checks that judge the finding's own citation; a bad draft expected to fail one of them must carry the fault
# in its citation, and one expected to fail any other check (or none) must carry a sound citation.
CITATION_FAULTS = (CheckName.PARAGRAPH_EXISTS, CheckName.QUOTE_MATCHES, CheckName.IN_FORCE)


class EvalCaseError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GoldCitation(_Strict):
    instrument_ref: str = Field(min_length=1)
    para: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class GoldReference(_Strict):
    instrument_ref: str = Field(min_length=1)
    para: str = Field(min_length=1)


class EvalCase(_Strict):
    case_id: str = Field(min_length=1)
    kind: Literal["clause", "attack"]
    clause_text: str = Field(min_length=1)
    policy_cited_instrument_ref: str | None
    policy_cited_para: str | None
    gold_label: FindingLabel
    gold_citation: GoldCitation | None
    gold_replaced_citation: GoldReference | None
    rationale: str = Field(min_length=1)
    attack_type: str | None
    expected_monitor_action: MonitorAction | None

    @model_validator(mode="after")
    def _consistent(self) -> "EvalCase":
        if self.gold_citation is None and self.gold_label != FindingLabel.INSUFFICIENT_EVIDENCE:
            raise ValueError(
                f"gold_citation is null but gold_label is {self.gold_label.value}; only "
                "insufficient_evidence may have no gold citation"
            )
        if self.gold_label == FindingLabel.OUTDATED_REFERENCE and self.gold_replaced_citation is None:
            raise ValueError("an outdated_reference case needs gold_replaced_citation (the policy's cite)")
        if self.policy_cited_para is not None and self.policy_cited_instrument_ref is None:
            raise ValueError("policy_cited_para is set without policy_cited_instrument_ref")
        if self.kind == "attack" and not self.attack_type:
            raise ValueError("an attack case needs attack_type")
        if self.kind == "clause" and self.attack_type is not None:
            raise ValueError("a clause case must have attack_type null")
        return self


class DraftSpec(_Strict):
    """A drafted finding as the monitor receives it (the fields of monitor.DraftFinding)."""

    clause_id: str = Field(min_length=1)
    label: FindingLabel
    rationale: str
    citation_instrument_ref: str
    citation_para: str
    citation_quote: str
    policy_cited_instrument_ref: str | None
    policy_cited_para: str | None


class BadDraft(_Strict):
    case_id: str = Field(min_length=1)
    clause_text: str = Field(min_length=1)
    draft: DraftSpec
    expected_monitor_action: MonitorAction
    expected_failed_check: CheckName | None

    @model_validator(mode="after")
    def _consistent(self) -> "BadDraft":
        if (self.expected_monitor_action == MonitorAction.CONTINUE) != (self.expected_failed_check is None):
            raise ValueError(
                "expected_failed_check must be null exactly when expected_monitor_action is continue "
                f"(got {self.expected_monitor_action.value} with {self.expected_failed_check})"
            )
        return self


def _read_list(path: Path) -> list[Any]:
    if not path.is_file():
        raise EvalCaseError(f"case file {path} not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise EvalCaseError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise EvalCaseError(f"{path} must hold a JSON list, got {type(data).__name__}")
    return data


def _parse(model: type[BaseModel], path: Path, rows: list[Any]) -> list[Any]:
    out = []
    for i, row in enumerate(rows):
        label = row.get("case_id", f"#{i}") if isinstance(row, dict) else f"#{i}"
        try:
            # JSON gives strings for enums; validate in JSON mode so strict typing still applies to the rest.
            out.append(model.model_validate_json(json.dumps(row)))
        except ValidationError as e:
            raise EvalCaseError(f"{path} entry {i} ({label}): {e}") from e
    return out


def load_cases(path: Path) -> list[EvalCase]:
    return _parse(EvalCase, path, _read_list(path))


def load_bad_drafts(path: Path) -> list[BadDraft]:
    return _parse(BadDraft, path, _read_list(path))


class ProblemKind(StrEnum):
    DUPLICATE_ID = "duplicate_id"
    UNKNOWN_INSTRUMENT = "unknown_instrument"
    MISSING_PARAGRAPH = "missing_paragraph"
    QUOTE_NOT_VERIFIED = "quote_not_verified"
    CITATION_NOT_IN_FORCE = "citation_not_in_force"
    REPLACED_IN_FORCE = "replaced_in_force"
    POLICY_CITE_MISMATCH = "policy_cite_mismatch"
    DRAFT_FAULT_MISMATCH = "draft_fault_mismatch"


@dataclass(frozen=True)
class Problem:
    case_id: str
    kind: ProblemKind
    detail: str

    def __str__(self) -> str:
        return f"{self.case_id}: {self.kind.value}: {self.detail}"


def _duplicates(ids: list[str]) -> list[Problem]:
    seen: set[str] = set()
    out = []
    for cid in ids:
        if cid in seen:
            out.append(Problem(cid, ProblemKind.DUPLICATE_ID, f"case_id {cid!r} appears more than once"))
        seen.add(cid)
    return out


def _reference_problems(corpus: Corpus, case_id: str, what: str, ref: str, para: str) -> list[Problem]:
    if ref not in corpus.instruments:
        detail = f"{what} instrument {ref!r} is not in the corpus"
        return [Problem(case_id, ProblemKind.UNKNOWN_INSTRUMENT, detail)]
    if corpus.paragraph(ref, para) is None:
        detail = f"{what} para {para!r} of {ref} is not in the corpus"
        return [Problem(case_id, ProblemKind.MISSING_PARAGRAPH, detail)]
    return []


def validate_cases(cases: list[EvalCase], corpus: Corpus) -> list[Problem]:
    """Every problem in the gold set: duplicate ids, gold citations missing from the corpus, not verbatim or
    not in force, replaced citations that are in force, and replaced citations the policy does not cite."""
    problems = _duplicates([c.case_id for c in cases])
    for c in cases:
        g = c.gold_citation
        if g is not None:
            found = _reference_problems(corpus, c.case_id, "gold_citation", g.instrument_ref, g.para)
            problems += found
            if not found:
                if not corpus.verify_quote(g.instrument_ref, g.para, g.quote).matches:
                    problems.append(
                        Problem(
                            c.case_id,
                            ProblemKind.QUOTE_NOT_VERIFIED,
                            f"gold quote is not verbatim in para {g.para} of {g.instrument_ref}: {g.quote!r}",
                        )
                    )
                if not corpus.in_force(g.instrument_ref):
                    problems.append(
                        Problem(
                            c.case_id,
                            ProblemKind.CITATION_NOT_IN_FORCE,
                            f"gold citation {g.instrument_ref} is not in force",
                        )
                    )
        r = c.gold_replaced_citation
        if r is not None:
            found = _reference_problems(corpus, c.case_id, "gold_replaced_citation", r.instrument_ref, r.para)
            problems += found
            if not found and corpus.in_force(r.instrument_ref):
                problems.append(
                    Problem(
                        c.case_id,
                        ProblemKind.REPLACED_IN_FORCE,
                        f"gold_replaced_citation {r.instrument_ref} is in force, so nothing was replaced",
                    )
                )
            if c.policy_cited_instrument_ref != r.instrument_ref:
                problems.append(
                    Problem(
                        c.case_id,
                        ProblemKind.POLICY_CITE_MISMATCH,
                        f"gold_replaced_citation is {r.instrument_ref} but the policy cites "
                        f"{c.policy_cited_instrument_ref}",
                    )
                )
    return problems


def validate_bad_drafts(drafts: list[BadDraft], corpus: Corpus) -> list[Problem]:
    """Duplicate ids, and a draft whose citation does not carry the fault it is expected to fail on.

    A draft expected to fail paragraph_exists must cite a missing paragraph; quote_matches, an existing
    paragraph with a quote that does not verify; in_force, a verbatim quote from an instrument not in force.
    Every other draft must carry a sound citation (exists, verbatim, in force), so that the expected check
    is the one that fails."""
    problems = _duplicates([d.case_id for d in drafts])
    for d in drafts:
        ref, para, quote = d.draft.citation_instrument_ref, d.draft.citation_para, d.draft.citation_quote
        known = ref in corpus.instruments
        exists = known and corpus.paragraph(ref, para) is not None
        verbatim = exists and bool(quote.strip()) and corpus.verify_quote(ref, para, quote).matches
        in_force = known and corpus.in_force(ref)
        expected = d.expected_failed_check
        if expected == CheckName.PARAGRAPH_EXISTS:
            ok, want = not exists, "a paragraph that is not in the corpus"
        elif expected == CheckName.QUOTE_MATCHES:
            ok, want = exists and not verbatim, "an existing paragraph with a quote that does not verify"
        elif expected == CheckName.IN_FORCE:
            ok, want = verbatim and not in_force, "a verbatim quote from an instrument not in force"
        else:
            ok, want = verbatim and in_force, "a sound citation (exists, verbatim, in force)"
        if not ok:
            problems.append(
                Problem(
                    d.case_id,
                    ProblemKind.DRAFT_FAULT_MISMATCH,
                    f"expected_failed_check {expected.value if expected else None} needs {want}; the draft "
                    f"cites para {para!r} of {ref!r} (exists={exists}, verbatim={verbatim}, "
                    f"in_force={in_force})",
                )
            )
    return problems
