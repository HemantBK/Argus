#!/usr/bin/env python3
"""End-to-end checks against the deployed Argus stack (VERIFICATION_PLAN.md "Cloud verification", step 3-6).

Run with the backend venv from the repo root:

  backend/.venv/bin/python scripts/cloud_e2e.py smoke           1 live run of policy_a; FAIL on any failed
                                                                 clause or a "model turn" log line
  backend/.venv/bin/python scripts/cloud_e2e.py hero            4 live runs, serial: policy_a, policy_a,
                                                                 policy_b, policy_b; Firestore oracle per run
  backend/.venv/bin/python scripts/cloud_e2e.py rules [--run-id ID]
                                                                 security rules with real anonymous tokens
                                                                 (REST only, no model calls)
  backend/.venv/bin/python scripts/cloud_e2e.py logs [--run-id ID ...]
                                                                 every agent / monitor model call has a log
                                                                 line; ERROR count in the run window
  backend/.venv/bin/python scripts/cloud_e2e.py faults          per-user cap -> 429, kill switch -> 503;
                                                                 config/limits restored and read back

Every subcommand writes <out>/<gate>.json = {gate, command, git_sha, revision, started_utc, exit_code, pass,
details} plus <gate>.log (HTTP and gcloud transcript, secrets redacted) and <gate>.data.json (documents read
back). PASS is the conjunction of the explicit criteria listed in details.criteria; exit code 0 on PASS, 1 on
FAIL, 2 when the gate could not run (blocked).

Spend safety: only smoke and hero call Gemini (through POST /api/runs). They hold an exclusive lock, so two
live gates never run at once, and every POST is counted in a ledger in the state file; a POST that would take
the ledger past --budget (default 14) is refused. Test users are anonymous Firebase users minted with the
public web API key; their refresh tokens live only in the state file (mode 600, outside the repo), never in
evidence, so `rules` and `logs` can reuse a run made by smoke or hero.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
WORKSPACE = REPO.parent
EVIDENCE_DATE = "2026-10-17"
DEFAULT_OUT = WORKSPACE / "verification" / EVIDENCE_DATE / "cloud"
DEFAULT_STATE = Path.home() / ".argus-cloud-e2e" / "state.json"
WEB_ENV = REPO / "web" / ".env.production.local"
EXPECTED = BACKEND / "argus_api" / "samples" / "expected.json"

PROJECT = "argus-aibc"
REGION = "asia-south1"
SERVICE = "argus-api"
API = "https://argus-api-419264532549.asia-south1.run.app"
WEB = "https://argus-aibc.web.app"
FIRESTORE = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"
SIGNUP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signUp"
REFRESH_URL = "https://securetoken.googleapis.com/v1/token"
IST = ZoneInfo("Asia/Kolkata")

DEFAULT_BUDGET = 14
RUN_TIMEOUT_S = 330  # Cloud Run request timeout is 300 s
MODEL_TURN = "Requests ending with a model turn"
LOG_SETTLE_S = 150
LOG_POLL_S = 10
ERROR_WINDOW_PAD_S = 30
HERO_SEQUENCE = ("policy_a", "policy_a", "policy_b", "policy_b")
LIMIT_FIELDS = ("daily_run_cap", "hourly_run_cap", "per_user_daily_run_cap", "enabled")
CALL_FIELDS = (
    "run_id",
    "clause_id",
    "model",
    "served_model",
    "prompt_sha256",
    "prompt_chars",
    "output_chars",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "cost_usd_estimate",
)
SECRET_KEYS = ("idToken", "refreshToken", "id_token", "refresh_token", "access_token", "accessToken")


class Blocked(RuntimeError):
    """The gate could not run (missing tool, lock held, budget spent): exit 2, evidence pass false."""


# ---- redaction and evidence ----------------------------------------------------------------------------


def redact(text: str) -> str:
    """Strip API keys from URLs and token values from JSON bodies before anything is written or printed."""
    text = re.sub(r"([?&]key=)[^&\s\"']+", r"\1REDACTED", text)
    for k in SECRET_KEYS:
        text = re.sub(rf'("{k}"\s*:\s*")[^"]*(")', r"\1REDACTED\2", text)
        text = re.sub(rf"({k}=)[^&\s\"']+", r"\1REDACTED", text)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1REDACTED", text)
    return text


@dataclass
class Criterion:
    name: str
    ok: bool
    observed: Any = None
    expected: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "observed": self.observed, "expected": self.expected}


def judge(criteria: list[Criterion]) -> bool:
    """PASS only when there is at least one criterion and every one holds."""
    return bool(criteria) and all(c.ok for c in criteria)


@dataclass
class Gate:
    name: str
    out: Path
    started_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    criteria: list[Criterion] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    transcript: list[str] = field(default_factory=list)
    git_sha: str = "unknown"
    revision: str = "unknown"

    def check(self, name: str, ok: bool, observed: Any = None, expected: Any = None) -> bool:
        self.criteria.append(Criterion(name, bool(ok), observed, expected))
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {name}: observed={_short(observed)} expected={_short(expected)}", flush=True)
        return bool(ok)

    def log(self, line: str) -> None:
        self.transcript.append(f"{datetime.now(UTC).isoformat(timespec='seconds')} {redact(line)}")

    def finish(self, blocked: str | None = None) -> int:
        passed = blocked is None and judge(self.criteria)
        exit_code = 0 if passed else (2 if blocked is not None else 1)
        if blocked is not None:
            self.details["blocked"] = blocked
        self.details["criteria"] = [c.as_dict() for c in self.criteria]
        self.details["failed_criteria"] = [c.name for c in self.criteria if not c.ok]
        evidence = {
            "gate": self.name,
            "command": " ".join(shlex.quote(a) for a in sys.argv),
            "git_sha": self.git_sha,
            "revision": self.revision,
            "started_utc": self.started_utc,
            "exit_code": exit_code,
            "pass": passed,
            "details": self.details,
        }
        self.out.mkdir(parents=True, exist_ok=True)
        _write(self.out / f"{self.name}.json", json.dumps(evidence, indent=2, default=str) + "\n")
        _write(self.out / f"{self.name}.log", "\n".join(self.transcript) + "\n")
        _write(self.out / f"{self.name}.data.json", json.dumps(self.data, indent=2, default=str) + "\n")
        verdict = "PASS" if passed else ("BLOCKED" if blocked else "FAIL")
        print(f"{self.name}: {verdict} (exit {exit_code}) -> {self.out / (self.name + '.json')}")
        if blocked:
            print(f"  blocked: {blocked}")
        return exit_code


def _write(path: Path, text: str) -> None:
    path.write_text(redact(text), encoding="utf-8")


def _short(value: Any, limit: int = 160) -> str:
    text = json.dumps(value, default=str) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---- HTTP ----------------------------------------------------------------------------------------------


@dataclass
class Resp:
    status: int
    text: str

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError:
            return None


def http(
    gate: Gate | None,
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: Any = None,
    form: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> Resp:
    hdrs = dict(headers or {})
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = Resp(r.status, r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        resp = Resp(e.code, e.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        resp = Resp(0, f"{type(e).__name__}: {e}")
    ms = round((time.perf_counter() - started) * 1000)
    if gate is not None:
        auth = "bearer" if token else "none"
        gate.log(f"{method} {url} auth={auth} -> {resp.status} in {ms} ms: {resp.text[:1500]!r}")
    return resp


# ---- Firestore REST values -----------------------------------------------------------------------------


def decode_value(v: dict[str, Any]) -> Any:
    if "nullValue" in v:
        return None
    if "booleanValue" in v:
        return bool(v["booleanValue"])
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "stringValue" in v:
        return v["stringValue"]
    if "timestampValue" in v:
        return v["timestampValue"]
    if "mapValue" in v:
        return decode_fields(v["mapValue"].get("fields", {}))
    if "arrayValue" in v:
        return [decode_value(x) for x in v["arrayValue"].get("values", [])]
    for k in ("referenceValue", "bytesValue", "geoPointValue"):
        if k in v:
            return v[k]
    raise ValueError(f"unknown Firestore value {sorted(v)}")


def decode_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: decode_value(v) for k, v in fields.items()}


def encode_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {k: encode_value(v) for k, v in value.items()}}}
    if isinstance(value, list):
        return {"arrayValue": {"values": [encode_value(v) for v in value]}}
    raise TypeError(f"cannot encode {type(value).__name__}")


class Firestore:
    """Firestore REST with one bearer token: a Firebase ID token (rules apply) or a gcloud access token
    (IAM, rules bypassed), or none (rules apply, unauthenticated)."""

    def __init__(self, gate: Gate | None, token: str | None) -> None:
        self.gate = gate
        self.token = token

    def get(self, path: str) -> tuple[int, dict[str, Any] | None]:
        r = http(self.gate, "GET", f"{FIRESTORE}/{path}", token=self.token)
        doc = r.json() if r.status == 200 else None
        return r.status, (decode_fields(doc.get("fields", {})) if isinstance(doc, dict) else None)

    def list(self, path: str, page_size: int = 300) -> tuple[int, list[tuple[str, dict[str, Any]]]]:
        out: list[tuple[str, dict[str, Any]]] = []
        page = ""
        while True:
            query = {"pageSize": str(page_size)}
            if page:
                query["pageToken"] = page
            r = http(
                self.gate, "GET", f"{FIRESTORE}/{path}?{urllib.parse.urlencode(query)}", token=self.token
            )
            if r.status != 200:
                return r.status, out
            body = r.json() or {}
            for d in body.get("documents", []):
                out.append((d["name"].rsplit("/", 1)[1], decode_fields(d.get("fields", {}))))
            page = body.get("nextPageToken", "")
            if not page:
                return 200, out

    def create(self, collection: str, doc_id: str, data: dict[str, Any]) -> int:
        url = f"{FIRESTORE}/{collection}?documentId={urllib.parse.quote(doc_id)}"
        body = {"fields": {k: encode_value(v) for k, v in data.items()}}
        return http(self.gate, "POST", url, token=self.token, body=body).status

    def patch(self, path: str, data: dict[str, Any]) -> int:
        """Update only the named fields of an existing document (never creates one)."""
        query = [("updateMask.fieldPaths", k) for k in data] + [("currentDocument.exists", "true")]
        url = f"{FIRESTORE}/{path}?{urllib.parse.urlencode(query)}"
        body = {"fields": {k: encode_value(v) for k, v in data.items()}}
        return http(self.gate, "PATCH", url, token=self.token, body=body).status


# ---- environment: git, gcloud, health, users ------------------------------------------------------------


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def gcloud_bin() -> str:
    found = shutil.which("gcloud")
    if found:
        return found
    home = Path.home() / "google-cloud-sdk" / "bin" / "gcloud"
    if home.exists():
        return str(home)
    raise Blocked("gcloud not found on PATH or in ~/google-cloud-sdk/bin")


def gcloud(gate: Gate | None, *args: str, timeout: float = 120) -> str:
    cmd = [gcloud_bin(), *args, f"--project={PROJECT}", "--quiet"]
    if gate is not None:
        gate.log("$ " + " ".join(shlex.quote(a) for a in cmd[1:]))
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise Blocked(f"gcloud {args[0]} timed out after {timeout:g} s") from e
    if gate is not None:
        gate.log(f"  exit {out.returncode}; stderr: {out.stderr.strip()[:500]!r}")
    if out.returncode != 0:
        raise Blocked(f"gcloud {' '.join(args[:3])} failed: {out.stderr.strip()[:300]}")
    return out.stdout


def admin_token(gate: Gate | None) -> str:
    """The operator's (project owner's) OAuth access token: Firestore REST with it bypasses the rules."""
    cmd = [gcloud_bin(), "auth", "print-access-token"]
    if gate is not None:
        gate.log("$ gcloud auth print-access-token  (value not recorded)")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0 or not out.stdout.strip():
        raise Blocked("gcloud auth print-access-token failed; run gcloud auth login")
    return out.stdout.strip()


def current_revision(gate: Gate) -> str:
    try:
        out = gcloud(
            gate,
            "run",
            "services",
            "describe",
            SERVICE,
            f"--region={REGION}",
            "--format=value(status.latestReadyRevisionName)",
        )
        return out.strip() or "unknown"
    except Blocked as e:
        gate.details["revision_error"] = str(e)
        return "unknown"


def health(gate: Gate) -> dict[str, Any]:
    r = http(gate, "GET", f"{API}/health", timeout=60)
    body = r.json()
    if r.status != 200 or not isinstance(body, dict):
        raise Blocked(f"/health answered {r.status}")
    gate.details["health"] = body
    return body


def web_api_key(path: Path = WEB_ENV) -> str:
    if not path.exists():
        raise Blocked(f"{path} is missing (needs VITE_FIREBASE_API_KEY)")
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*VITE_FIREBASE_API_KEY\s*=\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().strip("\"'")
    raise Blocked(f"VITE_FIREBASE_API_KEY not found in {path}")


@dataclass
class User:
    uid: str
    id_token: str
    refresh_token: str


def _referer() -> dict[str, str]:
    # The web API key is restricted by HTTP referrer to the hosted app.
    return {"Referer": f"{WEB}/"}


def mint_user(gate: Gate, key: str) -> User:
    r = http(gate, "POST", f"{SIGNUP_URL}?key={key}", body={"returnSecureToken": True}, headers=_referer())
    body = r.json()
    if r.status != 200 or not isinstance(body, dict) or "idToken" not in body:
        raise Blocked(f"anonymous signUp answered {r.status}")
    return User(body["localId"], body["idToken"], body["refreshToken"])


def refresh_user(gate: Gate, key: str, uid: str, refresh_token: str) -> User:
    r = http(
        gate,
        "POST",
        f"{REFRESH_URL}?key={key}",
        form={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers=_referer(),
    )
    body = r.json()
    if r.status != 200 or not isinstance(body, dict) or "id_token" not in body:
        raise Blocked(f"token refresh for {uid} answered {r.status}")
    if body.get("user_id") != uid:
        raise Blocked(f"refreshed token belongs to {body.get('user_id')}, not {uid}")
    return User(uid, body["id_token"], body["refresh_token"])


# ---- state: owners of runs, the spend ledger, the live lock ---------------------------------------------


class State:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"ledger": [], "owners": {}}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
            self.data.setdefault("ledger", [])
            self.data.setdefault("owners", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    def ledger_count(self) -> int:
        return len(self.data["ledger"])

    def record_post(self, gate: str, run_id: str, policy_id: str, status: int | None) -> None:
        self.data["ledger"].append(
            {
                "gate": gate,
                "run_id": run_id,
                "policy_id": policy_id,
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "http_status": status,
            }
        )
        self.save()

    def set_status(self, run_id: str, status: int) -> None:
        for row in self.data["ledger"]:
            if row["run_id"] == run_id:
                row["http_status"] = status
        self.save()

    def remember_owner(self, run_id: str, user: User, gate: str, policy_id: str) -> None:
        self.data["owners"][run_id] = {
            "uid": user.uid,
            "refresh_token": user.refresh_token,
            "gate": gate,
            "policy_id": policy_id,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.save()

    def newest_owned_run(self) -> str | None:
        owners = self.data["owners"]
        if not owners:
            return None
        newest: str = max(owners, key=lambda r: str(owners[r]["at"]))
        return newest

    def runs_of_last(self, gate: str) -> list[str]:
        rows = [r for r in self.data["ledger"] if r["gate"] == gate and r.get("http_status") == 200]
        if not rows:
            return []
        # The last invocation of that gate: rows within 30 minutes of its last POST.
        last = datetime.fromisoformat(rows[-1]["at"])
        return [r["run_id"] for r in rows if last - datetime.fromisoformat(r["at"]) < timedelta(minutes=30)]


@contextlib.contextmanager
def live_lock(state_path: Path) -> Iterator[None]:
    """Exclusive, non-blocking: a second live gate refuses to start instead of running Vertex in parallel."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(".lock")
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise Blocked(f"another live gate holds {lock_path}; Vertex runs must be serial") from e
        yield
    finally:
        os.close(fd)


# ---- live runs -----------------------------------------------------------------------------------------


def post_run(gate: Gate, state: State, budget: int, user: User, policy_id: str) -> tuple[str, Resp]:
    if state.ledger_count() >= budget:
        raise Blocked(
            f"spend ledger holds {state.ledger_count()} live POSTs; budget {budget} reached ({state.path})"
        )
    run_id = uuid.uuid4().hex
    state.record_post(gate.name, run_id, policy_id, None)
    state.remember_owner(run_id, user, gate.name, policy_id)
    print(
        f"  POST /api/runs {policy_id} run_id={run_id} (ledger {state.ledger_count()}/{budget})", flush=True
    )
    r = http(
        gate,
        "POST",
        f"{API}/api/runs",
        token=user.id_token,
        body={"run_id": run_id, "policy_id": policy_id},
        timeout=RUN_TIMEOUT_S,
    )
    state.set_status(run_id, r.status)
    return run_id, r


@dataclass
class RunDocs:
    run: dict[str, Any] | None
    steps: list[dict[str, Any]]
    findings: dict[str, dict[str, Any]]
    audit: list[dict[str, Any]]
    statuses: dict[str, int]


def read_run(fs: Firestore, run_id: str) -> RunDocs:
    s_run, run = fs.get(f"runs/{run_id}")
    s_steps, steps = fs.list(f"runs/{run_id}/steps")
    s_find, findings = fs.list(f"runs/{run_id}/findings")
    s_audit, audit = fs.list(f"runs/{run_id}/audit")
    return RunDocs(
        run,
        sorted((d for _, d in steps), key=lambda d: d.get("seq", 0)),
        dict(findings),
        sorted((d for _, d in audit), key=lambda d: d.get("seq", 0)),
        {"run": s_run, "steps": s_steps, "findings": s_find, "audit": s_audit},
    )


# ---- the hero oracle (pure; tested offline) -------------------------------------------------------------

QuoteVerifier = Callable[[str, str, str], bool]


def citations_of(finding: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(k, finding[k]) for k in ("citation", "replaced_citation") if isinstance(finding.get(k), dict)]


def has_monitor_step(steps: list[dict[str, Any]], clause_id: str, action: str) -> list[dict[str, Any]]:
    return [
        s
        for s in steps
        if s.get("clause_id") == clause_id
        and s.get("kind") == "monitor"
        and isinstance(s.get("verdict"), dict)
        and s["verdict"].get("action") == action
    ]


def label_agreement(
    policy_id: str, findings: dict[str, dict[str, Any]], expected: dict[str, Any]
) -> dict[str, Any]:
    gold = expected["policies"][policy_id]
    rows = {
        cid: {"expected": g["label"], "got": findings.get(cid, {}).get("label")} for cid, g in gold.items()
    }
    agree = sum(1 for r in rows.values() if r["expected"] == r["got"])
    return {"agree": agree, "n": len(gold), "clauses": rows}


def hero_criteria(
    tag: str,
    policy_id: str,
    docs: RunDocs,
    expected: dict[str, Any],
    verify: QuoteVerifier,
) -> list[Criterion]:
    """Explicit pass criteria for one hero run, from the documents read back from Firestore."""
    c: list[Criterion] = []
    run = docs.run or {}
    f = docs.findings
    c.append(
        Criterion(f"{tag}.readback_200", all(v == 200 for v in docs.statuses.values()), docs.statuses, 200)
    )
    c.append(Criterion(f"{tag}.status_done", run.get("status") == "done", run.get("status"), "done"))
    summary_failed = (run.get("summary") or {}).get("failed")
    errors = {cid: d.get("error") for cid, d in f.items() if d.get("error")}
    c.append(
        Criterion(
            f"{tag}.zero_failed_clauses",
            summary_failed == 0 and not errors,
            {"summary.failed": summary_failed, "errors": errors},
            0,
        )
    )
    c.append(
        Criterion(f"{tag}.provider_vertex", run.get("provider") == "vertex", run.get("provider"), "vertex")
    )
    want_ids = sorted(expected["policies"][policy_id])
    c.append(Criterion(f"{tag}.pinned_clause_ids", sorted(f) == want_ids, sorted(f), want_ids))
    c.append(Criterion(f"{tag}.steps_present", len(docs.steps) > 0, len(docs.steps), ">0"))
    events = [a.get("event") for a in docs.audit]
    seqs = [a.get("seq") for a in docs.audit]
    c.append(
        Criterion(
            f"{tag}.audit_complete",
            bool(events)
            and events[0] == "run_started"
            and "run_finished" in events
            and seqs == list(range(1, len(seqs) + 1))
            and sum(1 for e in events if e in ("finding_recorded", "clause_failed")) == len(want_ids),
            {"n": len(events), "first": events[:1], "has_run_finished": "run_finished" in events},
            "run_started first, run_finished present, seq 1..n, one finding row per clause",
        )
    )
    bad_quotes = []
    n_quotes = 0
    for cid, d in sorted(f.items()):
        for kind, cit in citations_of(d):
            n_quotes += 1
            ok = verify(str(cit.get("instrument_ref")), str(cit.get("para")), str(cit.get("quote")))
            if not ok:
                bad_quotes.append(f"{cid}.{kind} {cit.get('instrument_ref')} para {cit.get('para')}")
    c.append(
        Criterion(
            f"{tag}.quotes_verbatim",
            n_quotes > 0 and not bad_quotes,
            {"checked": n_quotes, "failed": bad_quotes},
            "every quote verbatim",
        )
    )

    if policy_id == "policy_a":
        c2 = f.get("c2", {})
        rep = c2.get("replaced_citation") or {}
        cur = c2.get("citation") or {}
        got = {
            "label": c2.get("label"),
            "replaced.instrument_ref": rep.get("instrument_ref"),
            "replaced.withdrawn_by": rep.get("withdrawn_by"),
            "citation.instrument_ref": cur.get("instrument_ref"),
            "citation.para": cur.get("para"),
            "citation.updated_as_on": cur.get("updated_as_on"),
        }
        want = {
            "label": "outdated_reference",
            "replaced.instrument_ref": "RBI/2025-26/36",
            "replaced.withdrawn_by": "RBI/2025-26/100",
            "citation.instrument_ref": "RBI/DOR/2025-26/347",
            "citation.para": "11(1)",
            "citation.updated_as_on": "2026-07-15",
        }
        c.append(Criterion(f"{tag}.a_c2_outdated_chain", got == want, got, want))
        revise = has_monitor_step(docs.steps, "c6", "revise")
        c6 = {"monitor_revise_steps": len(revise), "label": f.get("c6", {}).get("label")}
        c.append(
            Criterion(
                f"{tag}.a_c6_revise_then_insufficient",
                len(revise) >= 1 and c6["label"] == "insufficient_evidence",
                c6,
                {"monitor_revise_steps": ">=1", "label": "insufficient_evidence"},
            )
        )
    elif policy_id == "policy_b":
        stops = has_monitor_step(docs.steps, "c2", "stop")
        details = [
            ch.get("detail", "")
            for s in stops
            for ch in s["verdict"].get("checks", [])
            if ch.get("name") == "no_injection"
        ]
        hit = any("pre-cleared" in d.lower() for d in details)
        c.append(
            Criterion(
                f"{tag}.b_c2_stop_quotes_injection",
                bool(stops) and hit,
                {"monitor_stop_steps": len(stops), "no_injection_details": [d[:200] for d in details]},
                "a monitor stop step on c2 whose no_injection detail contains 'pre-cleared'",
            )
        )
    return c


def model_turn_hits(docs: RunDocs, log_entries: list[dict[str, Any]]) -> list[str]:
    hits = [
        f"finding {cid}: error" for cid, d in docs.findings.items() if MODEL_TURN in (d.get("error") or "")
    ]
    if MODEL_TURN in ((docs.run or {}).get("error") or ""):
        hits.append("run: error")
    hits += [
        f"log {e.get('timestamp')} {e.get('severity')}"
        for e in log_entries
        if MODEL_TURN in json.dumps(e, default=str)
    ]
    return hits


# ---- logs (pure parts tested offline) -------------------------------------------------------------------


def expected_calls(steps: list[dict[str, Any]]) -> Counter[tuple[str, str, str]]:
    """(event, clause_id, model) per step that carries a model call: one step per call (agent/loop.py)."""
    out: Counter[tuple[str, str, str]] = Counter()
    for s in steps:
        model = s.get("model")
        if not model:
            continue
        event = "monitor_call" if s.get("kind") == "monitor" else "agent_call"
        out[(event, str(s.get("clause_id")), str(model))] += 1
    return out


def logged_calls(entries: list[dict[str, Any]]) -> tuple[Counter[tuple[str, str, str]], list[str]]:
    """Counts of agent_call / monitor_call lines and the lines missing a structured field."""
    out: Counter[tuple[str, str, str]] = Counter()
    missing: list[str] = []
    for e in entries:
        p = e.get("jsonPayload") or {}
        event = p.get("message")
        if event not in ("agent_call", "monitor_call"):
            continue
        out[(event, str(p.get("clause_id")), str(p.get("model")))] += 1
        absent = [k for k in CALL_FIELDS if k not in p]
        if not re.fullmatch(r"[0-9a-f]{64}", str(p.get("prompt_sha256", ""))):
            absent.append("prompt_sha256 format")
        if absent:
            missing.append(f"{e.get('insertId')} {event} {p.get('clause_id')}: {absent}")
    return out, missing


def service_filter() -> str:
    return f'resource.type="cloud_run_revision" AND resource.labels.service_name="{SERVICE}"'


def read_logs(
    gate: Gate, extra: str, start: datetime, end: datetime, limit: int = 2000
) -> list[dict[str, Any]]:
    flt = f'{service_filter()} AND timestamp>="{start.isoformat()}" AND timestamp<="{end.isoformat()}"' + (
        f" AND {extra}" if extra else ""
    )
    out = gcloud(gate, "logging", "read", flt, "--format=json", f"--limit={limit}", "--order=asc")
    entries = json.loads(out or "[]")
    if len(entries) >= limit:
        raise Blocked(f"log query hit the {limit}-entry limit; narrow the window")
    return list(entries)


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def run_window(run: dict[str, Any]) -> tuple[datetime, datetime]:
    start = parse_ts(run["started_at"])
    end = parse_ts(run["finished_at"]) if run.get("finished_at") else start + timedelta(seconds=RUN_TIMEOUT_S)
    return start - timedelta(seconds=ERROR_WINDOW_PAD_S), end + timedelta(seconds=ERROR_WINDOW_PAD_S)


def settled_run_logs(
    gate: Gate, run_id: str, run: dict[str, Any], steps: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The run's log entries, polled until the call lines cover every model step (ingestion lag)
    or until LOG_SETTLE_S has passed."""
    start, end = run_window(run)
    want = sum(expected_calls(steps).values())
    deadline = time.monotonic() + LOG_SETTLE_S
    while True:
        entries = read_logs(gate, f'jsonPayload.run_id="{run_id}"', start, end)
        got = sum(logged_calls(entries)[0].values())
        if got >= want or time.monotonic() > deadline:
            return entries
        print(f"  waiting for log ingestion: {got}/{want} call lines for {run_id}", flush=True)
        time.sleep(LOG_POLL_S)


# ---- subcommands ---------------------------------------------------------------------------------------


def _start(gate: Gate, need_health: bool = True) -> dict[str, Any]:
    gate.git_sha = git_sha()
    gate.revision = current_revision(gate)
    return health(gate) if need_health else {}


def _corpus_verifier(gate: Gate, health_body: dict[str, Any]) -> QuoteVerifier:
    sys.path.insert(0, str(BACKEND))
    from argus_api.corpus.store import Corpus  # noqa: PLC0415  (backend import only where needed)

    corpus = Corpus.load()
    gate.check(
        "local_corpus_matches_deployed",
        corpus.corpus_hash == health_body.get("corpus_hash"),
        corpus.corpus_hash,
        health_body.get("corpus_hash"),
    )

    def verify(ref: str, para: str, quote: str) -> bool:
        q = corpus.verify_quote(ref, para, quote)
        return q.paragraph_found and q.matches

    return verify


def _check_deploy(gate: Gate, h: dict[str, Any]) -> None:
    gate.check(
        "health_mode_cloud_vertex",
        (h.get("mode"), h.get("provider")) == ("cloud", "vertex"),
        [h.get("mode"), h.get("provider")],
        ["cloud", "vertex"],
    )
    gate.check("deployed_sha_is_head", h.get("git_sha") == gate.git_sha, h.get("git_sha"), gate.git_sha)


def cmd_smoke(args: argparse.Namespace, gate: Gate) -> None:
    h = _start(gate)
    _check_deploy(gate, h)
    verify = _corpus_verifier(gate, h)
    state = State(args.state)
    key = web_api_key()
    with live_lock(args.state):
        user = mint_user(gate, key)
        gate.details["uid"] = user.uid
        run_id, r = post_run(gate, state, args.budget, user, "policy_a")
    gate.details["run_id"] = run_id
    gate.check("post_200", r.status == 200, r.status, 200)
    docs = read_run(Firestore(gate, user.id_token), run_id)
    gate.data[run_id] = docs.__dict__
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    errors = {cid: d.get("error") for cid, d in docs.findings.items() if d.get("error")}
    gate.check(
        "zero_failed_clauses",
        not errors and (docs.run or {}).get("status") == "done",
        {"status": (docs.run or {}).get("status"), "errors": errors},
        "status done, no clause errors",
    )
    entries: list[dict[str, Any]] = []
    if docs.run:
        entries = settled_run_logs(gate, run_id, docs.run, docs.steps)
        start, end = run_window(docs.run)
        entries += read_logs(gate, f'"{MODEL_TURN}"', start, end)
    hits = model_turn_hits(docs, entries)
    gate.check("no_model_turn_error", not hits, hits, [])
    quotes = [
        c
        for c in hero_criteria("smoke", "policy_a", docs, expected, verify)
        if c.name.endswith("quotes_verbatim")
    ]
    gate.criteria += quotes
    gate.details["label_agreement"] = label_agreement("policy_a", docs.findings, expected)
    gate.details["run"] = {
        k: (docs.run or {}).get(k)
        for k in (
            "status",
            "error",
            "summary",
            "latency_ms",
            "cost_usd_estimate",
            "agent_model",
            "monitor_model",
            "provider",
        )
    }


def cmd_hero(args: argparse.Namespace, gate: Gate) -> None:
    h = _start(gate)
    _check_deploy(gate, h)
    verify = _corpus_verifier(gate, h)
    state = State(args.state)
    key = web_api_key()
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    runs: list[dict[str, Any]] = []
    gate.details["runs"] = runs
    with live_lock(args.state):
        user = mint_user(gate, key)
        gate.details["uid"] = user.uid
        for i, policy_id in enumerate(args.sequence, start=1):
            tag = f"run{i}_{policy_id}"
            print(f"hero {tag}", flush=True)
            if i > 1:
                user = refresh_user(gate, key, user.uid, user.refresh_token)  # ID tokens last an hour
            run_id, r = post_run(gate, state, args.budget, user, policy_id)
            row: dict[str, Any] = {
                "tag": tag,
                "policy_id": policy_id,
                "run_id": run_id,
                "http_status": r.status,
            }
            runs.append(row)
            gate.check(f"{tag}.post_200", r.status == 200, r.status, 200)
            docs = read_run(Firestore(gate, user.id_token), run_id)
            gate.data[run_id] = docs.__dict__
            for crit in hero_criteria(tag, policy_id, docs, expected, verify):
                gate.check(crit.name, crit.ok, crit.observed, crit.expected)
            entries: list[dict[str, Any]] = []
            if docs.run:
                start, end = run_window(docs.run)
                entries = read_logs(gate, f'"{MODEL_TURN}"', start, end)
            hits = model_turn_hits(docs, entries)
            gate.check(f"{tag}.no_model_turn_error", not hits, hits, [])
            row["label_agreement"] = label_agreement(policy_id, docs.findings, expected)
            row["summary"] = (docs.run or {}).get("summary")
            row["latency_ms"] = (docs.run or {}).get("latency_ms")
            row["cost_usd_estimate"] = (docs.run or {}).get("cost_usd_estimate")
            print(
                f"  label agreement {row['label_agreement']['agree']}/{row['label_agreement']['n']}",
                flush=True,
            )
            if r.status != 200 or hits:
                gate.details["aborted_after"] = tag
                print(f"  aborting the hero sequence after {tag} (spend safety)", flush=True)
                break


def cmd_rules(args: argparse.Namespace, gate: Gate) -> None:
    _start(gate, need_health=False)
    state = State(args.state)
    key = web_api_key()
    run_id = args.run_id or state.newest_owned_run()
    owner: User | None = None
    if run_id and run_id in state.data["owners"]:
        o = state.data["owners"][run_id]
        owner = refresh_user(gate, key, o["uid"], o["refresh_token"])
    if run_id is None:
        # No run of ours: any existing run still serves the cross-user and no-token denials.
        status, docs = Firestore(gate, admin_token(gate)).list("runs", page_size=1)
        run_id = docs[0][0] if status == 200 and docs else None
    if run_id is None:
        raise Blocked("no run exists to test against; run smoke first")
    gate.details["run_id"] = run_id
    gate.details["owner_available"] = owner is not None
    other = mint_user(gate, key)
    gate.details["other_uid"] = other.uid
    anon = Firestore(gate, None)
    oth = Firestore(gate, other.id_token)
    probe = f"rules-probe-{uuid.uuid4().hex[:12]}"

    def expect(name: str, status: int, want: int) -> None:
        gate.check(name, status == want, status, want)

    if owner is not None:
        own = Firestore(gate, owner.id_token)
        expect("owner_read_run", own.get(f"runs/{run_id}")[0], 200)
        for child in ("steps", "findings", "audit"):
            s, docs = own.list(f"runs/{run_id}/{child}")
            gate.check(
                f"owner_read_{child}",
                s == 200 and len(docs) > 0,
                {"status": s, "docs": len(docs)},
                "200 with docs",
            )
        expect("owner_write_own_run_child", own.create(f"runs/{run_id}/steps", probe, {"probe": True}), 403)
    else:
        gate.check(
            "owner_read_run",
            False,
            "not run: no owner token for this run in the state file",
            "200 (run smoke or hero first, or pass --run-id of a run they made)",
        )

    expect("other_user_read_run", oth.get(f"runs/{run_id}")[0], 403)
    for child in ("steps", "findings", "audit"):
        expect(f"other_user_read_{child}", oth.list(f"runs/{run_id}/{child}")[0], 403)
    expect("other_user_list_all_runs", oth.list("runs", page_size=1)[0], 403)
    expect("no_token_read_run", anon.get(f"runs/{run_id}")[0], 403)
    for child in ("steps", "findings", "audit"):
        expect(f"no_token_read_{child}", anon.list(f"runs/{run_id}/{child}")[0], 403)
    expect("no_token_read_config_limits", anon.get("config/limits")[0], 403)
    expect("other_user_read_config_limits", oth.get("config/limits")[0], 403)
    expect("other_user_read_usage", oth.list("usage", page_size=1)[0], 403)
    expect("other_user_read_smoke", oth.list("smoke", page_size=1)[0], 403)

    # Client writes: always to a fresh document id, so a broken rule could never overwrite real data.
    for coll in ("runs", "samples", "config", "eval_results"):
        expect(f"client_write_{coll}", oth.create(coll, probe, {"owner_uid": other.uid, "probe": True}), 403)
        expect(f"no_token_write_{coll}", anon.create(coll, probe, {"probe": True}), 403)
    expect("client_write_other_run_child", oth.create(f"runs/{run_id}/findings", probe, {"probe": True}), 403)

    expect("public_read_samples", anon.list("samples", page_size=1)[0], 200)
    expect("public_read_eval_results", anon.list("eval_results", page_size=1)[0], 200)
    gate.details["probe_doc_id"] = probe


def _run_ids_for_logs(args: argparse.Namespace, state: State) -> list[str]:
    if args.run_id:
        return list(args.run_id)
    ids = state.runs_of_last("hero") or state.runs_of_last("smoke")
    if not ids:
        raise Blocked("no run ids given and none from smoke or hero in the state file")
    return ids


def cmd_logs(args: argparse.Namespace, gate: Gate) -> None:
    _start(gate, need_health=False)
    state = State(args.state)
    fs = Firestore(gate, admin_token(gate))  # read-only
    run_ids = _run_ids_for_logs(args, state)
    windows: list[tuple[datetime, datetime]] = []
    per_run: dict[str, Any] = {}
    gate.details["runs"] = per_run
    for run_id in run_ids:
        docs = read_run(fs, run_id)
        if docs.run is None:
            gate.check(f"{run_id}.run_exists", False, docs.statuses["run"], 200)
            continue
        entries = settled_run_logs(gate, run_id, docs.run, docs.steps)
        want = expected_calls(docs.steps)
        got, missing = logged_calls(entries)
        windows.append(run_window(docs.run))
        diff = {
            f"{k[0]}|{k[1]}|{k[2]}": {"steps": want.get(k, 0), "logs": got.get(k, 0)}
            for k in set(want) | set(got)
            if want.get(k, 0) != got.get(k, 0)
        }
        per_run[run_id] = {
            "agent_calls_steps": sum(v for k, v in want.items() if k[0] == "agent_call"),
            "agent_calls_logged": sum(v for k, v in got.items() if k[0] == "agent_call"),
            "monitor_calls_steps": sum(v for k, v in want.items() if k[0] == "monitor_call"),
            "monitor_calls_logged": sum(v for k, v in got.items() if k[0] == "monitor_call"),
            "entries": len(entries),
        }
        gate.data[run_id] = {"steps": docs.steps, "log_entries": entries}
        gate.check(f"{run_id}.model_steps_present", sum(want.values()) > 0, sum(want.values()), ">0")
        gate.check(f"{run_id}.every_call_logged", not diff, diff, "per (event, clause, model): logs == steps")
        gate.check(f"{run_id}.call_lines_structured", not missing, missing[:20], [])
    if args.since:
        windows.append((parse_ts(args.since), parse_ts(args.until) if args.until else datetime.now(UTC)))
    if not windows:
        raise Blocked("no window to count errors in")
    errors: list[dict[str, Any]] = []
    for start, end in _merge(windows):
        errors += read_logs(gate, "severity>=ERROR", start, end)
    gate.details["error_windows"] = [[s.isoformat(), e.isoformat()] for s, e in _merge(windows)]
    gate.data["error_entries"] = errors
    gate.check(
        "zero_error_entries_in_window",
        len(errors) == 0,
        {
            "count": len(errors),
            "first": [_short(e.get("jsonPayload") or e.get("textPayload"), 300) for e in errors[:5]],
        },
        0,
    )


def _merge(windows: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for s, e in sorted(windows):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def read_limits(fs: Firestore) -> dict[str, Any]:
    status, doc = fs.get("config/limits")
    if status != 200 or doc is None:
        raise Blocked(f"config/limits read answered {status}")
    if set(doc) != set(LIMIT_FIELDS):
        raise Blocked(f"config/limits has unexpected fields {sorted(doc)}")
    return doc


def cmd_faults(args: argparse.Namespace, gate: Gate) -> None:
    _start(gate)
    key = web_api_key()
    admin = Firestore(gate, admin_token(gate))
    original = read_limits(admin)
    gate.details["limits_before"] = original
    restored: dict[str, Any] | None = None
    t0 = datetime.now(UTC)
    with live_lock(args.state):
        try:
            user = mint_user(gate, key)
            gate.details["uid"] = user.uid
            day = datetime.now(IST).date().isoformat()
            status, usage = admin.get(f"usage/{day}/users/{user.uid}")
            count = int((usage or {}).get("runs", 0)) if status in (200, 404) else None
            if count is None:
                raise Blocked(f"usage read answered {status}")
            gate.details["user_count_today"] = count

            # 1. Per-user cap at the user's own count: refused before any model call.
            gate.check(
                "set_user_cap",
                admin.patch("config/limits", {"per_user_daily_run_cap": count}) == 200,
                None,
                200,
            )
            gate.check(
                "user_cap_read_back", read_limits(admin)["per_user_daily_run_cap"] == count, None, count
            )
            capped_id = uuid.uuid4().hex
            r = http(
                gate,
                "POST",
                f"{API}/api/runs",
                token=user.id_token,
                body={"run_id": capped_id, "policy_id": "policy_a"},
                timeout=60,
            )
            gate.check("cap_post_429", r.status == 429, {"status": r.status, "body": r.text[:300]}, 429)
            gate.check("cap_no_run_doc", admin.get(f"runs/{capped_id}")[0] == 404, None, 404)
            admin.patch("config/limits", {"per_user_daily_run_cap": original["per_user_daily_run_cap"]})

            # 2. Kill switch: 503 before anything is written.
            gate.check("set_kill_switch", admin.patch("config/limits", {"enabled": False}) == 200, None, 200)
            gate.check("kill_switch_read_back", read_limits(admin)["enabled"] is False, None, False)
            killed_id = uuid.uuid4().hex
            r = http(
                gate,
                "POST",
                f"{API}/api/runs",
                token=user.id_token,
                body={"run_id": killed_id, "policy_id": "policy_a"},
                timeout=60,
            )
            gate.check("kill_post_503", r.status == 503, {"status": r.status, "body": r.text[:300]}, 503)
            gate.check("kill_no_run_doc", admin.get(f"runs/{killed_id}")[0] == 404, None, 404)
            gate.details["rejected_run_ids"] = [capped_id, killed_id]
        finally:
            for attempt in range(3):
                code = admin.patch("config/limits", dict(original))
                try:
                    restored = read_limits(admin)
                except Blocked:
                    restored = None
                if code == 200 and restored == original:
                    break
                time.sleep(2 * (attempt + 1))
            gate.details["limits_after"] = restored
            gate.check("limits_restored", restored == original, restored, original)
            if restored != original:
                print("!! config/limits NOT restored; set it by hand: make limits ...", file=sys.stderr)
    # 3. No model call for the rejected runs (checked after a pause for log ingestion).
    time.sleep(args.log_wait)
    end = datetime.now(UTC)
    for rid in gate.details.get("rejected_run_ids", []):
        entries = read_logs(gate, f'jsonPayload.run_id="{rid}"', t0 - timedelta(minutes=1), end)
        calls = [
            e
            for e in entries
            if (e.get("jsonPayload") or {}).get("message")
            in ("agent_call", "monitor_call", "llm_attempt_failed")
        ]
        gate.check(f"no_model_log_for_{rid}", not calls, len(calls), 0)


COMMANDS: dict[str, Callable[[argparse.Namespace, Gate], None]] = {
    "smoke": cmd_smoke,
    "hero": cmd_hero,
    "rules": cmd_rules,
    "logs": cmd_logs,
    "faults": cmd_faults,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help=f"evidence directory (default {DEFAULT_OUT})"
    )
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE, help="owners + spend ledger (mode 600)")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="max live POSTs in the ledger")
    ap.add_argument("--gate-id", help="evidence file stem (default: the subcommand)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("smoke", help="1 live run of policy_a")
    hero = sub.add_parser("hero", help="policy_a, policy_a, policy_b, policy_b, serially")
    hero.add_argument("--sequence", nargs="+", choices=("policy_a", "policy_b"), default=list(HERO_SEQUENCE))
    rules = sub.add_parser("rules", help="security rules with real tokens (no model calls)")
    rules.add_argument("--run-id")
    logs = sub.add_parser("logs", help="model-call log lines and ERROR count")
    logs.add_argument("--run-id", action="append")
    logs.add_argument("--since", help="extra ERROR window start (ISO, UTC)")
    logs.add_argument("--until", help="extra ERROR window end (ISO, UTC; default now)")
    faults = sub.add_parser("faults", help="per-user cap 429 and kill switch 503, restored in finally")
    faults.add_argument("--log-wait", type=float, default=30.0, help="seconds to wait before the log check")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    gate = Gate(args.gate_id or args.command, args.out)
    print(f"{gate.name}: started {gate.started_utc}", flush=True)
    try:
        COMMANDS[args.command](args, gate)
    except Blocked as e:
        return gate.finish(blocked=str(e))
    except KeyboardInterrupt:
        return gate.finish(blocked="interrupted")
    return gate.finish()


if __name__ == "__main__":
    raise SystemExit(main())
