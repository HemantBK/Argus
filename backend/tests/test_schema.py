import re
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from argus_api.schema import (
    EvalCell,
    EvalCondition,
    EvalCorpus,
    EvalCorpusDocument,
    EvalMetric,
    EvalResults,
    FindingLabel,
    MetricUnit,
    Run,
)
from argus_api.seed_fake_run import build


def test_fake_run_round_trips_through_json() -> None:
    run, steps, findings, audit = build()
    assert Run.model_validate(run.model_dump(mode="json")) == run
    assert {f.label for f in findings} <= set(FindingLabel)
    assert [s.seq for s in steps] == sorted(s.seq for s in steps)


def test_fake_run_is_labelled_fake() -> None:
    run, _, findings, _ = build()
    assert "FAKE" in run.policy_title
    assert all("FAKE" in f.clause_text for f in findings)


# ---- eval_results/latest ----------------------------------------------------------------------------------

EVAL_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "eval" / "evalResults.ts"


def _eval_doc(**update: Any) -> dict[str, Any]:
    """The example document of web/BACKEND_REQUESTS.md item 5, with one measured and one unmeasured cell."""
    doc: dict[str, Any] = {
        "run_date": "2026-10-18",
        "conditions": [{"id": "full_argus", "label": "Full Argus"}],
        "metrics": [
            {"id": "citation_accuracy", "label": "Citation accuracy", "unit": "ratio"},
            {"id": "latency", "label": "Latency per review", "unit": "seconds"},
        ],
        "results": [
            {
                "metric": "citation_accuracy",
                "condition": "full_argus",
                "value": 0.5,
                "n_label": "2 clause cases",
            },
            {"metric": "latency", "condition": "full_argus", "value": None, "n_label": None},
        ],
        "corpus": {
            "documents": [{"instrument": "Directions", "instrument_ref": "RBI/TEST/1"}],
            "as_on": "2026-07-15",
            "paragraphs": 3,
            "supersession_rows": 1,
        },
        "failure_cases": ["A plain-English sentence."],
    }
    doc.update(update)
    return doc


def test_eval_results_round_trips_the_page_shape() -> None:
    doc = _eval_doc()
    assert EvalResults.model_validate(doc).model_dump(mode="json") == doc
    assert EvalResults.model_validate(_eval_doc(corpus=None)).corpus is None


@pytest.mark.parametrize(
    ("update", "problem"),
    [
        (
            {"results": [{"metric": "latency", "condition": "full_argus", "value": 1.0, "n_label": None}]},
            "sample size",
        ),
        (
            {"results": [{"metric": "nope", "condition": "full_argus", "value": None, "n_label": None}]},
            "not in metrics",
        ),
        (
            {"results": [{"metric": "latency", "condition": "nope", "value": None, "n_label": None}]},
            "not in conditions",
        ),
        (
            {"results": [{"metric": "latency", "condition": "full_argus", "value": -1, "n_label": "n"}]},
            "greater than",
        ),
        ({"metrics": [{"id": "latency", "label": "Latency", "unit": "minutes"}], "results": []}, "unit"),
        ({"run_date": "  "}, "run_date"),
        ({"failure_cases": [""]}, "failure_cases"),
        ({"extra": 1}, "extra"),
    ],
)
def test_eval_results_refuses_what_the_page_refuses(update: dict[str, Any], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        EvalResults.model_validate(_eval_doc(**update))


def _ts_fields(source: str, interface: str) -> set[str]:
    match = re.search(rf"export interface {interface} \{{(.*?)\n\}}", source, re.S)
    assert match, f"interface {interface} not found in {EVAL_TS}"
    return set(re.findall(r"^\s+(\w+)\??:", match.group(1), re.M))


def test_eval_models_have_the_fields_the_page_reads() -> None:
    source = EVAL_TS.read_text(encoding="utf-8")
    pairs = {
        "EvalCondition": EvalCondition,
        "EvalMetric": EvalMetric,
        "EvalCell": EvalCell,
        "EvalCorpus": EvalCorpus,
        "EvalResults": EvalResults,
    }
    for ts_name, model in pairs.items():
        assert _ts_fields(source, ts_name) == set(model.model_fields), ts_name
    unit = re.search(r"export type MetricUnit = (.*?);", source)
    assert unit and set(re.findall(r'"(\w+)"', unit.group(1))) == set(get_args(MetricUnit))
    docs = re.search(r"documents: \{(.*?)\}\[\];", source)
    assert docs and set(re.findall(r"(\w+):", docs.group(1))) == set(EvalCorpusDocument.model_fields)
