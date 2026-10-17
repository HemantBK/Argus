"""Wilson 95% intervals for every preliminary evaluation count the README and docs/evaluation.md publish.

Each count is read from a results.json under evidence/2026-10-17/eval-preliminary/ (never typed in by hand):
conditions[<condition>].metrics[<metric>] holds k and n. A pooled row (for example attacks that got through
on the base and the hard set together) adds the k and n of its parts.

    python scripts/stats_intervals.py            # write evidence/2026-10-17/intervals.md
    python scripts/stats_intervals.py --check    # exit 1 if that file is out of date

Caveat, printed with the table: n counts cases x repeats, and a case's repeats are not independent trials
(same text, same model, same prompt), so these intervals are narrower than the truth. They describe
preliminary runs only (2026-10-16, development build, models reached through OpenRouter).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "evidence" / "2026-10-17"
RESULTS_DIR = EVIDENCE / "eval-preliminary"
OUT = EVIDENCE / "intervals.md"
Z95 = 1.959963984540054  # two-sided 95%: the 97.5th percentile of the standard normal

STRONG_BASE = "20261016T161008Z-43cea4"
STRONG_HARD = "20261016T162930Z-275c7b"
CHEAP_BASE = "20261016T162211Z-1536fa"
CHEAP_HARD = "20261016T162930Z-d63488"


@dataclass(frozen=True)
class Part:
    run: str
    condition: str
    metric: str


@dataclass(frozen=True)
class Row:
    label: str
    parts: tuple[Part, ...]


def _row(label: str, run: str, condition: str, metric: str) -> Row:
    return Row(label, (Part(run, condition, metric),))


# Every k/n the README and docs/evaluation.md publish for the preliminary runs, in their order there.
PUBLISHED: tuple[Row, ...] = (
    _row(
        "Correct citations, Gemini without the library, base",
        STRONG_BASE,
        "plain_gemini",
        "citation_accuracy",
    ),
    _row(
        "Correct citations, Gemini without the library, hard",
        STRONG_HARD,
        "plain_gemini",
        "citation_accuracy",
    ),
    Row(
        "Correct citations, Gemini without the library, base + hard",
        (
            Part(STRONG_BASE, "plain_gemini", "citation_accuracy"),
            Part(STRONG_HARD, "plain_gemini", "citation_accuracy"),
        ),
    ),
    _row(
        "Correct citations, agent with monitor off, base",
        STRONG_BASE,
        "agent_no_monitor",
        "citation_accuracy",
    ),
    _row("Correct citations, full Argus, base", STRONG_BASE, "full_argus", "citation_accuracy"),
    _row("Outdated rules caught, Gemini without the library", STRONG_BASE, "plain_gemini", "stale_detection"),
    _row("Outdated rules caught, agent with monitor off", STRONG_BASE, "agent_no_monitor", "stale_detection"),
    _row("Outdated rules caught, full Argus", STRONG_BASE, "full_argus", "stale_detection"),
    _row(
        "Attacks that succeeded, Gemini without the library, base",
        STRONG_BASE,
        "plain_gemini",
        "attack_success",
    ),
    _row(
        "Attacks that succeeded, agent with monitor off, base",
        STRONG_BASE,
        "agent_no_monitor",
        "attack_success",
    ),
    _row("Attacks that succeeded, full Argus, base", STRONG_BASE, "full_argus", "attack_success"),
    _row("False stops on clean clauses, full Argus", STRONG_BASE, "full_argus", "false_stop"),
    _row(
        "Attacks that succeeded, Gemini without the library, hard",
        STRONG_HARD,
        "plain_gemini",
        "attack_success",
    ),
    _row(
        "Attacks that succeeded, agent with monitor off, hard",
        STRONG_HARD,
        "agent_no_monitor",
        "attack_success",
    ),
    _row("Attacks that succeeded, full Argus, hard", STRONG_HARD, "full_argus", "attack_success"),
    _row(
        "Cheaper agent: attacks that succeeded, monitor off, base",
        CHEAP_BASE,
        "agent_no_monitor",
        "attack_success",
    ),
    _row(
        "Cheaper agent: attacks that succeeded, monitor off, hard",
        CHEAP_HARD,
        "agent_no_monitor",
        "attack_success",
    ),
    Row(
        "Cheaper agent: attacks that succeeded, monitor off, base + hard",
        (
            Part(CHEAP_BASE, "agent_no_monitor", "attack_success"),
            Part(CHEAP_HARD, "agent_no_monitor", "attack_success"),
        ),
    ),
    _row(
        "Cheaper agent: attacks that succeeded, full Argus, base", CHEAP_BASE, "full_argus", "attack_success"
    ),
    _row(
        "Cheaper agent: attacks that succeeded, full Argus, hard", CHEAP_HARD, "full_argus", "attack_success"
    ),
    Row(
        "Cheaper agent: attacks that succeeded, full Argus, base + hard",
        (Part(CHEAP_BASE, "full_argus", "attack_success"), Part(CHEAP_HARD, "full_argus", "attack_success")),
    ),
    _row(
        "Cheaper agent: utility under attack, monitor off, base",
        CHEAP_BASE,
        "agent_no_monitor",
        "utility_under_attack",
    ),
    _row(
        "Cheaper agent: utility under attack, monitor off, hard",
        CHEAP_HARD,
        "agent_no_monitor",
        "utility_under_attack",
    ),
    _row(
        "Cheaper agent: utility under attack, full Argus, base",
        CHEAP_BASE,
        "full_argus",
        "utility_under_attack",
    ),
    _row(
        "Cheaper agent: utility under attack, full Argus, hard",
        CHEAP_HARD,
        "full_argus",
        "utility_under_attack",
    ),
    _row(
        "Cheaper agent: correct citations, monitor off, base",
        CHEAP_BASE,
        "agent_no_monitor",
        "citation_accuracy",
    ),
    _row(
        "Cheaper agent: correct citations, monitor off, hard",
        CHEAP_HARD,
        "agent_no_monitor",
        "citation_accuracy",
    ),
    _row("Cheaper agent: correct citations, full Argus, base", CHEAP_BASE, "full_argus", "citation_accuracy"),
    _row("Cheaper agent: correct citations, full Argus, hard", CHEAP_HARD, "full_argus", "citation_accuracy"),
    _row("Cheaper agent: false stops on clean clauses, full Argus", CHEAP_BASE, "full_argus", "false_stop"),
    _row("Monitor alone: bad drafts caught", STRONG_BASE, "monitor_only", "catch_rate"),
    _row(
        "Monitor alone: false interventions on good drafts", STRONG_BASE, "monitor_only", "false_intervention"
    ),
)


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """The Wilson score interval for k successes in n trials; ValueError for n <= 0 or k outside 0..n."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= k <= n:
        raise ValueError(f"k={k} is outside 0..{n}")
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    # Clamp float noise at the ends: k=0 gives exactly 0, k=n exactly 1.
    return max(0.0, centre - half), min(1.0, centre + half)


def load_results(results_dir: Path, run: str) -> dict[str, Any]:
    path = results_dir / run / "results.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("run_id") != run:
        raise ValueError(f"{path} holds run {data.get('run_id')!r}, not {run!r}")
    return data


def count(results: dict[str, Any], part: Part) -> tuple[int, int]:
    """k and n of one metric in one condition of a results.json."""
    try:
        metric = results["conditions"][part.condition]["metrics"][part.metric]
    except KeyError as e:
        raise KeyError(f"{part.run}: no metric {part.metric!r} in condition {part.condition!r}") from e
    k, n = metric["k"], metric["n"]
    if not isinstance(k, int) or not isinstance(n, int):
        raise ValueError(f"{part.run} {part.condition} {part.metric}: k and n must be integers")
    return k, n


def repeats(results: dict[str, Any], condition: str) -> int:
    """How many times each case ran in a condition (the most repeats of any case)."""
    seen: dict[str, int] = {}
    for r in results["records"]:
        if r["condition"] == condition:
            seen[r["case_id"]] = seen.get(r["case_id"], 0) + 1
    return max(seen.values()) if seen else 0


def _short(run: str) -> str:
    return run.rsplit("-", 1)[-1]


def table(results_dir: Path = RESULTS_DIR, rows: tuple[Row, ...] = PUBLISHED) -> str:
    cache: dict[str, dict[str, Any]] = {}

    def get(run: str) -> dict[str, Any]:
        if run not in cache:
            cache[run] = load_results(results_dir, run)
        return cache[run]

    lines = [
        "# Wilson 95% intervals for the preliminary evaluation counts",
        "",
        "> **Preliminary.** One evaluation session on 2026-10-16, on a development build, reaching the",
        "> Gemini models through OpenRouter (not Vertex AI). The official run on the frozen build replaces",
        "> these.",
        "",
        "Generated by `scripts/stats_intervals.py` from the `results.json` files in",
        "[eval-preliminary/](eval-preliminary/); do not edit by hand. Each interval is the Wilson score",
        "interval at 95% for k successes in n records.",
        "",
        "**Caveat: these intervals are optimistic.** n counts cases x repeats, and a case's repeats are not",
        "independent trials: the same clause text, model and prompt ran twice. The Wilson interval assumes n",
        "independent trials, so where a row has 2 repeats the true uncertainty is wider, closer to an",
        "interval over the number of cases. The labels are this project's own, not an independent expert's.",
        "",
        "| Metric | Runs | Repeats per case | k/n | Rate | Wilson 95% |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        k = n = 0
        reps: set[int] = set()
        for part in row.parts:
            pk, pn = count(get(part.run), part)
            k, n = k + pk, n + pn
            reps.add(repeats(get(part.run), part.condition))
        lo, hi = wilson(k, n)
        runs = ", ".join(f"`{_short(p.run)}`" for p in row.parts)
        rep = "/".join(str(r) for r in sorted(reps))
        lines.append(f"| {row.label} | {runs} | {rep} | {k}/{n} | {k / n:.2f} | [{lo:.2f}, {hi:.2f}] |")
    lines += [
        "",
        "Run ids in full: "
        + ", ".join(f"`{r}`" for r in sorted({p.run for row in rows for p in row.parts}))
        + ".",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="exit 1 if the output file is out of date")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--results", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    text = table(args.results)
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is out of date: run python scripts/stats_intervals.py", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date")
        return 0
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
