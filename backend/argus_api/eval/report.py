"""Write an evaluation run to results/<run_id>/: results.json (everything) and report.md (for people).

The report gives conditions x metrics with n, the model ids, the git SHA (and whether the tree had
uncommitted changes), the corpus snapshot date, the run date and three failure cases in plain English.
publish_to_firestore writes the summary (no per-case records) to eval_results/latest; the CLI calls it only
with --publish, after the numbers have been reviewed.
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Protocol

from argus_api.eval.metrics import Metric, attack_succeeded
from argus_api.eval.records import CONDITIONS, CaseRecord

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_SCHEMA = "argus-eval-v1"
FIRESTORE_COLLECTION = "eval_results"
FIRESTORE_DOC = "latest"
FAILURE_CASES = 3
# Which condition's failures the report shows first: the full system's matter most.
FAILURE_PREFERENCE = ("full_argus", "monitor_only", "agent_no_monitor", "plain_gemini")


class ReportError(RuntimeError):
    pass


def git_info(repo_dir: Path) -> dict[str, Any]:
    """HEAD's SHA and whether the working tree has uncommitted changes. Fails loudly without git."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        raise ReportError(f"cannot read the git state of {repo_dir}: {e}") from e
    return {"sha": sha, "dirty": bool(status.strip())}


def _fmt(name: str, m: dict[str, Any]) -> str:
    value, n, k = m["value"], m["n"], m.get("k")
    if value is None:
        return f"n/a (n={n})"
    if name.endswith("_ms"):
        return f"{value:.0f} ms (n={n})"
    if name.endswith("_usd"):
        return f"${value:.5f} (n={n})"
    if k is None:
        return f"{value:.2f} (n={n})"
    return f"{value:.2f} ({k}/{n})"


def _describe_citation(r: dict[str, Any]) -> str:
    c = r.get("predicted_citation")
    if c is None:
        return "gave no citation"
    a = r.get("citation_assessment")
    where = f"cited para {c['para']} of {c['instrument_ref']}"
    if a is None:
        return where
    faults = []
    if not a["exists"]:
        faults.append("that paragraph is not in the library")
    else:
        if not a["in_force"]:
            faults.append("that instrument is not in force")
        if not a["quote_verified"]:
            faults.append("the quote is not verbatim")
        if not a["same_para_as_gold"]:
            faults.append("it is not the gold paragraph")
    return where + (f" ({'; '.join(faults)})" if faults else " (correct)")


def describe_failure(r: dict[str, Any], gold_citations: dict[str, str]) -> str:
    cid, cond = r["case_id"], r["condition"]
    if r["kind"] == "bad_draft":
        got = "failed" if r["error"] else f"answered {r['final_action']}"
        checks = ", ".join(r["failed_checks"]) or "none"
        return (
            f"Bad draft {cid} ({cond}): the monitor should have answered {r['expected_monitor_action']} "
            f"(failing {r['expected_failed_check'] or 'no check'}) but {got}; failed checks: {checks}."
        )
    what = "attack case" if r["kind"] == "attack" else "clause case"
    if r["error"]:
        outcome = f"the review failed ({r['error'][:200]})"
    else:
        outcome = f"it labelled the clause {r['predicted_label']} and {_describe_citation(r)}"
        if r.get("first_intervention_step") is not None:
            outcome += (
                f"; the monitor first said {r['first_intervention_action']} "
                f"at step {r['first_intervention_step']}"
            )
    gold = gold_citations.get(cid, "no gold citation")
    return f"{what.capitalize()} {cid} ({cond}): the gold label is {r['gold_label']} ({gold}), but {outcome}."


def _is_failure(r: CaseRecord) -> bool:
    if r.kind == "bad_draft":
        return r.error is not None or r.final_action != r.expected_monitor_action
    if r.kind == "attack" and attack_succeeded(r):
        return True
    return not r.label_correct or (r.citation_assessment is not None and not r.citation_assessment.accurate)


def choose_failures(records: list[CaseRecord], limit: int = FAILURE_CASES) -> list[CaseRecord]:
    """Up to `limit` failing records, distinct cases, the full system's first, in case order."""
    out: list[CaseRecord] = []
    seen: set[str] = set()
    for cond in FAILURE_PREFERENCE:
        for r in records:
            if r.condition == cond and r.case_id not in seen and _is_failure(r):
                out.append(r)
                seen.add(r.case_id)
                if len(out) == limit:
                    return out
    return out


def build_results(
    *,
    run_id: str,
    run_date: str,
    argv: list[str],
    models: dict[str, Any],
    git: dict[str, Any],
    corpus_info: dict[str, Any],
    case_files: dict[str, str],
    prompt_versions: dict[str, str],
    records: list[CaseRecord],
    metrics: dict[str, dict[str, Metric]],
    gold_citations: dict[str, str],
) -> dict[str, Any]:
    dumped = [r.model_dump(mode="json") for r in records]
    failures = choose_failures(records)
    return {
        "schema": RESULTS_SCHEMA,
        "run_id": run_id,
        "run_date": run_date,
        "argv": argv,
        "models": models,
        "git": git,
        "corpus": corpus_info,
        "case_files": case_files,
        "prompt_versions": prompt_versions,
        "conditions": {
            cond: {
                "records": sum(1 for r in records if r.condition == cond),
                "metrics": {name: m.as_dict() for name, m in ms.items()},
            }
            for cond, ms in metrics.items()
        },
        "failure_cases": [describe_failure(r.model_dump(mode="json"), gold_citations) for r in failures],
        "records": dumped,
    }


def render_report(results: dict[str, Any]) -> str:
    conds = [c for c in CONDITIONS if c in results["conditions"]]
    lines = [
        f"# Argus evaluation {results['run_id']}",
        "",
        "Unreviewed measurement. No number here goes into the app, deck, README or video until it has been "
        "reviewed (PLAN section 7).",
        "",
        f"- Run date: {results['run_date']}",
        f"- Git: {results['git']['sha']}"
        + (" (uncommitted changes present)" if results["git"]["dirty"] else ""),
        f"- Provider: {results['models']['provider']}; agent model {results['models']['agent_model']}; "
        f"monitor model {results['models']['monitor_model']}"
        + ("" if results["models"]["pinned"] else " (NOT the pinned config.PINNED_MODELS ids)"),
        f"- Corpus snapshot date: {results['corpus']['snapshot_date']}",
        f"- Prompt versions: {', '.join(f'{k} {v}' for k, v in results['prompt_versions'].items())}",
        f"- Command: {' '.join(results['argv'])}",
        "",
        "Rates show k/n; other metrics show (n=...). n counts case x repeat records. n/a means n is 0.",
        "",
    ]
    names: list[str] = []
    for c in conds:
        for name in results["conditions"][c]["metrics"]:
            if name not in names:
                names.append(name)
    headers = [f"{c} ({results['conditions'][c]['records']} records)" for c in conds]
    lines.append("| metric | " + " | ".join(headers) + " |")
    lines.append("|---|" + "---|" * len(conds))
    for name in names:
        cells = []
        for c in conds:
            m = results["conditions"][c]["metrics"].get(name)
            cells.append("-" if m is None else _fmt(name, m))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "## Failure cases", ""]
    if results["failure_cases"]:
        lines += [f"{i}. {text}" for i, text in enumerate(results["failure_cases"], start=1)]
    else:
        lines.append("No failing record in this run.")
    lines.append("")
    return "\n".join(lines)


def write_results(results: dict[str, Any], root: Path = RESULTS_DIR) -> Path:
    out = root / str(results["run_id"])
    if out.exists():
        raise ReportError(f"{out} already exists; run ids are unique per run")
    out.mkdir(parents=True)
    (out / "results.json").write_text(
        json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "report.md").write_text(render_report(results), encoding="utf-8")
    return out


class FirestoreLike(Protocol):
    def collection(self, collection_id: str) -> Any: ...


def publish_to_firestore(client: FirestoreLike, results: dict[str, Any]) -> str:
    """Write the run's summary (everything but the per-case records) to eval_results/latest."""
    summary = {k: v for k, v in results.items() if k != "records"}
    client.collection(FIRESTORE_COLLECTION).document(FIRESTORE_DOC).set(summary)
    return f"{FIRESTORE_COLLECTION}/{FIRESTORE_DOC}"
