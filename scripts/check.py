#!/usr/bin/env python3
"""Automatic rule checks for Argus. The script decides every status; nobody types PASS by hand.

Run from the repository root:

    backend/.venv/bin/python scripts/check.py commit [--fast]
    backend/.venv/bin/python scripts/check.py deploy --strict [--expect-sha SHA] [--allow-min-zero]
    backend/.venv/bin/python scripts/check.py submission --submission submission.json   # or ../SUBMISSION.md
    backend/.venv/bin/python scripts/check.py rules-coverage [--rules-file RULES.md]

Each check prints one line: ID, the rule IDs it covers, a status and a one-line reason.
Statuses: PASS, FAIL, WARN (looks wrong but is not a rule breach), HUMAN (a person must confirm) and
BLOCKED (the check could not run: a missing tool, project or input). BLOCKED is never PASS.
The exit code is 1 when any check FAILs (with --strict, also when any is BLOCKED), otherwise 0.

Rule IDs: E-/T-/B-/S- are the competition rules (eligibility, team, build, submission); ENG-En are the items
of the engineering standard; G1..G10 are the verification gates.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
HUMAN = "HUMAN"
BLOCKED = "BLOCKED"
STATUSES = (PASS, FAIL, WARN, HUMAN, BLOCKED)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
EARLIEST_COMMIT_DATE = dt.date(2026, 10, 16)  # B-04: hackathon launch, IST
VERTEX_ONLY_TEST = "tests/test_config.py::test_cloud_mode_is_vertex_only"
TEMPLATE_INSTRUCTION = "Kindly refrain from sending edit requests"
REQUIRED_TECH = ("Firebase", "Firestore", "Cloud Run", "Gemini")
FORBIDDEN_CLOUD_ENV = (
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "FIREBASE_AUTH_EMULATOR_HOST",
    "ARGUS_AGENT_MODEL",
    "ARGUS_MONITOR_MODEL",
)
EVIL_ORIGIN = "https://evil.example"
HTTP_TIMEOUT_S = 20
USER_AGENT = "argus-check/1.0 (+https://github.com)"

# Every competition rule (RULES.md sections A-D). rules-coverage fails if one is neither checked nor HUMAN.
RULES: dict[str, str] = {
    "E-01": "every participant is at least 21",
    "E-02": "working professionals only, no students",
    "E-03": "one student disqualifies the whole team",
    "E-04": "organisers may verify ID, employment and age",
    "E-05": "every member based in and resident of a JAPAC country",
    "E-06": "only shortlisted participants take part",
    "T-01": "team of 2 to 4 members",
    "T-02": "team formation closed Oct 16, no roster changes",
    "T-03": "a person is on only one team",
    "T-04": "one submission per team, one theme",
    "T-05": "members stay on the event channels",
    "T-06": "only 2 members travel to the finale",
    "B-01": "uses Google AI models (Gemini)",
    "B-02": "deployed on Google Cloud, no other cloud in the serving path",
    "B-03": "uses Firebase, Firestore, Cloud Run and Gemini",
    "B-04": "only new work built during the hackathon",
    "B-05": "everything in English",
    "B-06": "addresses the BFSI problem statement",
    "B-07": "sole authors, no third-party infringement",
    "B-08": "no objectionable content",
    "B-09": "no personal data breach; synthetic data only",
    "S-01": "pitch deck on the template, as PDF",
    "S-02": "demo video 3-4 minutes, public, shows the working solution",
    "S-03": "deployed prototype link public and working through evaluation",
    "S-04": "public GitHub repository",
    "S-05": "description explains Firebase, Firestore, Cloud Run and Gemini",
    "S-06": "all submission links public and working",
    "S-07": "problem statement in the submission (slide 1)",
    "S-08": "submitted before the deadline",
}

# What only a person can confirm. Stages that print them: submission prints all, deploy prints "deploy" ones.
HUMAN_ITEMS: list[tuple[str, tuple[str, ...], str, tuple[str, ...]]] = [
    (
        "H-ELIGIBILITY",
        ("E-01", "E-02", "E-03", "E-05"),
        "each member confirms in writing: 21+, not a "
        "student (LinkedIn shows no current student status), lives in a JAPAC country",
        ("submission",),
    ),
    (
        "H-ID-READY",
        ("E-04",),
        "each member has government ID, age and employment proof ready",
        ("submission",),
    ),
    (
        "H-SHORTLISTED",
        ("E-06", "T-01", "T-02"),
        "Team Management shows 2-4 members, all joined (not INVITED), roster unchanged since Oct 16",
        ("submission",),
    ),
    (
        "H-ONE-TEAM",
        ("T-03", "T-04"),
        "each member is on no other team; one submission, theme BFSI",
        ("submission",),
    ),
    ("H-CHANNELS", ("T-05",), "all members watch Discord and email until Dec 4", ("submission",)),
    (
        "H-FINALE",
        ("T-06",),
        "only if shortlisted (Nov 7): at most 2 travellers, visas on them",
        ("submission",),
    ),
    (
        "H-REAL-USE",
        ("B-01", "B-03"),
        "Gemini, Firebase, Firestore and Cloud Run each do real work visible in "
        "the code and the architecture slide",
        ("submission",),
    ),
    (
        "H-NO-COPIED-CODE",
        ("B-04",),
        "no code copied from earlier repos (when-to-stop, StepShield, ...)",
        ("submission",),
    ),
    ("H-ENGLISH", ("B-05",), "code, docs, deck and video are in English", ("submission",)),
    (
        "H-PROBLEM-FIT",
        ("B-06",),
        "slide 1 and README map the problem to BFSI compliance automation / document intelligence",
        ("submission",),
    ),
    (
        "H-LICENCES",
        ("B-07",),
        "only permissively licensed dependencies; RBI sources cited; no copied assets",
        ("submission",),
    ),
    ("H-CONTENT", ("B-08",), "nothing offensive or off-brand in demo data, UI or video", ("submission",)),
    (
        "H-NO-PERSONAL-DATA",
        ("B-09",),
        "no real personal data anywhere in the demo, video or Firestore",
        ("submission",),
    ),
    ("H-TEMPLATE", ("S-01",), "deck built from deck/template.pptx in the prescribed order", ("submission",)),
    (
        "H-VIDEO-CONTENT",
        ("S-02",),
        "video records the live deployed app working, not mock screens",
        ("submission",),
    ),
    (
        "H-UPTIME",
        ("S-03",),
        "uptime check scheduled and watched until Nov 6; nobody redeploys a broken build",
        ("deploy", "submission"),
    ),
    (
        "H-DEADLINE",
        ("S-08",),
        "submitted on the dashboard before Oct 18 11:59 PM IST, receipt screenshot kept",
        ("submission",),
    ),
]


# --------------------------------------------------------------------------------------------- data types
@dataclasses.dataclass
class CmdRun:
    command: str
    cwd: str
    exit_code: int | None
    output: str
    started_utc: str
    duration_s: float
    store_raw: bool = True

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.output.encode("utf-8", "replace")).hexdigest()

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclasses.dataclass
class Result:
    check_id: str
    rules: tuple[str, ...]
    status: str
    reason: str
    details: list[str] = dataclasses.field(default_factory=list)
    runs: list[CmdRun] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class Check:
    check_id: str
    rules: tuple[str, ...]
    fn: Callable[[Ctx], Result]
    slow: bool = False


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact(secret: str) -> str:
    return secret[:6] + "..." + f"[{len(secret)} chars redacted]"


# --------------------------------------------------------------------------------------------- context
class Ctx:
    """Everything a check needs: the repo, the tools, the inputs, and a log of every command it ran."""

    def __init__(
        self,
        repo: Path,
        *,
        venv_bin: Path | None = None,
        node_dir: Path | None = None,
        args: argparse.Namespace | None = None,
        timeout_s: int = 1800,
    ) -> None:
        self.repo = repo.resolve()
        self.venv_bin = venv_bin if venv_bin is not None else self.repo / "backend" / ".venv" / "bin"
        self.node_dir = node_dir
        self.args = args if args is not None else argparse.Namespace()
        self.timeout_s = timeout_s
        self.runs: list[CmdRun] = []
        self._blobs: dict[str, bytes] | None = None
        self._submission: dict[str, Any] | None = None

    # ---- commands
    def tool_env(self, *, offline: bool = False) -> dict[str, str]:
        env = dict(os.environ)
        if self.node_dir is not None:
            env["PATH"] = f"{self.node_dir}{os.pathsep}{env.get('PATH', '')}"
        if offline:
            # Tests run without keys, emulators or git-hook state, and any outbound HTTP goes to a dead proxy.
            for name in (
                "OPENROUTER_API_KEY",
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "GOOGLE_APPLICATION_CREDENTIALS",
                "FIRESTORE_EMULATOR_HOST",
                "FIREBASE_AUTH_EMULATOR_HOST",
                "GIT_DIR",
                "GIT_INDEX_FILE",
                "GIT_WORK_TREE",
                "GIT_PREFIX",
            ):
                env.pop(name, None)
            dead = "http://127.0.0.1:9"
            env.update(
                HTTP_PROXY=dead,
                HTTPS_PROXY=dead,
                http_proxy=dead,
                https_proxy=dead,
                ALL_PROXY=dead,
                NO_PROXY="localhost,127.0.0.1,::1,testserver",
                no_proxy="localhost,127.0.0.1,::1,testserver",
                NODE_USE_ENV_PROXY="1",
            )
        return env

    def run(
        self,
        argv: list[str],
        cwd: Path | None = None,
        *,
        env: dict[str, str] | None = None,
        timeout_s: int | None = None,
        store_raw: bool = True,
        input_text: str | None = None,
    ) -> CmdRun:
        cwd = cwd or self.repo
        started = utc_now()
        t0 = time.monotonic()
        command = " ".join(_quote(a) for a in argv)
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                env=env if env is not None else self.tool_env(),
                input=input_text,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout_s or self.timeout_s,
                check=False,
            )
            code: int | None = proc.returncode
            output = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        except FileNotFoundError as exc:
            code, output = None, f"not found: {exc.filename}"
        except subprocess.TimeoutExpired:
            code, output = None, f"timed out after {timeout_s or self.timeout_s} s"
        run = CmdRun(command, str(cwd), code, output, started, round(time.monotonic() - t0, 2), store_raw)
        self.runs.append(run)
        return run

    def internal(self, name: str, output: str) -> None:
        """Record an in-process scan as evidence, like a command."""
        self.runs.append(CmdRun(f"internal:{name}", str(self.repo), 0, output, utc_now(), 0.0))

    def git(self, *args: str, store_raw: bool = True) -> CmdRun:
        return self.run(["git", *args], self.repo, env=dict(os.environ), store_raw=store_raw)

    # ---- tools
    def venv_tool(self, name: str) -> Path | None:
        path = self.venv_bin / name
        return path if path.exists() else None

    def node_bin(self) -> Path | None:
        if self.node_dir is not None and (self.node_dir / "node").exists():
            return self.node_dir / "node"
        found = shutil.which("node")
        return Path(found) if found else None

    # ---- the index (what will be committed)
    def tracked(self) -> list[str]:
        return sorted(self.blobs())

    def blobs(self) -> dict[str, bytes]:
        if self._blobs is None:
            self._blobs = read_index_blobs(self.repo)
        return self._blobs

    def text(self, path: str) -> str | None:
        data = self.blobs().get(path)
        if data is None or b"\0" in data[:8192]:
            return None
        return data.decode("utf-8", "replace")

    # ---- submission inputs
    def submission(self) -> dict[str, Any]:
        if self._submission is None:
            path = getattr(self.args, "submission", None)
            if not path:
                raise Blocked("no --submission JSON given")
            p = Path(path)
            if not p.is_file():
                raise Blocked(f"submission file not found: {p}")
            if p.suffix.lower() == ".md":
                # A submission text (SUBMISSION.md) on its own: it is the description, nothing else is known.
                self._submission = {"description_file": p.name, "_base": str(p.resolve().parent)}
                return self._submission
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise Blocked("submission JSON must be an object")
            data["_base"] = str(p.resolve().parent)
            self._submission = data
        return self._submission

    def sub_path(self, key: str) -> Path | None:
        value = self.submission().get(key)
        if not value:
            return None
        p = Path(str(value)).expanduser()
        return p if p.is_absolute() else Path(self.submission()["_base"]) / p


class Blocked(Exception):
    """Raised inside a check when it cannot run."""


def _quote(arg: str) -> str:
    return arg if re.fullmatch(r"[\w@%+=:,./-]+", arg) else "'" + arg.replace("'", "'\\''") + "'"


def read_index_blobs(repo: Path) -> dict[str, bytes]:
    """Content of every tracked file as staged in the index, so a pre-commit run sees what is committed."""
    env = dict(os.environ)
    ls = subprocess.run(["git", "ls-files", "-z"], cwd=repo, env=env, capture_output=True, check=True)
    paths = [p for p in ls.stdout.decode("utf-8", "replace").split("\0") if p]
    if not paths:
        return {}
    request = "".join(f":{p}\n" for p in paths).encode()
    cat = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=repo, env=env, input=request, capture_output=True, check=True
    )
    out, pos, blobs = cat.stdout, 0, {}
    for path in paths:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        if len(header) == 2 and header[1] == "missing":
            continue
        size = int(header[2])
        if header[1] == "blob":
            blobs[path] = out[pos : pos + size]
        pos += size + 1
    return blobs


# --------------------------------------------------------------------------------------------- path rules
def is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if any(d in {"tests", "tests_emulator", "e2e", "__tests__", "fixtures"} for d in parts[:-1]):
        return True
    if name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py":
        return True
    return bool(re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", name) or ".test-util." in name)


def is_seed_path(path: str) -> bool:
    return Path(path).name.startswith("seed")


def forbidden_reason(path: str) -> str | None:
    parts = path.split("/")
    name = parts[-1]
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "environment file"
    caches = {".mypy_cache", ".pytest_cache", ".ruff_cache", "node_modules", "dist", "__pycache__", ".venv"}
    hit = caches.intersection(parts[:-1])
    if hit:
        return f"cache/build directory {sorted(hit)[0]}"
    if path.startswith("backend/data/corpus/"):
        return "RBI corpus (rebuilt by script, never committed)"
    if path.startswith("backend/argus_api/eval/results/"):
        return "evaluation output"
    if name.lower().endswith(".pdf"):
        return "PDF"
    if name in {"RULES.md", "STATUS.md", "DECISIONS.md"}:
        return "private planning file"
    if {"council", "research"}.intersection(parts[:-1]):
        return "private council/research material"
    return None


SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("openrouter-key", re.compile(r"sk-or-[A-Za-z0-9_\-]{16,}")),
    ("openai-project-key", re.compile(r"sk-proj-[A-Za-z0-9_\-]{16,}")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai-key", re.compile(r"sk-[A-Za-z0-9]{40,}")),
    ("pem-private-key", re.compile(r"-{5}BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-{5}")),
    ("gcp-service-account", re.compile(r"\"type\"\s*:\s*\"service_account\"")),
]


def find_secrets(text: str) -> list[tuple[str, int, str]]:
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pattern in SECRET_PATTERNS:
            for m in pattern.finditer(line):
                hits.append((name, lineno, m.group(0)))
    return hits


# --------------------------------------------------------------------------------------------- commit checks
def _cmd_result(ctx: Ctx, check_id: str, rules: tuple[str, ...], runs: list[CmdRun], what: str) -> Result:
    missing = [r for r in runs if r.exit_code is None and r.output.startswith("not found")]
    if missing:
        return Result(check_id, rules, BLOCKED, f"{what}: {missing[0].output}")
    bad = [r for r in runs if not r.ok]
    if bad:
        tail = [ln for ln in bad[0].output.strip().splitlines() if ln.strip()][-12:]
        return Result(
            check_id, rules, FAIL, f"{what} failed: {bad[0].command} (exit {bad[0].exit_code})", tail
        )
    return Result(check_id, rules, PASS, f"{what} clean ({len(runs)} command{'s' if len(runs) > 1 else ''})")


def _needs_backend(ctx: Ctx, tool: str) -> Path:
    if not (ctx.repo / "backend").is_dir():
        raise Blocked("no backend/ directory")
    exe = ctx.venv_tool(tool)
    if exe is None:
        raise Blocked(f"{tool} not found in {ctx.venv_bin} (run make setup)")
    return exe


def _scripts_present(ctx: Ctx) -> bool:
    return (ctx.repo / "scripts" / "check.py").is_file()


def check_py_lint(ctx: Ctx) -> Result:
    rules = ("ENG-E6", "G1")
    ruff = _needs_backend(ctx, "ruff")
    runs = [ctx.run([str(ruff), "check", "."], ctx.repo / "backend")]
    if _scripts_present(ctx):
        runs.append(ctx.run([str(ruff), "check", "scripts"], ctx.repo))
    return _cmd_result(ctx, "C-PY-LINT", rules, runs, "ruff check")


def check_py_format(ctx: Ctx) -> Result:
    rules = ("ENG-E6", "G1")
    ruff = _needs_backend(ctx, "ruff")
    runs = [ctx.run([str(ruff), "format", "--check", "."], ctx.repo / "backend")]
    if _scripts_present(ctx):
        runs.append(ctx.run([str(ruff), "format", "--check", "scripts"], ctx.repo))
    return _cmd_result(ctx, "C-PY-FORMAT", rules, runs, "ruff format --check")


def check_py_types(ctx: Ctx) -> Result:
    rules = ("ENG-E6", "G1")
    mypy = _needs_backend(ctx, "mypy")
    runs = [ctx.run([str(mypy), "argus_api"], ctx.repo / "backend")]
    if _scripts_present(ctx):
        runs.append(
            ctx.run([str(mypy), "--strict", "--python-version", "3.11", "scripts/check.py"], ctx.repo)
        )
    return _cmd_result(ctx, "C-PY-TYPES", rules, runs, "mypy")


def _needs_web(ctx: Ctx, binary: str) -> Path:
    web = ctx.repo / "web"
    if not (web / "package.json").is_file():
        raise Blocked("no web/package.json")
    if ctx.node_bin() is None:
        raise Blocked("node not found (pass --node-dir or put node on PATH)")
    exe = web / "node_modules" / ".bin" / binary
    if not exe.exists():
        raise Blocked(f"web/node_modules/.bin/{binary} missing (run npm ci in web/)")
    return exe


def check_web_types(ctx: Ctx) -> Result:
    tsc = _needs_web(ctx, "tsc")
    run = ctx.run([str(tsc), "--noEmit"], ctx.repo / "web")
    return _cmd_result(ctx, "C-WEB-TYPES", ("ENG-E6", "G1"), [run], "tsc --noEmit")


def check_web_lint(ctx: Ctx) -> Result:
    eslint = _needs_web(ctx, "eslint")
    run = ctx.run([str(eslint), ".", "--max-warnings", "0"], ctx.repo / "web")
    return _cmd_result(ctx, "C-WEB-LINT", ("ENG-E6", "G1"), [run], "eslint --max-warnings 0")


def check_py_tests(ctx: Ctx) -> Result:
    rules = ("ENG-E5", "G2")
    python = _needs_backend(ctx, "python")
    with tempfile.TemporaryDirectory(prefix="argus-check-") as tmp:
        junit = Path(tmp) / "junit.xml"
        run = ctx.run(
            [str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
            ctx.repo / "backend",
            env=ctx.tool_env(offline=True),
        )
        counts = _junit_counts(junit)
    if run.exit_code is None:
        return Result("C-PY-TESTS", rules, FAIL, f"pytest did not finish: {run.output[:120]}")
    summary = (
        f"{counts.get('tests', 0)} tests, {counts.get('failures', 0)} failed, "
        f"{counts.get('errors', 0)} errors, {counts.get('skipped', 0)} skipped (offline)"
    )
    if run.exit_code != 0 or counts.get("tests", 0) == 0:
        tail = [ln for ln in run.output.strip().splitlines() if ln.strip()][-15:]
        return Result("C-PY-TESTS", rules, FAIL, f"pytest exit {run.exit_code}: {summary}", tail)
    return Result("C-PY-TESTS", rules, PASS, f"pytest: {summary}")


def _junit_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for s in suites:
        for key in totals:
            totals[key] += int(s.get(key, "0"))
    return totals


def check_web_tests(ctx: Ctx) -> Result:
    rules = ("ENG-E5", "G2")
    vitest = _needs_web(ctx, "vitest")
    with tempfile.TemporaryDirectory(prefix="argus-check-") as tmp:
        report = Path(tmp) / "vitest.json"
        run = ctx.run(
            [str(vitest), "run", "--reporter=dot", "--reporter=json", f"--outputFile.json={report}"],
            ctx.repo / "web",
            env=ctx.tool_env(offline=True),
        )
        data = json.loads(report.read_text()) if report.is_file() else {}
    total, failed = int(data.get("numTotalTests", 0)), int(data.get("numFailedTests", 0))
    summary = f"{total} tests, {failed} failed, {data.get('numPendingTests', 0)} skipped (offline)"
    if run.exit_code != 0 or total == 0:
        tail = [ln for ln in run.output.strip().splitlines() if ln.strip()][-15:]
        return Result("C-WEB-TESTS", rules, FAIL, f"vitest exit {run.exit_code}: {summary}", tail)
    return Result("C-WEB-TESTS", rules, PASS, f"vitest: {summary}")


REQ_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9._,-]+\])?==(?P<ver>[A-Za-z0-9._+!-]+)\s*(;[^#]*)?(#.*)?$"
)
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def _exact_npm(spec: str) -> bool:
    if spec.startswith("npm:"):
        spec = spec.rsplit("@", 1)[-1]
    return bool(EXACT_VERSION.match(spec))


def check_deps_pinned(ctx: Ctx) -> Result:
    rules = ("ENG-E1", "G1")
    problems: list[str] = []
    pins: dict[str, dict[str, str]] = {}
    req_files = [p for p in ctx.tracked() if re.fullmatch(r"requirements.*\.txt", Path(p).name)]
    for path in req_files:
        for lineno, raw in enumerate((ctx.text(path) or "").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#") or re.match(r"^-[rc]\s+\S+$", line):
                continue
            m = REQ_LINE.match(line)
            if not m:
                problems.append(f"{path}:{lineno}: not pinned with ==: {line}")
                continue
            pins.setdefault(m["name"].lower().replace("_", "-"), {})[path] = m["ver"]
    for name, where in sorted(pins.items()):
        if len(set(where.values())) > 1:
            problems.append(f"{name} pinned to different versions: {where}")
    pkg_files = [p for p in ctx.tracked() if Path(p).name == "package.json" and "node_modules" not in p]
    for path in pkg_files:
        try:
            pkg = json.loads(ctx.text(path) or "{}")
        except json.JSONDecodeError as exc:
            problems.append(f"{path}: invalid JSON ({exc})")
            continue
        for section in ("dependencies", "devDependencies", "optionalDependencies", "overrides"):
            for dep, spec in _flatten_specs(pkg.get(section) or {}):
                if not _exact_npm(spec):
                    problems.append(f"{path} {section}.{dep} is not an exact version: {spec!r}")
        lock_path = str(Path(path).parent / "package-lock.json") if "/" in path else "package-lock.json"
        if lock_path not in ctx.blobs():
            problems.append(f"{lock_path} is not tracked")
            continue
        try:
            lock = json.loads(ctx.text(lock_path) or "{}")
        except json.JSONDecodeError as exc:
            problems.append(f"{lock_path}: invalid JSON ({exc})")
            continue
        root = (lock.get("packages") or {}).get("") or {}
        for section in ("dependencies", "devDependencies"):
            if (root.get(section) or {}) != (pkg.get(section) or {}):
                problems.append(f"{lock_path} {section} out of sync with {path} (run npm install)")
    ctx.internal("deps-pinned", "\n".join(problems) or "ok")
    if not req_files and not pkg_files:
        return Result("C-DEPS-PINNED", rules, BLOCKED, "no requirements*.txt or package.json tracked")
    if problems:
        return Result(
            "C-DEPS-PINNED", rules, FAIL, f"{len(problems)} unpinned or unlocked dependencies", problems
        )
    return Result(
        "C-DEPS-PINNED",
        rules,
        PASS,
        f"{len(pins)} Python pins with == in {len(req_files)} files; "
        f"{len(pkg_files)} package.json exact and locked",
    )


def _flatten_specs(section: dict[str, Any], prefix: str = "") -> Iterable[tuple[str, str]]:
    for name, spec in section.items():
        if isinstance(spec, dict):
            yield from _flatten_specs(spec, f"{prefix}{name}>")
        else:
            yield f"{prefix}{name}", str(spec)


def check_forbidden_files(ctx: Ctx) -> Result:
    rules = ("ENG-E7", "ENG-E12", "B-09", "G1")
    hits = [f"{p}: {r}" for p in ctx.tracked() if (r := forbidden_reason(p))]
    ctx.internal("forbidden-files", "\n".join(hits) or "ok")
    if hits:
        return Result("C-FORBIDDEN-FILES", rules, FAIL, f"{len(hits)} forbidden files tracked", hits)
    return Result(
        "C-FORBIDDEN-FILES", rules, PASS, f"none of {len(ctx.tracked())} tracked files is forbidden"
    )


def check_secrets_tree(ctx: Ctx) -> Result:
    rules = ("ENG-E7", "G1")
    hits = []
    for path in ctx.tracked():
        text = ctx.text(path)
        if text is None:
            continue
        hits += [f"{path}:{ln}: {name} {redact(s)}" for name, ln, s in find_secrets(text)]
    ctx.internal("secrets-tree", "\n".join(hits) or "ok")
    if hits:
        return Result("C-SECRETS-TREE", rules, FAIL, f"{len(hits)} secrets in tracked files", hits)
    return Result("C-SECRETS-TREE", rules, PASS, f"no secrets in {len(ctx.tracked())} tracked files (index)")


def check_secrets_history(ctx: Ctx) -> Result:
    rules = ("ENG-E7", "G1")
    log = ctx.git(
        "log",
        "-p",
        "--all",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--format=%x00commit %H%n%B",
        store_raw=False,  # the raw history may hold the very secret; only the redacted findings are kept
    )
    if log.exit_code is None or (not log.ok and "does not have any commits" in log.output):
        return Result("C-SECRETS-HISTORY", rules, BLOCKED, f"git log unavailable: {log.output[:100]}")
    if not log.ok:
        return Result("C-SECRETS-HISTORY", rules, BLOCKED, f"git log exit {log.exit_code}")
    commit, path, hits, commits = "?", "(message)", set(), 0
    for line in log.output.splitlines():
        if line.startswith("\0commit "):
            commit, path, commits = line[8:16], "(message)", commits + 1
            continue
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        for name, pattern in SECRET_PATTERNS:
            for m in pattern.finditer(line):
                hits.add(f"commit {commit} {path}: {name} {redact(m.group(0))}")
    ctx.internal("secrets-history", "\n".join(sorted(hits)) or "ok")
    if hits:
        return Result(
            "C-SECRETS-HISTORY",
            rules,
            FAIL,
            f"{len(hits)} secrets in git history (rotate them)",
            sorted(hits),
        )
    return Result("C-SECRETS-HISTORY", rules, PASS, f"no secrets in {commits} commits (all refs, git log -p)")


PLACEHOLDER_KEYS = re.compile(r"(?i)(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)")
PLACEHOLDER_VALUE = re.compile(r"(?i)(fake|emulator|placeholder|example|changeme|your[-_]|dummy|<.*>)")


def check_env_examples(ctx: Ctx) -> Result:
    rules = ("ENG-E7", "G1")
    problems: list[str] = []
    examples = [p for p in ctx.tracked() if Path(p).name == ".env.example"]
    for path in examples:
        values = {}
        for lineno, raw in enumerate((ctx.text(path) or "").splitlines(), 1):
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", raw)
            if not m:
                continue
            name, value = m[1], m[2].strip("'\"")
            values[name] = value
            if PLACEHOLDER_KEYS.search(name) and value and not PLACEHOLDER_VALUE.search(value):
                problems.append(f"{path}:{lineno}: {name} has a value that is not a placeholder")
        if "VITE_FIREBASE_API_KEY" in values and values.get("VITE_USE_EMULATORS", "").lower() != "true":
            problems.append(f"{path}: Firebase web config without VITE_USE_EMULATORS=true (emulator only)")
    ctx.internal("env-examples", "\n".join(problems) or "ok")
    if problems:
        return Result("C-ENV-EXAMPLES", rules, FAIL, f"{len(problems)} real-looking values", problems)
    if not examples:
        return Result("C-ENV-EXAMPLES", rules, PASS, "no .env.example tracked")
    return Result("C-ENV-EXAMPLES", rules, PASS, f"{len(examples)} .env.example files hold only placeholders")


def check_provenance(ctx: Ctx) -> Result:
    rules = ("B-04", "ENG-E12", "G1")
    log = ctx.git("log", "--all", "--format=%H%x09%aI%x09%cI")
    if not log.ok or not log.output.strip():
        return Result("C-PROVENANCE", rules, BLOCKED, "no commits to inspect")
    too_early = []
    first: dt.datetime | None = None
    for line in log.output.strip().splitlines():
        sha, author, committer = line.split("\t")
        for when in (dt.datetime.fromisoformat(author), dt.datetime.fromisoformat(committer)):
            first = when if first is None or when < first else first
            if when.astimezone(IST).date() < EARLIEST_COMMIT_DATE:
                too_early.append(f"{sha[:8]} dated {when.isoformat()}")
    msgs = ctx.git("log", "--all", "--format=%H%x00%B%x1e")
    coauthored = []
    for record in msgs.output.split("\x1e"):
        if "\0" not in record:
            continue
        sha, body = record.strip().split("\0", 1)
        if re.search(r"(?im)^\s*co-authored-by\s*:", body):
            coauthored.append(f"{sha[:8]} has a Co-Authored-By line")
    problems = sorted(set(too_early)) + coauthored
    assert first is not None
    first_ist = first.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
    if problems:
        return Result(
            "C-PROVENANCE", rules, FAIL, f"first commit {first_ist}; {len(problems)} problems", problems
        )
    count = len(log.output.strip().splitlines())
    return Result(
        "C-PROVENANCE",
        rules,
        PASS,
        f"earliest commit {first_ist} (>= {EARLIEST_COMMIT_DATE}); {count} commits, no Co-Authored-By",
    )


PLACEHOLDER_PATTERNS = [
    ("[VERIFY]", re.compile(r"\[VERIFY\]")),
    ("lorem", re.compile(r"(?i)\blorem\b")),
    ("TODO", re.compile(r"\bTODO\b")),
]


def placeholder_scope(path: str) -> bool:
    if path == "README.md":
        return True
    if path.startswith("web/src/"):
        return not is_test_path(path)
    if path.startswith("backend/argus_api/"):
        return not is_test_path(path) and not is_seed_path(path)
    return False


def check_placeholders(ctx: Ctx) -> Result:
    rules = ("ENG-E13", "G1")
    hits = []
    scanned = [p for p in ctx.tracked() if placeholder_scope(p)]
    for path in scanned:
        for lineno, line in enumerate((ctx.text(path) or "").splitlines(), 1):
            for label, pattern in PLACEHOLDER_PATTERNS:
                if pattern.search(line):
                    hits.append(f"{path}:{lineno}: {label}: {line.strip()[:100]}")
    ctx.internal("placeholders", "\n".join(hits) or "ok")
    if hits:
        return Result("C-PLACEHOLDERS", rules, FAIL, f"{len(hits)} placeholders left", hits)
    if "README.md" not in ctx.blobs():
        return Result(
            "C-PLACEHOLDERS",
            rules,
            WARN,
            f"no placeholders in {len(scanned)} files, but README.md is not tracked yet",
        )
    return Result("C-PLACEHOLDERS", rules, PASS, f"no [VERIFY]/lorem/TODO in {len(scanned)} files")


# The seed scripts write labelled FAKE data; this checker has to name the word to look for it.
FAKE_ALLOWED = {"backend/argus_api/seed_fake_run.py", "web/scripts/seed_demo.py", "scripts/check.py"}
WEB_SRC = "web/src/"


def synthetic_data_files(ctx: Ctx) -> list[str]:
    return [
        p
        for p in ctx.tracked()
        if re.search(r"(^|/)(samples|eval/cases)/[^/]+\.json$", p) and not is_test_path(p)
    ]


def is_sample_policy_file(path: str) -> bool:
    """Demo sample files are what a user sees as a lender's policy (B-09: "synthetic sample
    policies only"), so each one declares itself synthetic. Eval cases are test fixtures,
    not policies: they are held to the personal-data scan, not to a marker."""
    return re.search(r"(^|/)samples/[^/]+\.json$", path) is not None


# Personal identifiers that must never appear in tracked data (B-09, India IT Act).
PERSONAL_DATA_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")),
    ("Indian mobile number", re.compile(r"(?<![\d.])(\+91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}(?![\d.])")),
    ("PAN", re.compile(r"\b[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]\b")),
    ("Aadhaar number", re.compile(r"(?<![\d.])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![\d.])")),
]


def _json_strings(data: Any) -> Iterable[str]:
    if isinstance(data, str):
        yield data
    elif isinstance(data, dict):
        for value in data.values():
            yield from _json_strings(value)
    elif isinstance(data, list):
        for item in data:
            yield from _json_strings(item)


def personal_data_hits(data: Any) -> list[str]:
    hits = []
    for text in _json_strings(data):
        for label, pattern in PERSONAL_DATA_PATTERNS:
            match = pattern.search(text)
            if match:
                hits.append(f"{label} {match.group(0)!r}")
    return hits


def _has_synthetic_marker(data: Any) -> bool:
    return isinstance(data, dict) and data.get("synthetic") is True


def check_synthetic(ctx: Ctx) -> Result:
    rules = ("B-09", "B-08", "G1")
    fails: list[str] = []
    warns: list[str] = []
    files = synthetic_data_files(ctx)
    for path in files:
        content = ctx.text(path) or ""
        if "FAKE" in content:
            fails.append(f"{path}: contains FAKE")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            fails.append(f"{path}: invalid JSON ({exc})")
            continue
        if is_sample_policy_file(path) and not _has_synthetic_marker(data):
            fails.append(f'{path}: sample policy has no top-level "synthetic": true')
        for hit in personal_data_hits(data):
            fails.append(f"{path}: looks like personal data: {hit}")
    for path in ctx.tracked():
        if path in FAKE_ALLOWED or is_test_path(path) or path in files:
            continue
        text = ctx.text(path)
        if text is None or not re.search(r"\bFAKE\b", text):
            continue
        line = next(i for i, ln in enumerate(text.splitlines(), 1) if re.search(r"\bFAKE\b", ln))
        if path.endswith(".md") and not path.startswith(WEB_SRC):  # web/src ships to users: always a FAIL
            warns.append(f"{path}:{line}: FAKE mentioned in documentation")
        else:
            fails.append(f"{path}:{line}: FAKE outside the seed scripts and tests")
    ctx.internal("synthetic", "\n".join(fails + warns) or "ok")
    if fails:
        return Result("C-SYNTHETIC", rules, FAIL, f"{len(fails)} problems in data files", fails + warns)
    if not files:
        return Result("C-SYNTHETIC", rules, WARN, "no samples/*.json or eval/cases/*.json tracked", warns)
    if warns:
        return Result(
            "C-SYNTHETIC",
            rules,
            WARN,
            f"{len(files)} data files clean (samples marked synthetic, no personal data), FAKE only in "
            f"seeds/tests; {len(warns)} doc mentions",
            warns,
        )
    return Result(
        "C-SYNTHETIC",
        rules,
        PASS,
        f"{len(files)} data files clean (samples marked synthetic, no personal data), no FAKE outside seeds",
    )


OTHER_CLOUD_IMPORT = re.compile(
    r"(?m)^\s*(?:import|from)\s+(boto3|botocore|aioboto3|aiobotocore|s3transfer|s3fs|awscrt|azure)\b"
)
OTHER_CLOUD_PACKAGE = re.compile(
    r"(?i)^(boto3|botocore|aioboto3|aiobotocore|s3fs|awscrt|azure[-.\w]*)\s*(==|$)"
)


def check_google_only(ctx: Ctx) -> Result:
    rules = ("B-01", "B-02", "G5")
    problems = []
    for path in ctx.tracked():
        if path.startswith("backend/argus_api/") and path.endswith(".py"):
            for m in OTHER_CLOUD_IMPORT.finditer(ctx.text(path) or ""):
                problems.append(f"{path}: imports {m[1]}")
        if re.fullmatch(r"requirements.*\.txt", Path(path).name):
            for line in (ctx.text(path) or "").splitlines():
                if OTHER_CLOUD_PACKAGE.match(line.strip()):
                    problems.append(f"{path}: depends on {line.strip()}")
    ctx.internal("other-cloud-imports", "\n".join(problems) or "ok")
    python = _needs_backend(ctx, "python")
    run = ctx.run(
        [str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", VERTEX_ONLY_TEST],
        ctx.repo / "backend",
        env=ctx.tool_env(offline=True),
    )
    if not run.ok or not re.search(r"\b1 passed\b", run.output):
        tail = [ln for ln in run.output.strip().splitlines() if ln.strip()][-8:]
        problems.append(f"{VERTEX_ONLY_TEST} did not pass (exit {run.exit_code})")
        problems += ["  " + t for t in tail]
    if problems:
        return Result("C-GOOGLE-ONLY", rules, FAIL, "non-Google serving path possible", problems)
    return Result(
        "C-GOOGLE-ONLY",
        rules,
        PASS,
        f"{VERTEX_ONLY_TEST.split('::')[1]} passed; no AWS/Azure imports or packages in backend",
    )


def _strip_rules_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def check_firestore_rules(ctx: Ctx) -> Result:
    rules = ("B-09", "S-03", "G4")
    text = ctx.text("firestore.rules")
    if text is None:
        return Result("C-FIRESTORE-RULES", rules, FAIL, "firestore.rules is not tracked")
    body = _strip_rules_comments(text)
    problems = []
    for m in re.finditer(r"\ballow\s+([^:;{}]+?)\s*:", body):
        methods = {x.strip() for x in m[1].split(",")}
        bad = methods & {"write", "create", "update", "delete"}
        if bad:
            line = body[: m.start()].count("\n") + 1
            problems.append(f"firestore.rules:{line}: client {', '.join(sorted(bad))} allowed")
    for coll in ("samples", "eval_results"):
        if not re.search(rf"match\s+/{coll}/\{{", body):
            problems.append(f"firestore.rules: no read rule for /{coll} (must be public read-only)")
    ctx.internal("firestore-rules", "\n".join(problems) or "ok")
    if problems:
        return Result("C-FIRESTORE-RULES", rules, FAIL, f"{len(problems)} problems", problems)
    return Result(
        "C-FIRESTORE-RULES", rules, PASS, "no client writes anywhere; samples and eval_results read-only"
    )


# Claims the product does not make (plan step 5): retired features (clause splitting and upload: policies are
# the pre-split sample files), an absolute claim, a live-status claim (CLAIMS_LEDGER N2) and a stale UI label.
# Matched case-insensitively, across line breaks, as whole words.
BANNED_PHRASES = (
    "no finding rests",
    "snapshot as on",
    "Gemini Pro",
    "vector search",
    "embeddings",
    "Cloud Storage",
    "clause splitting",
    "splits the policy",
    "splitting the policy",
    "uploaded policy",
    "in force today",
    "prevents injection",
    "FREE-AI compliant",
)
# These are wrong in any sentence: the first is itself the absolute claim, the second the retired label, the
# third a live-status claim that a negation does not fix ("not in force today" still claims to know today).
NEVER_NEGATED = frozenset({"no finding rests", "snapshot as on", "in force today"})
# A retired feature may be named to say it is absent: "there are no embeddings", "keyword search, not vector
# search". The negation must sit in the same sentence, within NEGATION_WINDOW words before the phrase.
NEGATION = re.compile(
    r"(?i)\b(?:no|not|without|never|nor|neither|none|instead of|rather than|no longer)\b|n't\b"
)
NEGATION_WINDOW = 6
SENTENCE_BREAK = re.compile(r"[.!?;|](?=\s)|\n\s*\n|\n\s*(?:[-*+]|\d+\.)\s")


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w-])" + r"\s+".join(map(re.escape, phrase.split())) + r"(?![\w-])", re.I)


BANNED_PATTERNS = [(phrase, _phrase_pattern(phrase)) for phrase in BANNED_PHRASES]


def _negated(text: str, start: int) -> bool:
    before = text[max(0, start - 300) : start]
    breaks = list(SENTENCE_BREAK.finditer(before))
    sentence = before[breaks[-1].end() :] if breaks else before
    window = " ".join(sentence.split()[-NEGATION_WINDOW:])
    return NEGATION.search(window) is not None


def banned_phrase_hits(text: str) -> list[tuple[int, str, str]]:
    """(line, phrase, context) of each banned phrase in `text` that the negation allowlist does not excuse."""
    hits = []
    for phrase, pattern in BANNED_PATTERNS:
        for m in pattern.finditer(text):
            if phrase not in NEVER_NEGATED and _negated(text, m.start()):
                continue
            line = text.count("\n", 0, m.start()) + 1
            context = " ".join(text[max(0, m.start() - 40) : m.end() + 40].split())
            hits.append((line, phrase, context))
    return sorted(hits)


def banned_phrase_scope(path: str) -> bool:
    if path in {"README.md", "web/README.md"} or path.startswith("docs/"):
        return True
    return path.startswith(WEB_SRC) and not is_test_path(path)


def check_banned_phrases(ctx: Ctx) -> Result:
    rules = ("ENG-E13", "G1")
    scanned = [p for p in ctx.tracked() if banned_phrase_scope(p)]
    hits = []
    for path in scanned:
        for line, phrase, context in banned_phrase_hits(ctx.text(path) or ""):
            hits.append(f"{path}:{line}: {phrase!r}: ...{context}...")
    ctx.internal("banned-phrases", "\n".join(hits) or "ok")
    if hits:
        return Result("C-BANNED-PHRASES", rules, FAIL, f"{len(hits)} banned phrases", hits)
    return Result(
        "C-BANNED-PHRASES",
        rules,
        PASS,
        f"none of {len(BANNED_PHRASES)} banned phrases in {len(scanned)} files (web/src, READMEs, docs/)",
    )


TAGLINE_FILE = "web/src/start/copy.ts"
TAGLINE_DECL = re.compile(r"export\s+const\s+TAGLINE\s*(?::\s*string\s*)?=\s*")
TS_STRING = re.compile(r"\s*(?:\"((?:[^\"\\\n]|\\.)*)\"|'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\$]|\\.)*)`)\s*")
TAGLINE_PIECE = 40  # any run this long counts as a copy, as start/copy.test.ts does for web/src


def read_tagline(source: str) -> str | None:
    """The TAGLINE string of copy.ts: one literal or literals joined with +, whitespace collapsed."""
    decl = TAGLINE_DECL.search(source)
    if decl is None:
        return None
    pos, parts = decl.end(), []
    while True:
        m = TS_STRING.match(source, pos)
        if m is None:
            break
        raw = next(g for g in m.groups() if g is not None)
        parts.append(re.sub(r"\\(.)", r"\1", raw))
        pos = m.end()
        if not source.startswith("+", pos):
            break
        pos += 1
    text = " ".join("".join(parts).split())
    return text or None


def check_tagline(ctx: Ctx) -> Result:
    rules = ("ENG-E13", "G1")
    source = ctx.text(TAGLINE_FILE)
    if source is None:
        return Result("C-TAGLINE", rules, FAIL, f"{TAGLINE_FILE} is not tracked")
    tagline = read_tagline(source)
    if tagline is None:
        return Result("C-TAGLINE", rules, FAIL, f"{TAGLINE_FILE} has no string literal TAGLINE")
    size = min(TAGLINE_PIECE, len(tagline))
    pieces = {tagline[i : i + size] for i in range(0, len(tagline) - size + 1, size)}
    pieces.add(tagline[-size:])
    copies = []
    for path in ctx.tracked():
        if path in {TAGLINE_FILE, "scripts/check.py"} or is_test_path(path):
            continue
        text = ctx.text(path)
        if text is None:
            continue
        flat = " ".join(text.split())
        piece = next((p for p in sorted(pieces) if p in flat), None)
        if piece is not None:
            copies.append(f"{path}: repeats the tagline: {piece!r}")
    ctx.internal("tagline", "\n".join(copies) or "ok")
    if copies:
        return Result(
            "C-TAGLINE",
            rules,
            FAIL,
            f"TAGLINE text appears outside {TAGLINE_FILE} in {len(copies)} files",
            copies,
        )
    return Result(
        "C-TAGLINE", rules, PASS, f"TAGLINE ({len(tagline)} chars) is written out only in {TAGLINE_FILE}"
    )


EMULATOR_ENV = "FIRESTORE_EMULATOR_HOST"


def seed_script_paths(ctx: Ctx) -> list[str]:
    return [p for p in ctx.tracked() if p.endswith(".py") and is_seed_path(p) and not is_test_path(p)]


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def seed_guard_problems(source: str) -> list[str]:
    """Why a seed script could write to a real Firestore. Empty when it has an emulator guard (a function
    that reads FIRESTORE_EMULATOR_HOST and raises SystemExit) that runs before every Firestore client."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"does not parse: {exc.msg} (line {exc.lineno})"]
    env_names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and node.value.value == EMULATOR_ENV
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    def reads_env(fn: ast.AST) -> bool:
        return any(
            (isinstance(n, ast.Constant) and n.value == EMULATOR_ENV)
            or (isinstance(n, ast.Name) and n.id in env_names)
            for n in ast.walk(fn)
        )

    def exits(fn: ast.AST) -> bool:
        for n in ast.walk(fn):
            if isinstance(n, ast.Raise) and n.exc is not None:
                exc = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
                if isinstance(exc, ast.Name) and exc.id == "SystemExit":
                    return True
        return False

    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    guards = {fn.name for fn in functions if reads_env(fn) and exits(fn)}
    if not guards:
        return [f"no emulator guard: no function reads {EMULATOR_ENV} and raises SystemExit"]

    def guard_lines(scope: ast.AST) -> list[int]:
        return [n.lineno for n in ast.walk(scope) if isinstance(n, ast.Call) and _call_name(n) in guards]

    module_guard = [
        stmt.lineno
        for stmt in tree.body
        if isinstance(stmt, ast.Expr | ast.Assign | ast.AnnAssign)
        and isinstance(stmt.value, ast.Call)
        and _call_name(stmt.value) in guards
    ]
    problems = []
    if not guard_lines(tree):
        problems.append(f"emulator guard {sorted(guards)[0]}() is defined but never called")
    top_level = [
        s for s in tree.body if not isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]
    for stmt in top_level:
        for call in (n for n in ast.walk(stmt) if isinstance(n, ast.Call) and _call_name(n) == "Client"):
            if not (module_guard and module_guard[0] < call.lineno):
                problems.append(
                    f"line {call.lineno}: module creates a Firestore client before the emulator guard"
                )
    for fn in functions:
        if fn.name in guards:
            continue
        guarded = guard_lines(fn)
        for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call) and _call_name(n) == "Client"):
            if module_guard and module_guard[0] < call.lineno:
                continue
            if not any(line <= call.lineno for line in guarded):
                problems.append(
                    f"line {call.lineno}: {fn.name}() creates a Firestore client before the emulator guard"
                )
    return problems


def check_seed_guard(ctx: Ctx) -> Result:
    rules = ("B-09", "G1")
    seeds = seed_script_paths(ctx)
    problems = [f"{path}: {why}" for path in seeds for why in seed_guard_problems(ctx.text(path) or "")]
    ctx.internal("seed-guard", "\n".join(problems) or "\n".join(seeds) or "no seed scripts")
    if problems:
        return Result(
            "C-SEED-GUARD", rules, FAIL, "a seed script could write to the real Firestore", problems
        )
    if not seeds:
        return Result("C-SEED-GUARD", rules, WARN, "no seed*.py script tracked")
    return Result(
        "C-SEED-GUARD",
        rules,
        PASS,
        f"{len(seeds)} seed scripts refuse to run without {EMULATOR_ENV}: {', '.join(seeds)}",
    )


COMMIT_CHECKS = [
    Check("C-PY-LINT", ("ENG-E6", "G1"), check_py_lint),
    Check("C-PY-FORMAT", ("ENG-E6", "G1"), check_py_format),
    Check("C-PY-TYPES", ("ENG-E6", "G1"), check_py_types),
    Check("C-WEB-TYPES", ("ENG-E6", "G1"), check_web_types),
    Check("C-WEB-LINT", ("ENG-E6", "G1"), check_web_lint),
    Check("C-PY-TESTS", ("ENG-E5", "G2"), check_py_tests, slow=True),
    Check("C-WEB-TESTS", ("ENG-E5", "G2"), check_web_tests, slow=True),
    Check("C-DEPS-PINNED", ("ENG-E1", "G1"), check_deps_pinned),
    Check("C-FORBIDDEN-FILES", ("ENG-E7", "ENG-E12", "B-09", "G1"), check_forbidden_files),
    Check("C-SECRETS-TREE", ("ENG-E7", "G1"), check_secrets_tree),
    Check("C-SECRETS-HISTORY", ("ENG-E7", "G1"), check_secrets_history),
    Check("C-ENV-EXAMPLES", ("ENG-E7", "G1"), check_env_examples),
    Check("C-PROVENANCE", ("B-04", "ENG-E12", "G1"), check_provenance),
    Check("C-PLACEHOLDERS", ("ENG-E13", "G1"), check_placeholders),
    Check("C-SYNTHETIC", ("B-09", "B-08", "G1"), check_synthetic),
    Check("C-GOOGLE-ONLY", ("B-01", "B-02", "G5"), check_google_only),
    Check("C-FIRESTORE-RULES", ("B-09", "S-03", "G4"), check_firestore_rules),
    Check("C-BANNED-PHRASES", ("ENG-E13", "G1"), check_banned_phrases),
    Check("C-TAGLINE", ("ENG-E13", "G1"), check_tagline),
    Check("C-SEED-GUARD", ("B-09", "G1"), check_seed_guard),
]


# --------------------------------------------------------------------------------------------- HTTP
@dataclasses.dataclass
class HttpResult:
    status: int | None
    final_url: str
    headers: dict[str, str]
    body: bytes
    error: str = ""


def http(
    ctx: Ctx,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    follow: bool = True,
    data: bytes | None = None,
    timeout_s: float = HTTP_TIMEOUT_S,
    limit: int = 200_000,
    shown_url: str | None = None,
) -> HttpResult:
    """A cookie-less request (urllib keeps no cookie jar), recorded as evidence.

    shown_url replaces url in the evidence when the real one carries a value that must not be written out."""
    started, t0 = utc_now(), time.monotonic()
    req = urllib.request.Request(
        url, method=method, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})}
    )
    handlers: list[urllib.request.BaseHandler] = [] if follow else [_NoRedirect()]
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            res = HttpResult(
                resp.status,
                resp.geturl(),
                {k.lower(): v for k, v in resp.headers.items()},
                resp.read(limit),
            )
    except urllib.error.HTTPError as exc:
        res = HttpResult(
            exc.code,
            exc.geturl() or url,
            {k.lower(): v for k, v in exc.headers.items()},
            exc.read(20_000) if exc.fp else b"",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        res = HttpResult(None, url, {}, b"", f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}")
    shown = "\n".join(f"{k}: {v}" for k, v in sorted(res.headers.items()))
    final = shown_url if shown_url is not None else res.final_url
    output = f"status {res.status} final {final} {res.error}\n{shown}"
    ctx.runs.append(
        CmdRun(
            f"http {method} {shown_url or url}",
            "-",
            res.status,
            output,
            started,
            round(time.monotonic() - t0, 2),
        )
    )
    return res


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _login_wall(res: HttpResult) -> bool:
    final = urllib.parse.urlparse(res.final_url)
    return final.netloc == "accounts.google.com" or (final.netloc == "github.com" and final.path == "/login")


def check_url_public(ctx: Ctx, url: str) -> tuple[str, str]:
    res = http(ctx, url)
    if res.status is None:
        return BLOCKED, f"{url}: unreachable ({res.error})"
    if _login_wall(res):
        return FAIL, f"{url}: redirects to a sign-in page ({res.final_url[:80]})"
    if res.status != 200:
        return FAIL, f"{url}: HTTP {res.status}"
    return PASS, f"{url}: 200"


def _worst(statuses: Iterable[str]) -> str:
    order = {FAIL: 0, BLOCKED: 1, WARN: 2, HUMAN: 3, PASS: 4}
    return min(statuses, key=lambda s: order[s], default=PASS)


# --------------------------------------------------------------------------------------------- deploy checks
# The live stack (DECISIONS D-48): the defaults, so `check.py deploy --strict` needs no flags.
DEFAULT_PROJECT = "argus-aibc"
DEFAULT_SERVICE_URL = "https://argus-api-419264532549.asia-south1.run.app"
DEFAULT_HOSTING_ORIGIN = "https://argus-aibc.web.app"
HEALTH_TIMEOUT_S = 60  # at min instances 0 the first /health waits for a cold start
EXPECTED_LOCATION = "global"
SERVICE_ACCOUNT_NAME = "argus-api"
# Exactly the project roles of the API's service account: Firestore, Vertex AI, logs, token revocation (D-53).
SERVICE_ACCOUNT_ROLES = frozenset(
    {"roles/datastore.user", "roles/aiplatform.user", "roles/logging.logWriter", "roles/firebaseauth.viewer"}
)
MAX_INSTANCES = 3
REQUEST_TIMEOUT_S = 300
# The production library (backend/data/corpus + supersession.json), as loaded to Firestore (D-48).
EXPECTED_FIRESTORE_COUNTS = {"rbi_paragraphs": 365, "rbi_instruments": 4, "supersession": 15}
# config/limits as set for the competition (D-48): 60 runs a day, 20 an hour, 5 per user, switched on.
EXPECTED_LIMITS = {"daily_run_cap": 60, "hourly_run_cap": 20, "per_user_daily_run_cap": 5, "enabled": True}
WEB_ENV_FILE = "web/.env.production.local"  # the Firebase web config the hosted bundle was built with
WEB_API_KEY_VAR = "VITE_FIREBASE_API_KEY"
# Names that must never reach the browser bundle, even without a value next to them.
BUNDLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("server-secret-name", re.compile(r"OPENROUTER_API_KEY|GEMINI_API_KEY|GOOGLE_APPLICATION_CREDENTIALS")),
    ("private-key-field", re.compile(r"\"private_key(?:_id)?\"\s*:")),
]
API_KEY_FORBIDDEN_TARGETS = ("generativelanguage.googleapis.com", "aiplatform.googleapis.com")
FIREBASE_RULES_API = "https://firebaserules.googleapis.com/v1"
IDENTITY_TOOLKIT_API = "https://identitytoolkit.googleapis.com/admin/v2"
API_KEYS_API = "https://apikeys.googleapis.com/v2"
MONITORING_API = "https://monitoring.googleapis.com/v3"
FIRESTORE_API = "https://firestore.googleapis.com/v1"
SAMPLE_PROVIDER = "vertex"  # runs.SAMPLE_PROVIDER: a public saved run is a real review on Vertex AI


def _deploy_inputs(ctx: Ctx) -> tuple[str, str]:
    project, url = getattr(ctx.args, "project", None), getattr(ctx.args, "service_url", None)
    if not project or not url:
        raise Blocked("needs --project and --service-url")
    return project, url.rstrip("/")


def _hosting_origin(ctx: Ctx) -> str:
    origin = getattr(ctx.args, "hosting_origin", None)
    if not origin:
        raise Blocked("needs --hosting-origin (the Firebase Hosting URL)")
    return str(origin).rstrip("/")


def _stdout(run: CmdRun) -> str:
    return run.output.split("\n[stderr]\n")[0]


def _repo_python(ctx: Ctx, code_or_script: list[str], what: str) -> str:
    """Run the backend venv's python in the repo (offline) and return its stdout; Blocked when it cannot."""
    python = ctx.venv_tool("python")
    if python is None:
        raise Blocked(f"{what}: no python in {ctx.venv_bin}")
    run = ctx.run([str(python), *code_or_script], ctx.repo, env=ctx.tool_env(offline=True), timeout_s=120)
    if not run.ok:
        raise Blocked(f"{what} failed (exit {run.exit_code}): {run.output.strip()[-200:]}")
    return _stdout(run).strip()


def repo_corpus_hash(ctx: Ctx) -> str:
    """scripts/corpus_hash.py: the hash of the library in backend/data/corpus, as /health computes it."""
    return _repo_python(ctx, ["scripts/corpus_hash.py"], "scripts/corpus_hash.py").splitlines()[-1]


def repo_pinned_models(ctx: Ctx) -> tuple[str, str]:
    """config.PINNED_MODELS['vertex'] from the repo: (agent model, monitor model)."""
    code = (
        "import json, sys; sys.path.insert(0, 'backend'); "
        "from argus_api.config import PINNED_MODELS; print(json.dumps(list(PINNED_MODELS['vertex'])))"
    )
    agent, monitor = json.loads(
        _repo_python(ctx, ["-c", code], "reading config.PINNED_MODELS").splitlines()[-1]
    )
    return str(agent), str(monitor)


def health_problems(
    body: dict[str, Any], *, models: tuple[str, str], corpus_hash: str, expect_sha: str | None
) -> list[str]:
    """Why a /health body does not describe the expected deploy (empty when it does)."""
    want: dict[str, Any] = {
        "status": "ok",
        "mode": "cloud",
        "provider": "vertex",
        "agent_model": models[0],
        "monitor_model": models[1],
        "location": EXPECTED_LOCATION,
        "corpus_hash": corpus_hash,
    }
    if expect_sha:
        want["git_sha"] = expect_sha
    problems = [f"{k} is {body.get(k)!r}, expected {v!r}" for k, v in want.items() if body.get(k) != v]
    if not expect_sha and body.get("git_sha") in {None, "", "unknown"}:
        problems.append(f"git_sha is {body.get('git_sha')!r}: the deploy did not record its commit")
    return problems


def check_deploy_health(ctx: Ctx) -> Result:
    rules = ("S-03", "B-01", "B-02")
    _, url = _deploy_inputs(ctx)
    res = http(ctx, f"{url}/health", timeout_s=HEALTH_TIMEOUT_S)
    if res.status is None:
        return Result("D-HEALTH", rules, BLOCKED, f"{url}/health unreachable: {res.error}")
    if res.status != 200:
        return Result("D-HEALTH", rules, FAIL, f"{url}/health returned {res.status}")
    try:
        body = json.loads(res.body)
    except ValueError:
        return Result("D-HEALTH", rules, FAIL, f"{url}/health is not JSON")
    ctx.internal("health-body", json.dumps(body, indent=2, sort_keys=True))
    expect_sha = getattr(ctx.args, "expect_sha", None)
    problems = health_problems(
        body, models=repo_pinned_models(ctx), corpus_hash=repo_corpus_hash(ctx), expect_sha=expect_sha
    )
    summary = (
        f"{url}/health: sha {str(body.get('git_sha'))[:12]}, {body.get('mode')}/{body.get('provider')}, "
        f"{body.get('agent_model')} + {body.get('monitor_model')}, location {body.get('location')}, "
        f"corpus {str(body.get('corpus_hash'))[:12]}"
    )
    if problems:
        return Result("D-HEALTH", rules, FAIL, summary, problems)
    sha_note = "sha equals --expect-sha" if expect_sha else "no --expect-sha given"
    return Result(
        "D-HEALTH", rules, PASS, f"{summary}; corpus hash equals scripts/corpus_hash.py; {sha_note}"
    )


# ---- the hosted web app
ASSET_REF = re.compile(r"""(?:src|href)\s*=\s*["'](/assets/[^"'?#]+)["']""")
SOURCE_MAP_REF = re.compile(r"//[#@]\s*sourceMappingURL=([^\s'\"]+)")


def served_assets(index_html: str) -> list[str]:
    """The /assets/... files index.html loads, in order, without duplicates."""
    return list(dict.fromkeys(ASSET_REF.findall(index_html)))


def _fetch_site(ctx: Ctx, origin: str) -> dict[str, bytes]:
    """index.html, every asset it loads and each script's source map (when served): path -> bytes."""
    index = http(ctx, f"{origin}/", timeout_s=HTTP_TIMEOUT_S, limit=5_000_000)
    if index.status is None:
        raise Blocked(f"{origin} unreachable: {index.error}")
    if index.status != 200:
        raise _Fail(f"{origin}/ returned HTTP {index.status}")
    files = {"index.html": index.body}
    for path in served_assets(index.body.decode("utf-8", "replace")):
        res = http(ctx, origin + path, limit=50_000_000)
        if res.status != 200:
            raise _Fail(f"{origin}{path} returned HTTP {res.status} {res.error}".strip())
        files[path.lstrip("/")] = res.body
        if path.endswith(".js"):
            refs = SOURCE_MAP_REF.findall(res.body[-2000:].decode("utf-8", "replace"))
            map_path = urllib.parse.urljoin(path, refs[-1]) if refs else path + ".map"
            if map_path.startswith("/assets/"):
                got = http(ctx, origin + map_path, limit=50_000_000)
                if got.status == 200:
                    files[map_path.lstrip("/")] = got.body
    return files


class _Fail(Exception):
    """Raised inside a check for a FAIL found while gathering inputs."""


def check_deploy_web(ctx: Ctx) -> Result:
    """The hosted app answers 200 and serves exactly the files in web/dist (the build that was checked)."""
    rules = ("S-03", "B-03")
    origin = _hosting_origin(ctx)
    try:
        files = _fetch_site(ctx, origin)
    except _Fail as exc:
        return Result("D-WEB", rules, FAIL, str(exc))
    dist = ctx.repo / "web" / "dist"
    if not (dist / "index.html").is_file():
        raise Blocked("web/dist/index.html missing: build the web app to compare what is served")
    problems = []
    for path, body in files.items():
        if path.endswith(".map"):
            continue  # compared as part of the script it belongs to; scanned by D-WEB-SECRETS
        local = dist / path
        if not local.is_file():
            problems.append(f"{path} is served but not in web/dist")
        elif hashlib.sha256(local.read_bytes()).hexdigest() != hashlib.sha256(body).hexdigest():
            problems.append(f"{path}: served sha256 differs from web/dist")
    ctx.internal(
        "served-files",
        "\n".join(f"{hashlib.sha256(b).hexdigest()}  {len(b):>9}  {p}" for p, b in sorted(files.items())),
    )
    assets = [p for p in files if p != "index.html" and not p.endswith(".map")]
    if not assets:
        problems.append("index.html loads no /assets/ files")
    summary = f"{origin}/ 200; {len(assets)} assets"
    if problems:
        return Result("D-WEB", rules, FAIL, summary, problems)
    maps = [p for p in files if p.endswith(".map")]
    note = f"; {len(maps)} source map(s) also served" if maps else ""
    return Result("D-WEB", rules, PASS, f"{summary}, index.html and every asset equal web/dist{note}")


def web_api_key(ctx: Ctx) -> str:
    """The Firebase web apiKey (public by design) the hosted bundle was built with, from WEB_ENV_FILE."""
    path = ctx.repo / WEB_ENV_FILE
    if not path.is_file():
        raise Blocked(f"{WEB_ENV_FILE} missing: cannot tell the Firebase web apiKey from a leaked key")
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == WEB_API_KEY_VAR and value.strip():
            return value.strip().strip("'\"")
    raise Blocked(f"{WEB_API_KEY_VAR} not set in {WEB_ENV_FILE}")


def bundle_secret_hits(files: dict[str, bytes], allowed_key: str) -> list[str]:
    """Every secret-looking string in the served files but the Firebase web apiKey, values redacted."""
    hits = []
    for path, body in sorted(files.items()):
        text = body.decode("utf-8", "replace")
        found = find_secrets(text)
        for lineno, line in enumerate(text.splitlines(), 1):
            found += [(name, lineno, m.group(0)) for name, pat in BUNDLE_PATTERNS for m in pat.finditer(line)]
        for name, lineno, value in found:
            if name == "google-api-key" and value == allowed_key:
                continue
            shown = value if name in {"server-secret-name", "private-key-field"} else redact(value)
            hits.append(f"{path}:{lineno}: {name} {shown}")
    return hits


def check_deploy_web_secrets(ctx: Ctx) -> Result:
    """No secret in what the browser downloads; the one Google API key there is the Firebase web apiKey."""
    rules = ("ENG-E7", "S-03", "G1")
    origin = _hosting_origin(ctx)
    allowed = web_api_key(ctx)
    try:
        files = _fetch_site(ctx, origin)
    except _Fail as exc:
        return Result("D-WEB-SECRETS", rules, FAIL, str(exc))
    hits = bundle_secret_hits(files, allowed)
    keys = {
        m.group(0)
        for b in files.values()
        for m in SECRET_PATTERNS[0][1].finditer(b.decode("utf-8", "replace"))
    }
    ctx.internal("bundle-scan", f"scanned {len(files)} files: {', '.join(sorted(files))}\n" + "\n".join(hits))
    if hits:
        return Result(
            "D-WEB-SECRETS", rules, FAIL, f"{len(hits)} secret-looking strings in the served app", hits
        )
    key_note = "the Firebase web apiKey" if allowed in keys else "no Google API key"
    return Result(
        "D-WEB-SECRETS",
        rules,
        PASS,
        f"{len(files)} served files scanned ({sum(len(b) for b in files.values()) // 1024} KiB): "
        f"only {key_note} and the API URL",
    )


# ---- the API
def check_deploy_no_token(ctx: Ctx) -> Result:
    """Starting a run without a Firebase ID token is refused with 401 (and so spends nothing)."""
    rules = ("S-03", "ENG-E9")
    _, url = _deploy_inputs(ctx)
    res = http(
        ctx,
        f"{url}/api/runs",
        method="POST",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        follow=False,
        timeout_s=HEALTH_TIMEOUT_S,
    )
    if res.status is None:
        return Result("D-NO-TOKEN", rules, BLOCKED, f"POST {url}/api/runs unreachable: {res.error}")
    if res.status != 401:
        return Result(
            "D-NO-TOKEN", rules, FAIL, f"POST /api/runs without a token returned {res.status}, not 401"
        )
    return Result("D-NO-TOKEN", rules, PASS, "POST /api/runs without a token returned 401")


def _gcloud(ctx: Ctx, *args: str) -> CmdRun:
    if shutil.which("gcloud") is None:
        raise Blocked("gcloud not found on PATH")
    return ctx.run(["gcloud", *args, "--quiet"], ctx.repo, env=dict(os.environ))


def _permission_problem(run: CmdRun) -> bool:
    return bool(
        re.search(
            r"PERMISSION_DENIED|does not have permission|403|not authorized|"
            r"reauthentication|login",
            run.output,
            re.I,
        )
    )


def _find_service(ctx: Ctx, project: str, url: str) -> tuple[str, str]:
    service, region = getattr(ctx.args, "service", None), getattr(ctx.args, "region", None)
    if service and region:
        return service, region
    run = _gcloud(ctx, "run", "services", "list", f"--project={project}", "--format=json")
    if not run.ok:
        raise Blocked(f"gcloud run services list failed (exit {run.exit_code}); pass --service and --region")
    for svc in json.loads(_stdout(run) or "[]"):
        meta = svc.get("metadata", {})
        urls = {svc.get("status", {}).get("url", "")}
        urls |= set(json.loads(meta.get("annotations", {}).get("run.googleapis.com/urls", "[]") or "[]"))
        if url in {u.rstrip("/") for u in urls if u}:
            return meta.get("name", ""), meta.get("labels", {}).get("cloud.googleapis.com/location", "")
    raise Blocked(f"no Cloud Run service in {project} serves {url}; pass --service and --region")


def env_name_problems(names: Iterable[str]) -> list[str]:
    """Environment variables a cloud deploy must not carry: keys, OpenRouter, emulators, model overrides."""
    problems = []
    for name in sorted(set(names)):
        if "KEY" in name.upper():
            problems.append(f"{name} is set on the service (no *KEY* variables in cloud mode)")
        elif (
            name in FORBIDDEN_CLOUD_ENV or name.upper().startswith("OPENROUTER") or "EMULATOR" in name.upper()
        ):
            problems.append(f"{name} is set on the service (local-only variable)")
    return problems


def run_config_problems(svc: dict[str, Any], project: str, *, allow_min_zero: bool) -> tuple[str, list[str]]:
    """(summary, problems) for a `gcloud run services describe --format=json` document."""
    tmpl = svc.get("spec", {}).get("template", {})
    ann = {
        **svc.get("metadata", {}).get("annotations", {}),
        **tmpl.get("metadata", {}).get("annotations", {}),
    }
    spec = tmpl.get("spec", {})
    containers = spec.get("containers", [])
    env = {e.get("name"): e.get("value", "<from secret>") for c in containers for e in c.get("env", [])}
    # No minScale annotation means Cloud Run's default, 0.
    min_i = ann.get("autoscaling.knative.dev/minScale") or ann.get("run.googleapis.com/minScale") or "0"
    max_i = ann.get("autoscaling.knative.dev/maxScale") or ann.get("run.googleapis.com/maxScale")
    timeout = spec.get("timeoutSeconds")
    account = spec.get("serviceAccountName")
    want_account = f"{SERVICE_ACCOUNT_NAME}@{project}.iam.gserviceaccount.com"
    status = svc.get("status", {})
    serving = [t for t in status.get("traffic", []) if t.get("percent")]
    problems = []
    if env.get("ARGUS_MODE") != "cloud":
        problems.append(f"ARGUS_MODE is {env.get('ARGUS_MODE')!r}, expected 'cloud'")
    if str(min_i) != "1" and not (allow_min_zero and str(min_i) == "0"):
        problems.append(
            f"min instances {min_i}, expected 1" + ("" if allow_min_zero else " (or --allow-min-zero)")
        )
    if max_i is None or not str(max_i).isdigit() or int(max_i) > MAX_INSTANCES:
        problems.append(f"max instances {max_i}, expected <= {MAX_INSTANCES}")
    if str(timeout) != str(REQUEST_TIMEOUT_S):
        problems.append(f"request timeout {timeout} s, expected {REQUEST_TIMEOUT_S}")
    if account != want_account:
        problems.append(f"service account {account!r}, expected {want_account!r}")
    problems += env_name_problems(n for n in env if n)
    if not serving or any(not t.get("latestRevision") for t in serving):
        problems.append(f"traffic does not all go to the latest revision: {serving}")
    revision = status.get("latestReadyRevisionName")
    min_note = " (allowed by --allow-min-zero)" if allow_min_zero and str(min_i) == "0" else ""
    summary = (
        f"{svc.get('metadata', {}).get('name')} revision {revision}: mode={env.get('ARGUS_MODE')} "
        f"min={min_i}{min_note} max={max_i} timeout={timeout}s sa={account}"
    )
    return summary, problems


def check_deploy_run_config(ctx: Ctx) -> Result:
    rules = ("S-03", "B-01", "B-02", "ENG-E7")
    project, url = _deploy_inputs(ctx)
    service, region = _find_service(ctx, project, url)
    run = _gcloud(
        ctx,
        "run",
        "services",
        "describe",
        service,
        f"--project={project}",
        f"--region={region}",
        "--format=json",
    )
    if not run.ok:
        return Result(
            "D-RUN-CONFIG", rules, BLOCKED, f"gcloud run services describe failed (exit {run.exit_code})"
        )
    summary, problems = run_config_problems(
        json.loads(_stdout(run)), project, allow_min_zero=bool(getattr(ctx.args, "allow_min_zero", False))
    )
    if problems:
        return Result("D-RUN-CONFIG", rules, FAIL, summary, problems)
    return Result("D-RUN-CONFIG", rules, PASS, summary + "; env names clean")


def service_account_roles(policy: dict[str, Any], member: str) -> set[str]:
    """Project roles bound to member in an IAM policy (conditional bindings included)."""
    return {b.get("role", "") for b in policy.get("bindings", []) if member in b.get("members", [])}


def check_deploy_sa_roles(ctx: Ctx) -> Result:
    rules = ("B-02", "ENG-E7")
    project, _ = _deploy_inputs(ctx)
    run = _gcloud(ctx, "projects", "get-iam-policy", project, "--format=json")
    if not run.ok:
        why = "no permission" if _permission_problem(run) else f"exit {run.exit_code}"
        return Result("D-SA-ROLES", rules, BLOCKED, f"gcloud projects get-iam-policy: {why}")
    member = f"serviceAccount:{SERVICE_ACCOUNT_NAME}@{project}.iam.gserviceaccount.com"
    roles = service_account_roles(json.loads(_stdout(run)), member)
    extra, missing = sorted(roles - SERVICE_ACCOUNT_ROLES), sorted(SERVICE_ACCOUNT_ROLES - roles)
    problems = [f"extra role {r}" for r in extra] + [f"missing role {r}" for r in missing]
    summary = f"{member.split(':', 1)[1]} has {', '.join(sorted(roles)) or 'no roles'}"
    if problems:
        return Result("D-SA-ROLES", rules, FAIL, summary, problems)
    return Result("D-SA-ROLES", rules, PASS, summary + " (exactly the 4 expected)")


# ---- Google APIs read with the gcloud login
def _gcloud_token(ctx: Ctx) -> str | None:
    """An access token from the active gcloud login, or None. The token never reaches the evidence."""
    if shutil.which("gcloud") is None:
        return None
    started = utc_now()
    proc = subprocess.run(
        ["gcloud", "auth", "print-access-token", "--quiet"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    ctx.runs.append(
        CmdRun(
            "gcloud auth print-access-token --quiet",
            str(ctx.repo),
            proc.returncode,
            "[token redacted]" if proc.returncode == 0 else proc.stderr,
            started,
            0.0,
        )
    )
    token_value = proc.stdout.strip()
    return token_value if proc.returncode == 0 and token_value else None


def _google_get(
    ctx: Ctx,
    url: str,
    project: str,
    *,
    token: str | None = None,
    shown_url: str | None = None,
    bill_project: bool = True,
) -> dict[str, Any]:
    """GET a Google REST API as the gcloud user; Blocked when there is no login or the call fails.

    bill_project False leaves out x-goog-user-project, so the call is attributed as gcloud's own calls are:
    for an API that need not be enabled on the project (API Keys)."""
    if token is None:
        if shutil.which("gcloud") is None:
            raise Blocked("gcloud not found on PATH; cannot call Google APIs")
        token = _gcloud_token(ctx)
        if token is None:
            raise Blocked("no gcloud access token (gcloud auth login)")
    headers = {"Authorization": f"Bearer {token}"}
    if bill_project:
        headers["x-goog-user-project"] = project
    res = http(ctx, url, headers=headers, shown_url=shown_url)
    if res.status != 200:
        raise Blocked(f"GET {shown_url or url} failed: {res.error or f'HTTP {res.status}'}")
    body = json.loads(res.body or b"{}")
    return body if isinstance(body, dict) else {}


def _sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def check_deploy_rules(ctx: Ctx) -> Result:
    """The Firestore rules in force are the repo's firestore.rules, byte for byte."""
    rules = ("B-09", "S-03", "G4")
    project, _ = _deploy_inputs(ctx)
    local_path = ctx.repo / "firestore.rules"
    if not local_path.is_file():
        raise Blocked("firestore.rules missing in the repo")
    local = local_path.read_text(encoding="utf-8")
    token = _gcloud_token(ctx) if shutil.which("gcloud") else None
    if token is None:
        raise Blocked("no gcloud access token; cannot read the deployed rules")
    release = _google_get(
        ctx, f"{FIREBASE_RULES_API}/projects/{project}/releases/cloud.firestore", project, token=token
    )
    ruleset_name = release.get("rulesetName")
    if not ruleset_name:
        return Result(
            "D-RULES", rules, FAIL, f"no cloud.firestore release in {project}: rules never deployed"
        )
    ruleset = _google_get(ctx, f"{FIREBASE_RULES_API}/{ruleset_name}", project, token=token)
    files = ruleset.get("source", {}).get("files", [])
    deployed = "".join(f.get("content", "") for f in files)
    ctx.internal("deployed-rules", deployed)
    summary = (
        f"{ruleset_name.rsplit('/', 1)[-1]}: deployed sha256 {_sha(deployed)[:12]}, repo {_sha(local)[:12]}"
    )
    if len(files) != 1 or deployed != local:
        return Result(
            "D-RULES",
            rules,
            FAIL,
            summary,
            [f"{len(files)} rules file(s) deployed; text differs from firestore.rules"],
        )
    return Result("D-RULES", rules, PASS, summary + " (equal)")


def auth_config_problems(config: dict[str, Any], hosting_host: str) -> list[str]:
    problems = []
    if config.get("signIn", {}).get("anonymous", {}).get("enabled") is not True:
        problems.append("Anonymous sign-in is disabled")
    domains = config.get("authorizedDomains", [])
    if hosting_host not in domains:
        problems.append(f"authorized domains {domains} do not include {hosting_host}")
    return problems


def check_deploy_anon_auth(ctx: Ctx) -> Result:
    rules = ("S-03", "B-03")
    project, _ = _deploy_inputs(ctx)
    host = urllib.parse.urlparse(_hosting_origin(ctx)).hostname or ""
    config = _google_get(ctx, f"{IDENTITY_TOOLKIT_API}/projects/{project}/config", project)
    problems = auth_config_problems(config, host)
    if problems:
        return Result("D-ANON-AUTH", rules, FAIL, "Firebase Auth config is wrong", problems)
    return Result(
        "D-ANON-AUTH",
        rules,
        PASS,
        f"Anonymous sign-in enabled; {host} is an authorized domain (Identity Toolkit)",
    )


def api_key_problems(key: dict[str, Any], hosts: set[str]) -> list[str]:
    """Why a Firebase web API key (API Keys v2 resource) is not locked to the app's sites and to safe APIs."""
    restrictions = key.get("restrictions", {})
    problems = []
    referrers = restrictions.get("browserKeyRestrictions", {}).get("allowedReferrers", [])
    if not referrers:
        problems.append("no HTTP referrer restriction: any site can use the key")
    for ref in referrers:
        host = urllib.parse.urlparse(ref if "://" in ref else f"https://{ref}").hostname or ""
        if host not in hosts:
            problems.append(f"referrer {ref!r} is outside {sorted(hosts)}")
    targets = [t.get("service", "") for t in restrictions.get("apiTargets", [])]
    if not targets:
        problems.append("no API restriction: the key works for every enabled API")
    problems += [f"key may call {t}" for t in targets if t in API_KEY_FORBIDDEN_TARGETS]
    return problems


def check_deploy_api_key(ctx: Ctx) -> Result:
    """The public Firebase web apiKey belongs to the project and is restricted by referrer and by API."""
    rules = ("ENG-E7", "ENG-E9", "S-03")
    project, _ = _deploy_inputs(ctx)
    host = urllib.parse.urlparse(_hosting_origin(ctx)).hostname or ""
    key_string = web_api_key(ctx)
    token = _gcloud_token(ctx) if shutil.which("gcloud") else None
    if token is None:
        raise Blocked("no gcloud access token; cannot read the API key restrictions")
    lookup = _google_get(
        ctx,
        f"{API_KEYS_API}/keys:lookupKey?" + urllib.parse.urlencode({"keyString": key_string}),
        project,
        token=token,
        shown_url=f"{API_KEYS_API}/keys:lookupKey?keyString={redact(key_string)}",
        bill_project=False,
    )
    name = str(lookup.get("name", ""))
    number = _stdout(_gcloud(ctx, "projects", "describe", project, "--format=value(projectNumber)")).strip()
    if not number or not name.startswith(f"projects/{number}/"):
        return Result(
            "D-API-KEY", rules, FAIL, f"the web apiKey is not a key of {project} ({name or 'not found'})"
        )
    key = _google_get(ctx, f"{API_KEYS_API}/{name}", project, token=token, bill_project=False)
    hosts = {host, f"{project}.firebaseapp.com"}
    problems = api_key_problems(key, hosts)
    summary = f"{key.get('displayName', name)} ({name.rsplit('/', 1)[-1]})"
    if problems:
        return Result("D-API-KEY", rules, FAIL, summary, problems)
    return Result("D-API-KEY", rules, PASS, summary + f": referrers within {sorted(hosts)}, APIs restricted")


def check_deploy_budget(ctx: Ctx) -> Result:
    rules = ("S-03", "ENG-E9")
    project, _ = _deploy_inputs(ctx)
    info = _gcloud(ctx, "billing", "projects", "describe", project, "--format=json")
    if not info.ok:
        return Result(
            "D-BUDGET", rules, BLOCKED, f"cannot read billing for {project} (exit {info.exit_code})"
        )
    billing = json.loads(_stdout(info))
    account = (billing.get("billingAccountName") or "").removeprefix("billingAccounts/")
    if not billing.get("billingEnabled") or not account:
        return Result("D-BUDGET", rules, FAIL, f"billing not enabled for {project}")
    number = _stdout(_gcloud(ctx, "projects", "describe", project, "--format=value(projectNumber)")).split()[
        0:1
    ]
    budgets = _gcloud(ctx, "billing", "budgets", "list", f"--billing-account={account}", "--format=json")
    if not budgets.ok:
        status = BLOCKED
        why = "no permission to list budgets" if _permission_problem(budgets) else f"exit {budgets.exit_code}"
        return Result("D-BUDGET", rules, status, f"gcloud billing budgets list: {why}")
    matching = []
    for b in json.loads(_stdout(budgets) or "[]"):
        projects = b.get("budgetFilter", {}).get("projects", [])
        if not projects or any(p.endswith(f"/{number[0]}") for p in projects if number):
            amount = b.get("amount", {}).get("specifiedAmount", {})
            matching.append(
                f"{b.get('displayName')}: {amount.get('units', '?')} {amount.get('currencyCode', '')}"
            )
    if not matching:
        return Result("D-BUDGET", rules, FAIL, f"no budget on billing account covers {project}")
    return Result(
        "D-BUDGET", rules, PASS, f"{len(matching)} budget(s) cover {project}: {'; '.join(matching)}"
    )


def uptime_checks_for(configs: list[dict[str, Any]], host: str) -> list[dict[str, Any]]:
    """Uptime check configs that probe https://<host>/health."""
    found = []
    for c in configs:
        labels = c.get("monitoredResource", {}).get("labels", {})
        path = c.get("httpCheck", {}).get("path", "")
        if labels.get("host") == host and path.rstrip("/") == "/health":
            found.append(c)
    return found


def alerting_policies_for(policies: list[dict[str, Any]], check_ids: set[str]) -> list[str]:
    """Names of enabled alert policies with a condition on one of these uptime checks."""
    names = []
    for p in policies:
        if p.get("enabled") is False:
            continue
        for cond in p.get("conditions", []):
            spec = cond.get("conditionThreshold") or cond.get("conditionAbsent") or {}
            text = spec.get("filter", "")
            if "uptime_check" in text and any(cid in text for cid in check_ids):
                names.append(p.get("displayName") or p.get("name", "?"))
                break
    return names


def check_deploy_uptime(ctx: Ctx) -> Result:
    """An uptime check probes the API's /health and an alert policy fires on it (S-03 until Nov 6)."""
    rules = ("S-03",)
    project, url = _deploy_inputs(ctx)
    host = urllib.parse.urlparse(url).hostname or ""
    run = _gcloud(ctx, "monitoring", "uptime", "list-configs", f"--project={project}", "--format=json")
    if not run.ok:
        why = "no permission" if _permission_problem(run) else f"exit {run.exit_code}"
        return Result("D-UPTIME", rules, BLOCKED, f"gcloud monitoring uptime list-configs: {why}")
    checks = uptime_checks_for(json.loads(_stdout(run) or "[]"), host)
    if not checks:
        return Result(
            "D-UPTIME",
            rules,
            FAIL,
            f"no uptime check on https://{host}/health in {project}: create one "
            f"(Monitoring > Uptime checks, path /health) with an email alert policy",
        )
    check_ids = {str(c.get("name", "")).rsplit("/", 1)[-1] for c in checks} - {""}
    policies = _google_get(ctx, f"{MONITORING_API}/projects/{project}/alertPolicies", project)
    alerting = alerting_policies_for(policies.get("alertPolicies", []), check_ids)
    names = ", ".join(str(c.get("displayName", "?")) for c in checks)
    if not alerting:
        return Result(
            "D-UPTIME", rules, FAIL, f"uptime check {names} exists but no enabled alert policy watches it"
        )
    return Result(
        "D-UPTIME", rules, PASS, f"uptime check {names} on /health; alert policy {', '.join(alerting)}"
    )


# ---- Firestore (production), read with the gcloud login
def _firestore_value(value: dict[str, Any]) -> Any:
    """One Firestore REST value as Python; only the scalar kinds these checks read."""
    if "booleanValue" in value:
        return value["booleanValue"]
    if "stringValue" in value:
        return value["stringValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "nullValue" in value:
        return None
    return value


def firestore_list(
    ctx: Ctx, project: str, collection: str, fields: list[str], token: str
) -> list[dict[str, Any]]:
    """Every document of a top-level collection (REST form), only the given fields, following page tokens."""
    base = f"{FIRESTORE_API}/projects/{project}/databases/(default)/documents/{collection}"
    docs: list[dict[str, Any]] = []
    page = ""
    while True:
        query = urllib.parse.urlencode(
            {"pageSize": 300, "mask.fieldPaths": fields, **({"pageToken": page} if page else {})}, doseq=True
        )
        res = http(
            ctx,
            f"{base}?{query}",
            headers={"Authorization": f"Bearer {token}", "x-goog-user-project": project},
        )
        if res.status != 200:
            why = res.error or f"HTTP {res.status}"
            raise Blocked(f"Firestore REST list of {collection}/ in {project} failed: {why}")
        body = json.loads(res.body or b"{}")
        docs += body.get("documents", [])
        page = body.get("nextPageToken", "")
        if not page:
            return docs


def _firestore_token(ctx: Ctx) -> str:
    if shutil.which("gcloud") is None:
        raise Blocked("gcloud not found on PATH; cannot read production Firestore")
    token_value = _gcloud_token(ctx)
    if token_value is None:
        raise Blocked("no gcloud access token (gcloud auth login)")
    return token_value


def paragraphs_hash(docs: list[dict[str, Any]]) -> str:
    """corpus_hash (corpus.store) of Firestore rbi_paragraphs: sha256 of sorted "<id> <hash>" lines."""
    lines = []
    for d in docs:
        f = d.get("fields", {})
        pid = _firestore_value(f["id"]) if "id" in f else None
        chash = _firestore_value(f["content_hash"]) if "content_hash" in f else None
        lines.append(f"{pid} {chash}\n")
    return _sha("".join(sorted(lines)))


def check_deploy_firestore_corpus(ctx: Ctx) -> Result:
    """Production Firestore holds the whole library (365 paragraphs, 4 instruments, 15 supersession rows)
    and its paragraphs hash to the repo library's corpus hash."""
    rules = ("B-03", "S-03")
    project, _ = _deploy_inputs(ctx)
    token = _firestore_token(ctx)
    counts, problems = {}, []
    paragraphs: list[dict[str, Any]] = []
    for collection, want in EXPECTED_FIRESTORE_COUNTS.items():
        fields = ["id", "content_hash"] if collection == "rbi_paragraphs" else ["__name__"]
        docs = firestore_list(ctx, project, collection, fields, token)
        counts[collection] = len(docs)
        if collection == "rbi_paragraphs":
            paragraphs = docs
        if len(docs) != want:
            problems.append(f"{collection}: {len(docs)} documents, expected {want}")
    remote_hash, local_hash = paragraphs_hash(paragraphs), repo_corpus_hash(ctx)
    if remote_hash != local_hash:
        problems.append(
            f"Firestore paragraphs hash {remote_hash[:12]} differs from corpus_hash.py {local_hash[:12]}"
        )
    summary = ", ".join(f"{k} {v}" for k, v in counts.items()) + f"; paragraphs hash {remote_hash[:12]}"
    if problems:
        return Result("D-FIRESTORE-CORPUS", rules, FAIL, summary, problems)
    return Result("D-FIRESTORE-CORPUS", rules, PASS, summary + " equals the repo library")


def limits_problems(fields: dict[str, Any]) -> list[str]:
    values = {k: _firestore_value(v) for k, v in fields.items()}
    problems = [
        f"{k} is {values.get(k)!r}, expected {v!r}" for k, v in EXPECTED_LIMITS.items() if values.get(k) != v
    ]
    problems += [f"unexpected field {k}" for k in sorted(set(values) - set(EXPECTED_LIMITS))]
    return problems


def check_deploy_limits(ctx: Ctx) -> Result:
    """config/limits reads back as 60 a day, 20 an hour, 5 per user, enabled."""
    rules = ("S-03", "ENG-E9")
    project, _ = _deploy_inputs(ctx)
    token = _firestore_token(ctx)
    url = f"{FIRESTORE_API}/projects/{project}/databases/(default)/documents/config/limits"
    res = http(ctx, url, headers={"Authorization": f"Bearer {token}", "x-goog-user-project": project})
    if res.status == 404:
        return Result("D-LIMITS", rules, FAIL, f"config/limits missing in {project}: every run is refused")
    if res.status != 200:
        raise Blocked(f"Firestore REST get config/limits failed: {res.error or f'HTTP {res.status}'}")
    fields = json.loads(res.body or b"{}").get("fields", {})
    shown = {k: _firestore_value(v) for k, v in sorted(fields.items())}
    problems = limits_problems(fields)
    if problems:
        return Result("D-LIMITS", rules, FAIL, f"config/limits = {shown}", problems)
    return Result("D-LIMITS", rules, PASS, f"config/limits = {shown}")


def sample_problems(doc: dict[str, Any]) -> list[str]:
    """Why one samples/ document (Firestore REST form) may not be public: seeded, or not run on Vertex AI."""
    fields = doc.get("fields", {})
    run_id = str(doc.get("name", "?")).rsplit("/", 1)[-1]
    seeded = _firestore_value(fields["seeded"]) if "seeded" in fields else False
    provider = _firestore_value(fields["provider"]) if "provider" in fields else None
    problems = []
    if seeded is not False:
        problems.append(f"samples/{run_id}: seeded is {seeded!r} (written by a seed script)")
    if provider != SAMPLE_PROVIDER:
        problems.append(f"samples/{run_id}: provider is {provider!r}, not {SAMPLE_PROVIDER!r}")
    return problems


def check_deploy_samples(ctx: Ctx) -> Result:
    rules = ("S-03", "B-01", "B-09")
    project = getattr(ctx.args, "project", None)
    if not project:
        raise Blocked("needs --project")
    docs = firestore_list(ctx, project, "samples", ["seeded", "provider"], _firestore_token(ctx))
    problems = [p for doc in docs for p in sample_problems(doc)]
    if problems:
        return Result(
            "D-SAMPLES", rules, FAIL, f"{len(problems)} problems in {len(docs)} public samples", problems
        )
    if not docs:
        return Result("D-SAMPLES", rules, WARN, f"samples/ in {project} is empty: the app has no saved run")
    return Result(
        "D-SAMPLES",
        rules,
        PASS,
        f"all {len(docs)} samples in {project}: not seeded, provider {SAMPLE_PROVIDER}",
    )


def _preflight(ctx: Ctx, url: str, origin: str) -> HttpResult:
    return http(
        ctx,
        f"{url}/api/runs",
        method="OPTIONS",
        follow=False,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


def check_deploy_cors(ctx: Ctx) -> Result:
    rules = ("S-03", "G9")
    _, url = _deploy_inputs(ctx)
    origin = _hosting_origin(ctx)
    good, evil = _preflight(ctx, url, origin.rstrip("/")), _preflight(ctx, url, EVIL_ORIGIN)
    if good.status is None or evil.status is None:
        return Result("D-CORS", rules, BLOCKED, f"preflight unreachable: {good.error or evil.error}")
    problems = []
    if good.status != 200 or good.headers.get("access-control-allow-origin") != origin.rstrip("/"):
        problems.append(
            f"preflight from {origin}: {good.status}, allow-origin="
            f"{good.headers.get('access-control-allow-origin')!r}"
        )
    if evil.headers.get("access-control-allow-origin") in {EVIL_ORIGIN, "*"}:
        problems.append(f"preflight from {EVIL_ORIGIN} allowed ({evil.status})")
    if problems:
        return Result("D-CORS", rules, FAIL, "CORS is wrong", problems)
    return Result("D-CORS", rules, PASS, f"{origin} allowed; {EVIL_ORIGIN} refused ({evil.status})")


DEPLOY_CHECKS = [
    Check("D-HEALTH", ("S-03", "B-01", "B-02"), check_deploy_health),
    Check("D-WEB", ("S-03", "B-03"), check_deploy_web),
    Check("D-WEB-SECRETS", ("ENG-E7", "S-03", "G1"), check_deploy_web_secrets),
    Check("D-CORS", ("S-03", "G9"), check_deploy_cors),
    Check("D-NO-TOKEN", ("S-03", "ENG-E9"), check_deploy_no_token),
    Check("D-RUN-CONFIG", ("S-03", "B-01", "B-02", "ENG-E7"), check_deploy_run_config),
    Check("D-SA-ROLES", ("B-02", "ENG-E7"), check_deploy_sa_roles),
    Check("D-RULES", ("B-09", "S-03", "G4"), check_deploy_rules),
    Check("D-ANON-AUTH", ("S-03", "B-03"), check_deploy_anon_auth),
    Check("D-API-KEY", ("ENG-E7", "ENG-E9", "S-03"), check_deploy_api_key),
    Check("D-FIRESTORE-CORPUS", ("B-03", "S-03"), check_deploy_firestore_corpus),
    Check("D-LIMITS", ("S-03", "ENG-E9"), check_deploy_limits),
    Check("D-BUDGET", ("S-03", "ENG-E9"), check_deploy_budget),
    Check("D-UPTIME", ("S-03",), check_deploy_uptime),
    Check("D-SAMPLES", ("S-03", "B-01", "B-09"), check_deploy_samples),
]


# --------------------------------------------------------------------------------------------- submission
def _pdf_pages(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise Blocked("pypdf not installed (pip install -r backend/requirements-dev.txt)") from exc
    return [page.extract_text() or "" for page in PdfReader(str(path)).pages]


def _deck(ctx: Ctx) -> tuple[Path, list[str]]:
    path = ctx.sub_path("deck_pdf")
    if path is None:
        raise Blocked("submission JSON has no deck_pdf")
    if not path.is_file():
        raise Blocked(f"deck_pdf not found: {path}")
    pages = _pdf_pages(path)
    ctx.internal("deck-text", "\n\f".join(pages))
    return path, pages


def check_deck(ctx: Ctx) -> Result:
    rules = ("S-01",)
    path, pages = _deck(ctx)
    text = " ".join(" ".join(pages).split())
    if TEMPLATE_INSTRUCTION.lower() in text.lower():
        return Result(
            "S-DECK",
            rules,
            FAIL,
            f"{path.name}: {len(pages)} pages; the organiser instruction slide is still in the deck",
        )
    if not pages:
        return Result("S-DECK", rules, FAIL, f"{path.name}: no pages")
    return Result(
        "S-DECK", rules, PASS, f"{path.name}: PDF with {len(pages)} pages, template instructions removed"
    )


SLIDE1_FIELDS = [
    ("team name", r"team\s+name\s*:"),
    ("team leader", r"team\s+leader(?:\s+name)?\s*:"),
    ("problem statement", r"problem\s+statement\s*:"),
]


def slide1_fields(text: str) -> dict[str, str]:
    flat = " ".join(text.split())
    found = []
    for label, pattern in SLIDE1_FIELDS:
        m = re.search(pattern, flat, re.I)
        if m:
            found.append((m.start(), m.end(), label))
    found.sort()
    values = {}
    for i, (_, end, label) in enumerate(found):
        stop = found[i + 1][0] if i + 1 < len(found) else len(flat)
        values[label] = flat[end:stop].strip()
    return values


def check_deck_slide1(ctx: Ctx) -> Result:
    rules = ("S-07", "B-06")
    _, pages = _deck(ctx)
    values = slide1_fields(pages[0] if pages else "")
    sub = ctx.submission()
    problems = []
    for label, _ in SLIDE1_FIELDS:
        value = values.get(label)
        if value is None:
            problems.append(f"slide 1 has no '{label}:' field")
        elif len(re.findall(r"[A-Za-z]{2,}", value)) < (5 if label == "problem statement" else 1):
            problems.append(f"slide 1 '{label}' is empty or too short: {value!r}")
    for key, label in (("team_name", "team name"), ("team_leader", "team leader")):
        expected = sub.get(key)
        if expected and expected.lower() not in values.get(label, "").lower():
            problems.append(f"slide 1 {label} does not contain {expected!r}")
    if problems:
        return Result("S-DECK-SLIDE1", rules, FAIL, "slide 1 is not filled in", problems)
    return Result(
        "S-DECK-SLIDE1",
        rules,
        PASS,
        f"slide 1: team {values['team name']!r}, leader "
        f"{values['team leader']!r}, problem statement {len(values['problem statement'].split())} words",
    )


def check_video_length(ctx: Ctx) -> Result:
    rules = ("S-02",)
    path = ctx.sub_path("video_file")
    if path is None:
        if ctx.submission().get("video_url"):
            return Result(
                "S-VIDEO-LENGTH", rules, HUMAN, "no video_file given: confirm the video is 3:00-4:00"
            )
        raise Blocked("submission JSON has neither video_file nor video_url")
    if not path.is_file():
        raise Blocked(f"video_file not found: {path}")
    if shutil.which("ffprobe") is None:
        raise Blocked("ffprobe not found (brew install ffmpeg)")
    run = ctx.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    try:
        seconds = float(run.output.split()[0])
    except (ValueError, IndexError):
        return Result("S-VIDEO-LENGTH", rules, FAIL, f"ffprobe could not read the duration of {path.name}")
    shown = f"{int(seconds // 60)}:{int(seconds % 60):02d}"
    if not 180 <= seconds <= 240:
        return Result("S-VIDEO-LENGTH", rules, FAIL, f"{path.name} is {shown}, must be 3:00-4:00")
    return Result("S-VIDEO-LENGTH", rules, PASS, f"{path.name} is {shown} ({seconds:.1f} s)")


def check_video_public(ctx: Ctx) -> Result:
    rules = ("S-02", "S-06")
    url = ctx.submission().get("video_url")
    if not url:
        raise Blocked("submission JSON has no video_url")
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.endswith("youtube.com") or host.endswith("youtu.be"):
        oembed = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
        res = http(ctx, oembed)
        if res.status is None:
            return Result("S-VIDEO-PUBLIC", rules, BLOCKED, f"YouTube oEmbed unreachable: {res.error}")
        if res.status != 200:
            return Result(
                "S-VIDEO-PUBLIC", rules, FAIL, f"YouTube oEmbed returned {res.status} (private or removed)"
            )
        title = json.loads(res.body or b"{}").get("title", "")
        return Result("S-VIDEO-PUBLIC", rules, PASS, f"YouTube oEmbed 200: {title!r}")
    if host.endswith("vimeo.com"):
        res = http(ctx, "https://vimeo.com/api/oembed.json?url=" + urllib.parse.quote(url, safe=""))
        status = PASS if res.status == 200 else BLOCKED if res.status is None else FAIL
        return Result("S-VIDEO-PUBLIC", rules, status, f"Vimeo oEmbed returned {res.status or res.error}")
    if host.endswith("drive.google.com") or host.endswith("docs.google.com"):
        status, why = check_url_public(ctx, url)
        return Result("S-VIDEO-PUBLIC", rules, status, f"Google Drive (no cookies): {why}")
    return Result("S-VIDEO-PUBLIC", rules, FAIL, f"video host {host} is not YouTube, Vimeo or Google Drive")


def check_repo_public(ctx: Ctx) -> Result:
    rules = ("S-04",)
    url = ctx.submission().get("repo_url")
    if not url:
        raise Blocked("submission JSON has no repo_url")
    m = re.match(r"https://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?$", url)
    if not m:
        return Result("S-REPO-PUBLIC", rules, FAIL, f"repo_url is not a GitHub repository URL: {url}")
    res = http(
        ctx, f"https://api.github.com/repos/{m[1]}/{m[2]}", headers={"Accept": "application/vnd.github+json"}
    )
    if res.status is None:
        return Result("S-REPO-PUBLIC", rules, BLOCKED, f"GitHub API unreachable: {res.error}")
    if res.status == 403 and "rate limit" in res.body.decode("utf-8", "replace").lower():
        return Result("S-REPO-PUBLIC", rules, BLOCKED, "GitHub API rate limit hit; retry later")
    if res.status != 200:
        return Result(
            "S-REPO-PUBLIC",
            rules,
            FAIL,
            f"GitHub API returned {res.status} for {m[1]}/{m[2]} (private or missing)",
        )
    data = json.loads(res.body)
    if data.get("private") is not False:
        return Result("S-REPO-PUBLIC", rules, FAIL, f"{m[1]}/{m[2]} is private")
    return Result("S-REPO-PUBLIC", rules, PASS, f"{data.get('full_name')} is public (private == false)")


def check_app_live(ctx: Ctx) -> Result:
    rules = ("S-03",)
    url = ctx.submission().get("app_url")
    if not url:
        raise Blocked("submission JSON has no app_url")
    status, why = check_url_public(ctx, url)
    return Result("S-APP-LIVE", rules, status, why)


SUBMISSION_PLACEHOLDERS = re.compile(r"(?i)\[VERIFY\]|not measured")


def check_description(ctx: Ctx) -> Result:
    rules = ("S-05", "B-03")
    path = ctx.sub_path("description_file")
    if path is None:
        raise Blocked("submission JSON has no description_file")
    if not path.is_file():
        raise Blocked(f"description_file not found: {path}")
    text = path.read_text(encoding="utf-8")
    ctx.internal("description", text)
    problems = [f"does not name {t}" for t in REQUIRED_TECH if not re.search(rf"\b{t}\b", text, re.I)]
    problems += [f"placeholder left: {m.group(0)!r}" for m in SUBMISSION_PLACEHOLDERS.finditer(text)]
    words = len(text.split())
    if problems:
        return Result("S-DESCRIPTION", rules, FAIL, f"description ({words} words) incomplete", problems)
    return Result(
        "S-DESCRIPTION", rules, PASS, f"description ({words} words) names {', '.join(REQUIRED_TECH)}"
    )


def check_readme(ctx: Ctx) -> Result:
    rules = ("S-04", "ENG-E13", "G10")
    text = ctx.text("README.md")
    if text is None:
        return Result("S-README", rules, FAIL, "README.md is not tracked")
    hits = [
        f"README.md:{i}: {ln.strip()[:100]}"
        for i, ln in enumerate(text.splitlines(), 1)
        if SUBMISSION_PLACEHOLDERS.search(ln)
    ]
    if hits:
        return Result("S-README", rules, FAIL, f"{len(hits)} placeholders left in README.md", hits)
    return Result("S-README", rules, PASS, "README.md has no [VERIFY] or 'not measured'")


def check_links(ctx: Ctx) -> Result:
    rules = ("S-06",)
    sub = ctx.submission()
    links = [str(u) for u in (sub.get("links") or [])]
    links += [str(sub[k]) for k in ("app_url", "repo_url", "video_url") if sub.get(k)]
    links = list(dict.fromkeys(links))
    if not links:
        raise Blocked("submission JSON has no links")
    results = [check_url_public(ctx, u) for u in links]
    status = _worst(s for s, _ in results)
    bad = [why for s, why in results if s != PASS]
    if status == PASS:
        return Result("S-LINKS", rules, PASS, f"all {len(links)} links return 200 without cookies")
    return Result("S-LINKS", rules, status, f"{len(bad)} of {len(links)} links not publicly reachable", bad)


def check_submission_phrases(ctx: Ctx) -> Result:
    rules = ("ENG-E13", "S-05")
    texts: dict[str, str] = {}
    missing = []
    path = ctx.sub_path("description_file")
    if path is not None and path.is_file():
        texts[path.name] = path.read_text(encoding="utf-8")
    else:
        missing.append("description_file" if path is None else f"description_file not found: {path}")
    if ctx.submission().get("deck_pdf"):
        try:
            deck, pages = _deck(ctx)
            texts[deck.name] = "\n\n".join(pages)
        except Blocked as exc:
            missing.append(str(exc))
    else:
        missing.append("deck_pdf")
    hits = [
        f"{name}:{line}: {phrase!r}: ...{context}..."
        for name, text in texts.items()
        for line, phrase, context in banned_phrase_hits(text)
    ]
    ctx.internal("submission-phrases", "\n".join(hits) or "ok")
    if hits:
        return Result("S-BANNED-PHRASES", rules, FAIL, f"{len(hits)} banned phrases", hits + missing)
    if not texts:
        raise Blocked("no description_file or deck_pdf to read: " + "; ".join(missing))
    if missing:
        return Result(
            "S-BANNED-PHRASES",
            rules,
            BLOCKED,
            f"no banned phrases in {', '.join(texts)}, but not read: {'; '.join(missing)}",
        )
    return Result("S-BANNED-PHRASES", rules, PASS, f"no banned phrases in {', '.join(texts)}")


SUBMISSION_CHECKS = [
    Check("S-DECK", ("S-01",), check_deck),
    Check("S-DECK-SLIDE1", ("S-07", "B-06"), check_deck_slide1),
    Check("S-VIDEO-LENGTH", ("S-02",), check_video_length),
    Check("S-VIDEO-PUBLIC", ("S-02", "S-06"), check_video_public),
    Check("S-REPO-PUBLIC", ("S-04",), check_repo_public),
    Check("S-APP-LIVE", ("S-03",), check_app_live),
    Check("S-DESCRIPTION", ("S-05", "B-03"), check_description),
    Check("S-README", ("S-04", "ENG-E13", "G10"), check_readme),
    Check("S-LINKS", ("S-06",), check_links),
    Check("S-BANNED-PHRASES", ("ENG-E13", "S-05"), check_submission_phrases),
]


def _human_check(check_id: str, rules: tuple[str, ...], text: str) -> Check:
    def fn(_ctx: Ctx) -> Result:
        return Result(check_id, rules, HUMAN, text)

    return Check(check_id, rules, fn)


def human_checks(stage: str) -> list[Check]:
    return [_human_check(c, r, t) for c, r, t, stages in HUMAN_ITEMS if stage in stages]


def stage_checks(stage: str, fast: bool) -> list[Check]:
    commit = [c for c in COMMIT_CHECKS if not (fast and c.slow)]
    if stage == "commit":
        return commit
    if stage == "deploy":
        return DEPLOY_CHECKS + human_checks("deploy")
    if stage == "submission":
        return SUBMISSION_CHECKS + commit + human_checks("submission")
    raise ValueError(stage)


# --------------------------------------------------------------------------------------------- coverage
def coverage() -> dict[str, list[str]]:
    cov: dict[str, list[str]] = {rule: [] for rule in RULES}
    for stage, checks in (
        ("commit", COMMIT_CHECKS),
        ("deploy", DEPLOY_CHECKS),
        ("submission", SUBMISSION_CHECKS),
    ):
        for c in checks:
            for rule in c.rules:
                if rule in cov:
                    cov[rule].append(f"{c.check_id} ({stage})")
    for check_id, rules, _text, _ in HUMAN_ITEMS:
        for rule in rules:
            cov.setdefault(rule, []).append(f"{check_id} (HUMAN)")
    return cov


def rules_in_file(path: Path) -> set[str]:
    return set(re.findall(r"^\|\s*([ETBS]-\d{2})\s*\|", path.read_text(encoding="utf-8"), re.M))


def cmd_rules_coverage(args: argparse.Namespace) -> int:
    cov = coverage()
    failed = False
    print(f"{'RULE':<6} {'HOW':<9} COVERED BY")
    for rule in sorted(cov, key=lambda r: ("ETBS".index(r[0]), r)):
        auto = [c for c in cov[rule] if not c.endswith("(HUMAN)")]
        human = [c for c in cov[rule] if c.endswith("(HUMAN)")]
        how = "auto+hum" if auto and human else "auto" if auto else "human" if human else "MISSING"
        failed |= how == "MISSING"
        print(f"{rule:<6} {how:<9} {', '.join(auto + human) or '-'}   # {RULES.get(rule, '?')}")
    if args.rules_file:
        listed = rules_in_file(Path(args.rules_file))
        unknown, stale = sorted(listed - set(cov)), sorted(set(RULES) - listed)
        for rule in unknown:
            print(f"{rule:<6} MISSING   in {args.rules_file} but not known to check.py")
        for rule in stale:
            print(f"{rule:<6} STALE     known to check.py but not in {args.rules_file}")
        failed |= bool(unknown or stale)
        print(
            f"compared with {args.rules_file}: {len(listed)} rule IDs, "
            f"{len(unknown)} unknown, {len(stale)} stale"
        )
    counts = {k: sum(1 for r in RULES if _how(cov[r]) == k) for k in ("auto", "auto+hum", "human", "MISSING")}
    print(
        f"summary: {len(RULES)} rules; automatic only {counts['auto']}, "
        f"automatic + human {counts['auto+hum']}, "
        f"human only {counts['human']}, missing {counts['MISSING']}"
    )
    return 1 if failed else 0


def _how(entries: list[str]) -> str:
    auto = any(not e.endswith("(HUMAN)") for e in entries)
    human = any(e.endswith("(HUMAN)") for e in entries)
    return "auto+hum" if auto and human else "auto" if auto else "human" if human else "MISSING"


# --------------------------------------------------------------------------------------------- runner
def run_checks(ctx: Ctx, checks: list[Check]) -> list[Result]:
    results = []
    for check in checks:
        ctx.runs = []
        try:
            result = check.fn(ctx)
        except Blocked as exc:
            result = Result(check.check_id, check.rules, BLOCKED, str(exc))
        except Exception as exc:  # a crash in a check is a FAIL of the checker, never a silent pass
            result = Result(check.check_id, check.rules, FAIL, f"checker error: {type(exc).__name__}: {exc}")
        if not ctx.runs:  # BLOCKED and HUMAN results still leave a trace of why
            ctx.internal(f"{result.status.lower()}", result.reason)
        result.runs = ctx.runs
        results.append(result)
    return results


def git_state(repo: Path) -> tuple[str, bool]:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=False)
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=False
    )
    return (sha.stdout.strip() or "no-commits"), bool(dirty.stdout.strip())


def write_evidence(directory: Path, results: list[Result], sha: str, dirty: bool) -> None:
    raw_dir = directory / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    with (directory / "evidence.jsonl").open("a", encoding="utf-8") as out:
        for r in results:
            commands = []
            for i, run in enumerate(r.runs, 1):
                raw_file = None
                if run.store_raw:
                    raw_file = f"raw/{r.check_id}.{i}.log"
                    (directory / raw_file).write_text(
                        f"$ {run.command}\n# cwd {run.cwd}\n# exit {run.exit_code}\n{run.output}",
                        encoding="utf-8",
                    )
                commands.append(
                    {
                        "command": run.command,
                        "cwd": run.cwd,
                        "exit_code": run.exit_code,
                        "output_sha256": run.sha256,
                        "started_utc": run.started_utc,
                        "duration_s": run.duration_s,
                        "raw_file": raw_file,
                    }
                )
            out.write(
                json.dumps(
                    {
                        "check_id": r.check_id,
                        "rules": list(r.rules),
                        "status": r.status,
                        "reason": r.reason,
                        "details": r.details,
                        "git_sha": sha,
                        "worktree_dirty": dirty,
                        "utc": utc_now(),
                        "commands": commands,
                    }
                )
                + "\n"
            )


def print_results(results: list[Result], stage: str, skipped: list[str], sha: str, dirty: bool) -> None:
    for r in results:
        print(f"{r.check_id:<18} {','.join(r.rules):<26} {r.status:<7} {r.reason}")
        if r.status in {FAIL, WARN, BLOCKED} and r.details:
            for line in r.details[:25]:
                print(f"{'':<18}   - {line}")
            if len(r.details) > 25:
                print(f"{'':<18}   ... {len(r.details) - 25} more")
    counts = {s: sum(1 for r in results if r.status == s) for s in STATUSES}
    print("-" * 100)
    print(
        f"stage {stage} at {sha[:12]}{' (uncommitted changes)' if dirty else ''}: "
        + ", ".join(f"{counts[s]} {s}" for s in STATUSES)
    )
    if skipped:
        print(f"skipped with --fast (not passed): {', '.join(skipped)}")
    humans = [r for r in results if r.status == HUMAN]
    if humans:
        print("a person must confirm:")
        for r in humans:
            print(f"  [{','.join(r.rules)}] {r.reason}")
    verdict = "FAIL" if counts[FAIL] else "NOT COMPLETE" if counts[BLOCKED] else "PASS (automatic checks)"
    print(f"result: {verdict}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=["commit", "deploy", "submission", "rules-coverage"])
    p.add_argument("--fast", action="store_true", help="skip the slow test suites (pre-commit hook)")
    p.add_argument("--json", metavar="PATH", help="write machine-readable results ('-' for stdout)")
    p.add_argument("--evidence-dir", metavar="DIR", help="append evidence.jsonl and raw outputs here")
    p.add_argument("--strict", action="store_true", help="also exit 1 when a check is BLOCKED")
    p.add_argument("--repo", default=".", help="repository root (default: current directory)")
    p.add_argument("--venv", help="directory holding ruff/mypy/python (default: backend/.venv/bin)")
    p.add_argument("--node-dir", help="directory holding node (default: ARGUS_NODE_DIR or PATH)")
    p.add_argument(
        "--project", default=DEFAULT_PROJECT, help=f"deploy: Google Cloud project id ({DEFAULT_PROJECT})"
    )
    p.add_argument("--service-url", default=DEFAULT_SERVICE_URL, help="deploy: Cloud Run service URL")
    p.add_argument("--service", help="deploy: Cloud Run service name (default: found from --service-url)")
    p.add_argument("--region", help="deploy: Cloud Run region (default: found from --service-url)")
    p.add_argument(
        "--hosting-origin",
        default=DEFAULT_HOSTING_ORIGIN,
        help="deploy: Firebase Hosting origin (the web app)",
    )
    p.add_argument("--expect-sha", help="deploy: /health git_sha must equal this commit")
    p.add_argument(
        "--allow-min-zero",
        action="store_true",
        help="deploy: accept min instances 0 (cost saving before the final gate, D-49); default needs 1",
    )
    p.add_argument(
        "--submission",
        help="submission: JSON with deck_pdf, video_file, video_url, repo_url, "
        "app_url, description_file, links[], team_name, team_leader; or a .md file, read as description_file",
    )
    p.add_argument("--rules-file", help="rules-coverage: RULES.md to compare the rule IDs with")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.stage == "rules-coverage":
        return cmd_rules_coverage(args)
    repo = Path(args.repo).resolve()
    node_dir = args.node_dir or os.environ.get("ARGUS_NODE_DIR")
    ctx = Ctx(
        repo,
        venv_bin=Path(args.venv) if args.venv else None,
        node_dir=Path(node_dir) if node_dir else None,
        args=args,
    )
    checks = stage_checks(args.stage, args.fast)
    skipped = [c.check_id for c in COMMIT_CHECKS if args.fast and c.slow] if args.stage != "deploy" else []
    results = run_checks(ctx, checks)
    sha, dirty = git_state(repo)
    print_results(results, args.stage, skipped, sha, dirty)
    failed = any(r.status == FAIL for r in results) or (
        args.strict and any(r.status == BLOCKED for r in results)
    )
    if args.evidence_dir:
        write_evidence(Path(args.evidence_dir), results, sha, dirty)
    if args.json:
        payload = json.dumps(
            {
                "stage": args.stage,
                "git_sha": sha,
                "worktree_dirty": dirty,
                "utc": utc_now(),
                "fast": args.fast,
                "skipped": skipped,
                "exit_code": 1 if failed else 0,
                "summary": {s: sum(1 for r in results if r.status == s) for s in STATUSES},
                "results": [
                    {
                        "id": r.check_id,
                        "rules": list(r.rules),
                        "status": r.status,
                        "reason": r.reason,
                        "details": r.details,
                    }
                    for r in results
                ],
            },
            indent=2,
        )
        if args.json == "-":
            print(payload)
        else:
            Path(args.json).write_text(payload + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
