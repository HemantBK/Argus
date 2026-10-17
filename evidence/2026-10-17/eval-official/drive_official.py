"""Run the pre-registered matrix (PREREG.md, Amendment 2) from the frozen worktree at 7c3072d on Vertex AI.

Each stage starts only when production /health shows agent_breaker "closed" or "half_open" (cool-down over), a burst of 8 parallel calls to
gemini-3.8-flash returns no 429/5xx/499, and no pause file exists (a deploy check or a video take is using
Vertex). A stage whose records are more than 10% capacity failures is re-run once at --workers 1. Hard stop at
07:00 IST. No --publish. Everything is logged to logs/driver.log."""

import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EV = Path(__file__).parent
WT = Path("<workspace>/argus-eval-wt/backend")
PY = "<workspace>/argus/backend/.venv/bin/python"
CORPUS = "<workspace>/argus/backend/data/corpus"
HEALTH = "https://argus-api-419264532549.asia-south1.run.app/health"
PAUSE = Path("<workspace>/scratch/EVAL_PAUSE")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
HARD_STOP = dt.datetime(2026, 10, 18, 7, 0, tzinfo=IST)
HARD = "argus_api/eval/cases/attacks_hard.json"
CAPACITY = ("429", "RESOURCE_EXHAUSTED", "504", "DEADLINE_EXCEEDED", "499", "CANCELLED", "503", "timed out", "busy")
STAGES = [  # (name, agent model override or None, args), in the pre-registered order
    ("R2", None, ["--cases", HARD, "--conditions", "plain_gemini", "agent_no_monitor", "full_argus"]),
    ("R1", None, ["--conditions", "plain_gemini", "agent_no_monitor", "full_argus"]),
    ("R3", "gemini-3.1-flash-lite", ["--conditions", "agent_no_monitor", "full_argus"]),
    ("R4", "gemini-3.1-flash-lite", ["--cases", HARD, "--conditions", "agent_no_monitor", "full_argus"]),
    ("R5", None, ["--conditions", "monitor_only"]),
]


def log(msg: str) -> None:
    line = f"{dt.datetime.now(IST):%H:%M:%S} {msg}"
    print(line, flush=True)
    with open(EV / "logs" / "driver.log", "a") as f:
        f.write(line + "\n")


def breaker() -> str:
    try:
        return json.load(urllib.request.urlopen(HEALTH, timeout=20)).get("agent_breaker", "?")
    except Exception as e:  # noqa: BLE001
        return f"health error {type(e).__name__}"


def burst() -> list[str]:
    from google import genai
    from google.genai import types

    c = genai.Client(vertexai=True, project="argus-aibc", location="global",
                     http_options=types.HttpOptions(timeout=30000))

    def one(_: int) -> str:
        try:
            c.models.generate_content(model="gemini-3.8-flash", contents="Reply with the single word ok.",
                                      config=types.GenerateContentConfig(
                                          thinking_config=types.ThinkingConfig(thinking_level="low")))
            return "ok"
        except Exception as e:  # noqa: BLE001
            return str(e)[:3]

    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(one, range(8)))


def wait_for_calm() -> bool:
    while dt.datetime.now(IST) < HARD_STOP:
        if PAUSE.exists():
            log("paused (pause file present)")
            time.sleep(120)
            continue
        b = breaker()
        # "half_open" only means the cool-down is over and the next clause is the trial; with no traffic it
        # stays there, so it counts as ready. The burst below is the real test of the pool.
        if b not in ("closed", "half_open"):
            log(f"waiting: breaker {b}")
            time.sleep(300)
            continue
        r = burst()
        if all(x == "ok" for x in r):
            log("calm: breaker closed, burst 8/8 ok")
            return True
        log(f"waiting: burst {r}")
        time.sleep(300)
    return False


def capacity_share(run_dir: Path) -> tuple[int, int]:
    recs = json.load(open(run_dir / "results.json")).get("records", [])
    bad = sum(1 for r in recs if r.get("error") and any(k in str(r["error"]) for k in CAPACITY))
    return bad, len(recs)


def run_stage(name: str, model: str | None, args: list[str], workers: int, tag: str) -> Path | None:
    before = set(p.name for p in (EV / "results").iterdir())
    env = {**os.environ, "ARGUS_LLM_PROVIDER": "vertex", "GOOGLE_CLOUD_PROJECT": "argus-aibc",
           "GOOGLE_CLOUD_LOCATION": "global"}
    env.pop("ARGUS_AGENT_MODEL", None)
    env.pop("ARGUS_MONITOR_MODEL", None)
    if model:
        env["ARGUS_AGENT_MODEL"] = model
    cmd = [PY, "-m", "argus_api.eval.run", *args, "--repeat", "2", "--workers", str(workers),
           "--corpus-dir", CORPUS, "--results-dir", str(EV / "results")]
    log(f"{name}{tag} start workers={workers}")
    with open(EV / "logs" / f"{name}{tag}.log", "w") as out:
        rc = subprocess.run(cmd, cwd=WT, env=env, stdout=out, stderr=subprocess.STDOUT).returncode
    new = [p for p in (EV / "results").iterdir() if p.name not in before]
    if rc != 0 or not new:
        log(f"{name}{tag} exit {rc}, no results")
        return None
    d = new[0]
    bad, n = capacity_share(d)
    log(f"{name}{tag} done {d.name}: capacity failures {bad}/{n}")
    (EV / "logs" / "stages.jsonl").open("a").write(json.dumps(
        {"stage": name, "tag": tag, "run": d.name, "workers": workers, "capacity_failures": bad, "records": n}) + "\n")
    return d


def main() -> int:
    log("driver start, frozen worktree " + subprocess.run(["git", "-C", str(WT), "rev-parse", "HEAD"],
                                                          capture_output=True, text=True).stdout.strip())
    for name, model, args in STAGES:
        if not wait_for_calm():
            log("hard stop reached before " + name)
            return 1
        d = run_stage(name, model, args, 2, "")
        if d is None:
            continue
        bad, n = capacity_share(d)
        if n and bad / n > 0.10:
            log(f"{name}: over 10% capacity failures, re-running once at workers 1 (both reported)")
            if wait_for_calm():
                run_stage(name, model, args, 1, "-rerun")
    log("ALL DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
