"""CLI: python -m argus_api.eval.run --conditions plain_gemini full_argus [--limit N] [--repeat 2] [--publish]

Loads and validates the case files against the corpus (any problem stops the run before a model is called),
runs each condition, computes the metrics and writes results/<run_id>/results.json and report.md. Firestore
is touched only with --publish. Settings come from the environment (settings.py); --env-file loads a .env
first without overriding variables already set.
"""

import argparse
import hashlib
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from argus_api.corpus.common import DEFAULT_OUT_DIR
from argus_api.corpus.store import Corpus
from argus_api.eval.dataset import (
    ATTACKS_FILE,
    BAD_DRAFTS_FILE,
    CASES_DIR,
    CLAUSES_FILE,
    BadDraft,
    EvalCase,
    load_bad_drafts,
    load_cases,
    validate_bad_drafts,
    validate_cases,
)
from argus_api.eval.metrics import Metric, condition_metrics
from argus_api.eval.plain import PLAIN_PROMPT_VERSION
from argus_api.eval.records import CASE_CONDITIONS, CONDITIONS, CaseRecord, Condition
from argus_api.eval.report import (
    RESULTS_DIR,
    FirestoreLike,
    build_results,
    git_info,
    publish_to_firestore,
    write_results,
)
from argus_api.eval.runner import EvalError, Reviewer, corpus_as_on, run_condition
from argus_api.eval.settings import EvalSettings, load_env_file
from argus_api.llm import LLM, build_llm
from argus_api.logs import configure_logging

REPO_DIR = Path(__file__).resolve().parents[3]


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m argus_api.eval.run", description=__doc__)
    p.add_argument("--conditions", nargs="+", choices=CONDITIONS, required=True)
    p.add_argument(
        "--cases",
        nargs="+",
        type=Path,
        default=[CASES_DIR / CLAUSES_FILE, CASES_DIR / ATTACKS_FILE],
        help="clause/attack case files (default: cases/clauses.json cases/attacks.json)",
    )
    p.add_argument("--bad-drafts", type=Path, default=CASES_DIR / BAD_DRAFTS_FILE)
    p.add_argument("--limit", type=int, default=None, help="first N cases (and first N bad drafts)")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument(
        "--publish",
        action="store_true",
        help="also write the summary to Firestore eval_results/latest (never by default)",
    )
    p.add_argument("--env-file", type=Path, default=None)
    p.add_argument("--corpus-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    args = p.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        p.error("--limit must be at least 1")
    if args.repeat < 1 or args.workers < 1:
        p.error("--repeat and --workers must be at least 1")
    if len(set(args.conditions)) != len(args.conditions):
        p.error("a condition is listed twice")
    return args


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_DIR))
    except ValueError:
        return str(path)


def default_firestore(es: EvalSettings) -> FirestoreLike:
    from google.cloud import firestore

    client: FirestoreLike = firestore.Client(project=es.settings.project)
    return client


def main(
    argv: list[str] | None = None,
    *,
    settings: EvalSettings | None = None,
    llm: LLM | None = None,
    corpus: Corpus | None = None,
    reviewer: Reviewer | None = None,
    firestore_factory: Callable[[EvalSettings], FirestoreLike] = default_firestore,
) -> Path:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(raw_argv)
    conditions: list[Condition] = list(args.conditions)
    if args.env_file is not None:
        load_env_file(args.env_file)
    es = settings if settings is not None else EvalSettings.from_env()
    corpus = corpus if corpus is not None else Corpus.load(args.corpus_dir)

    case_files: dict[str, str] = {}
    cases: list[EvalCase] = []
    bad_drafts: list[BadDraft] = []
    if any(c in CASE_CONDITIONS for c in conditions):
        for path in args.cases:
            cases += load_cases(path)
            case_files[_display(path)] = _sha256(path)
        problems = validate_cases(cases, corpus)
        if problems:
            raise EvalError("the case files have problems:\n" + "\n".join(str(p) for p in problems))
        cases = cases[: args.limit]
    if "monitor_only" in conditions:
        bad_drafts = load_bad_drafts(args.bad_drafts)
        case_files[_display(args.bad_drafts)] = _sha256(args.bad_drafts)
        problems = validate_bad_drafts(bad_drafts, corpus)
        if problems:
            raise EvalError("the bad drafts have problems:\n" + "\n".join(str(p) for p in problems))
        bad_drafts = bad_drafts[: args.limit]

    configure_logging()
    started = datetime.now(UTC)
    run_id = f"{started:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    model_client = llm if llm is not None else build_llm(es.settings)

    records: list[CaseRecord] = []
    metrics: dict[str, dict[str, Metric]] = {}
    for cond in conditions:
        recs = run_condition(
            cond,
            llm=model_client,
            es=es,
            corpus=corpus,
            cases=cases,
            bad_drafts=bad_drafts,
            repeat=args.repeat,
            run_id=run_id,
            reviewer=reviewer,
            workers=args.workers,
        )
        records += recs
        metrics[cond] = condition_metrics(cond, recs)

    from argus_api.agent.prompts import AGENT_PROMPT_VERSION
    from argus_api.monitor.prompts import MONITOR_PROMPT_VERSION

    results: dict[str, Any] = build_results(
        run_id=run_id,
        run_date=started.isoformat(timespec="seconds"),
        argv=["python", "-m", "argus_api.eval.run", *raw_argv],
        models={
            "provider": es.provider,
            "agent_model": es.agent_model,
            "monitor_model": es.monitor_model,
            "pinned": es.pinned,
            "agent_thinking": es.settings.agent_thinking,
            "monitor_thinking": es.settings.monitor_thinking,
        },
        git=git_info(REPO_DIR),
        corpus_info={
            "snapshot_date": corpus_as_on(corpus).isoformat(),
            "instruments": {ref: inst.page_sha256 for ref, inst in sorted(corpus.instruments.items())},
            "paragraphs": len(corpus.paragraphs),
        },
        case_files=case_files,
        prompt_versions={
            "plain": PLAIN_PROMPT_VERSION,
            "agent": AGENT_PROMPT_VERSION,
            "monitor": MONITOR_PROMPT_VERSION,
        },
        records=records,
        metrics=metrics,
        gold_citations={
            c.case_id: (
                f"gold citation para {c.gold_citation.para} of {c.gold_citation.instrument_ref}"
                if c.gold_citation is not None
                else "no gold citation"
            )
            for c in cases
        },
    )
    out = write_results(results, args.results_dir)
    print(f"eval results written to {out}", file=sys.stderr)
    if args.publish:
        where = publish_to_firestore(firestore_factory(es), results)
        print(f"eval summary published to Firestore {where}", file=sys.stderr)
    return out


if __name__ == "__main__":
    main()
