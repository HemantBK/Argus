"""scripts/stats_intervals.py: the Wilson interval is right, every count comes from a results.json, and the
committed table in evidence/ is what the script writes."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "argus_stats_intervals", REPO / "scripts" / "stats_intervals.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["argus_stats_intervals"] = module
    spec.loader.exec_module(module)
    return module


si = _load()


def test_wilson_matches_a_value_worked_by_hand() -> None:
    # 22/23: p = 0.9565, z = 1.96. Centre (p + z^2/2n)/(1 + z^2/n) = 0.9078; half-width 0.0868.
    lo, hi = si.wilson(22, 23)
    assert lo == pytest.approx(0.7900, abs=5e-4)
    assert hi == pytest.approx(0.9923, abs=5e-4)


def test_wilson_ends_are_exact_and_never_leave_zero_to_one() -> None:
    lo, hi = si.wilson(0, 23)
    assert lo == 0.0 and 0.14 < hi < 0.15
    lo, hi = si.wilson(36, 36)
    assert hi == 1.0 and 0.90 < lo < 0.91
    for n in (1, 2, 5, 20, 46):
        for k in range(n + 1):
            lo, hi = si.wilson(k, n)
            assert 0.0 <= lo <= k / n <= hi <= 1.0


def test_wilson_matches_the_closed_form_for_one_success_in_one() -> None:
    # k = n = 1: the lower bound is 1 / (1 + z^2).
    assert si.wilson(1, 1)[0] == pytest.approx(1 / (1 + si.Z95**2))


@pytest.mark.parametrize("k,n", [(0, 0), (-1, 5), (6, 5)])
def test_wilson_refuses_impossible_counts(k: int, n: int) -> None:
    with pytest.raises(ValueError):
        si.wilson(k, n)


def _results(tmp: Path, run: str, metrics: dict[str, dict[str, Any]], records: list[dict[str, Any]]) -> None:
    (tmp / run).mkdir(parents=True)
    data = {"run_id": run, "conditions": {"full_argus": {"metrics": metrics}}, "records": records}
    (tmp / run / "results.json").write_text(json.dumps(data), encoding="utf-8")


def test_counts_come_from_results_json_and_pooled_rows_add_up(tmp_path: Path) -> None:
    recs = [{"condition": "full_argus", "case_id": c, "repeat": r} for c in ("a", "b") for r in (0, 1)]
    _results(tmp_path, "run-x", {"attack_success": {"k": 1, "n": 4, "value": 0.25}}, recs)
    _results(tmp_path, "run-y", {"attack_success": {"k": 2, "n": 6, "value": 0.33}}, recs)
    rows = (
        si.Row(
            "pooled",
            (
                si.Part("run-x", "full_argus", "attack_success"),
                si.Part("run-y", "full_argus", "attack_success"),
            ),
        ),
    )
    text = si.table(tmp_path, rows)
    lo, hi = si.wilson(3, 10)
    assert f"| pooled | `x`, `y` | 2 | 3/10 | 0.30 | [{lo:.2f}, {hi:.2f}] |" in text
    assert "Preliminary" in text and "optimistic" in text


def test_a_missing_metric_is_an_error_not_a_zero(tmp_path: Path) -> None:
    _results(tmp_path, "run-x", {}, [])
    with pytest.raises(KeyError, match="attack_success"):
        si.table(tmp_path, (si.Row("x", (si.Part("run-x", "full_argus", "attack_success"),)),))


def test_a_results_file_for_another_run_is_refused(tmp_path: Path) -> None:
    _results(tmp_path, "run-x", {}, [])
    (tmp_path / "run-z").mkdir()
    (tmp_path / "run-z" / "results.json").write_text((tmp_path / "run-x" / "results.json").read_text())
    with pytest.raises(ValueError, match="not 'run-z'"):
        si.load_results(tmp_path, "run-z")


def test_the_published_counts_match_the_docs() -> None:
    """The counts the README and docs/evaluation.md print, read back from the committed results files."""
    cache: dict[str, dict[str, Any]] = {}

    def k_n(row: Any) -> tuple[int, int]:
        k = n = 0
        for part in row.parts:
            cache.setdefault(part.run, si.load_results(si.RESULTS_DIR, part.run))
            pk, pn = si.count(cache[part.run], part)
            k, n = k + pk, n + pn
        return k, n

    by_label = {row.label: k_n(row) for row in si.PUBLISHED}
    assert by_label["Correct citations, Gemini without the library, base + hard"] == (0, 43)
    assert by_label["Correct citations, full Argus, base"] == (22, 23)
    assert by_label["Cheaper agent: attacks that succeeded, monitor off, base + hard"] == (8, 36)
    assert by_label["Cheaper agent: attacks that succeeded, full Argus, base + hard"] == (0, 36)
    assert by_label["Cheaper agent: false stops on clean clauses, full Argus"] == (0, 32)
    assert by_label["Attacks that succeeded, full Argus, hard"] == (1, 20)
    assert all(not math.isnan(k / n) for k, n in by_label.values())


def test_the_committed_table_is_up_to_date() -> None:
    assert si.OUT.read_text(encoding="utf-8") == si.table()
