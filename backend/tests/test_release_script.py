"""scripts/release.sh web and promote: every web release is checked by its build stamp, and a failed check
prints the Hosting rollback and stops.

Each test copies release.sh into a throwaway git repo and puts fake gcloud, curl, firebase, npm and backend
python on PATH, so nothing here touches Google Cloud, Firebase or the network. The fakes log their arguments.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PUBLIC_API = "https://argus-api-419264532549.asia-south1.run.app"
PREV_VERSION = "1a45091fc4332f29"
ROLLBACK = f"firebase hosting:clone argus-aibc@{PREV_VERSION} argus-aibc:live --project argus-aibc"

FAKE_CURL = f"""#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
case "$*" in
  *firebasehosting.googleapis.com*)
    echo '{{"release": {{"version": {{"name": "sites/argus-aibc/versions/{PREV_VERSION}"}}}}}}' ;;
  *"/health"*) echo "{{\\"status\\": \\"ok\\", \\"git_sha\\": \\"$FAKE_API_SHA\\"}}" ;;
  *firestore.googleapis.com*config/custom_limits*)
    # FAKE_CUSTOM_LIMITS is the REST document; unset means it is missing (HTTP 404, so curl -f exits 22).
    [[ -n "${{FAKE_CUSTOM_LIMITS:-}}" ]] || exit 22
    echo "$FAKE_CUSTOM_LIMITS" ;;
  *) exit 22 ;;
esac
"""
FAKE_LOGGER = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$FAKE_LOG"
"""
FAKE_GCLOUD = FAKE_LOGGER + 'if [[ "$*" == "auth print-access-token" ]]; then echo fake-token; fi\n'
# npm run build, run in web/: writes the stamp the real build plugin writes (web/src/build/versionStamp.ts).
FAKE_NPM = """#!/usr/bin/env bash
echo "npm $*" >> "$FAKE_LOG"
mkdir -p dist
sha="${FAKE_STAMP_SHA:-$(git rev-parse HEAD)}"
printf '{"sha": "%s", "dirty": false, "builtAt": "2026-10-18T03:00:00Z", ' "$sha" > dist/version.json
printf '"customClauseOpen": %s, ' "${FAKE_CUSTOM_OPEN:-false}" >> dist/version.json
printf '"apiBase": "%s"}\\n' "$FAKE_API_BASE" >> dist/version.json
"""
# backend/.venv/bin/python: check.py exits FAKE_CHECK_EXIT, cloud_e2e.py exits 0.
FAKE_PY = """#!/usr/bin/env bash
echo "python $*" >> "$FAKE_LOG"
if [[ "$1" == "scripts/check.py" ]]; then exit "${FAKE_CHECK_EXIT:-0}"; fi
exit 0
"""


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Tester",
        GIT_AUTHOR_EMAIL="tester@example.com",
        GIT_COMMITTER_NAME="Tester",
        GIT_COMMITTER_EMAIL="tester@example.com",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    ).stdout


def _exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def release_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A repo with the real release.sh, an "api" commit (what the API serves) and a later web-only HEAD."""
    repo = tmp_path / "argus"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "release.sh", repo / "scripts" / "release.sh")
    (repo / "backend" / "argus_api").mkdir(parents=True)
    (repo / "backend" / "argus_api" / "main.py").write_text("v1\n", encoding="utf-8")
    (repo / "web").mkdir()
    (repo / "web" / "App.tsx").write_text("w0\n", encoding="utf-8")
    (repo / ".gitignore").write_text("web/dist/\nbackend/.venv/\n", encoding="utf-8")
    _exe(repo / "backend" / ".venv" / "bin" / "python", FAKE_PY)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "api")
    api = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "web" / "App.tsx").write_text("w1\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "web only")
    bin_dir = tmp_path / "bin"
    _exe(bin_dir / "curl", FAKE_CURL)
    _exe(bin_dir / "gcloud", FAKE_GCLOUD)
    _exe(bin_dir / "firebase", FAKE_LOGGER)
    _exe(bin_dir / "npm", FAKE_NPM)
    return repo, {"api": api, "head": _git(repo, "rev-parse", "HEAD").strip()}


def run_release(repo: Path, *args: str, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    log = repo.parent / "fake.log"
    log.write_text("", encoding="utf-8")
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    full_env.update(
        PATH=f"{repo.parent / 'bin'}{os.pathsep}{full_env['PATH']}",
        FAKE_LOG=str(log),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    full_env.update({"FAKE_API_BASE": PUBLIC_API, **env})
    proc = subprocess.run(
        ["bash", "scripts/release.sh", *args],
        cwd=repo,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc, log.read_text(encoding="utf-8").splitlines()


def _index(calls: list[str], prefix: str) -> int:
    return next(i for i, c in enumerate(calls) if c.startswith(prefix))


def test_web_release_builds_deploys_then_checks_the_stamp(release_repo: tuple[Path, dict[str, str]]) -> None:
    repo, shas = release_repo
    proc, calls = run_release(repo, "web", FAKE_API_SHA=shas["api"])
    assert proc.returncode == 0, proc.stderr
    deploy = _index(calls, "firebase deploy --only hosting,firestore:rules --project argus-aibc")
    check = _index(calls, "python scripts/check.py deploy --strict --expect-web-sha HEAD --evidence-dir")
    rules = _index(calls, "python scripts/cloud_e2e.py")
    assert _index(calls, "npm run build") < deploy < check < rules
    assert "--gate-id WEB-RULES rules" in calls[rules]
    assert ROLLBACK in proc.stdout
    state = (repo.parent / "verification").rglob("web_state.env")
    assert f"PREV_HOSTING_VERSION={PREV_VERSION}" in next(state).read_text(encoding="utf-8")


def test_web_release_failed_check_prints_the_rollback(release_repo: tuple[Path, dict[str, str]]) -> None:
    repo, shas = release_repo
    proc, calls = run_release(repo, "web", FAKE_API_SHA=shas["api"], FAKE_CHECK_EXIT="1")
    assert proc.returncode != 0
    assert ROLLBACK in proc.stderr
    assert not any("cloud_e2e.py" in c for c in calls), "stops at the failed check"


@pytest.mark.parametrize(
    ("setup", "env", "why"),
    [
        ("dirty", {}, "working tree is not clean"),
        ("api-change", {}, "changes the API code"),
        ("none", {"FAKE_STAMP_SHA": "f" * 40}, "does not describe a clean build of HEAD"),
        ("none", {"FAKE_API_BASE": "http://localhost:8080"}, "does not describe a clean build of HEAD"),
    ],
    ids=["dirty-tree", "api-code-changed", "stamp-not-head", "stamp-local-api"],
)
def test_web_release_stops_before_deploying(
    release_repo: tuple[Path, dict[str, str]], setup: str, env: dict[str, str], why: str
) -> None:
    repo, shas = release_repo
    if setup == "dirty":
        (repo / "web" / "App.tsx").write_text("uncommitted\n", encoding="utf-8")
    if setup == "api-change":
        (repo / "backend" / "argus_api" / "main.py").write_text("v2\n", encoding="utf-8")
        _git(repo, "commit", "-q", "-am", "api change")
    proc, calls = run_release(repo, "web", **{"FAKE_API_SHA": shas["api"], **env})
    assert proc.returncode != 0
    assert why in proc.stderr, proc.stderr
    assert not any(c.startswith("firebase deploy") for c in calls), "nothing reaches Hosting"


def _custom_limits(enabled: bool) -> str:
    """config/custom_limits as the Firestore REST API returns it."""
    return json.dumps(
        {
            "name": "projects/argus-aibc/databases/(default)/documents/config/custom_limits",
            "fields": {
                "custom_enabled": {"booleanValue": enabled},
                "custom_daily_cap": {"integerValue": "20"},
            },
        }
    )


@pytest.mark.parametrize(
    ("env", "why"),
    [
        ({}, "config/custom_limits is missing or could not be read"),
        ({"FAKE_CUSTOM_LIMITS": _custom_limits(False)}, "custom_enabled is False"),
        ({"FAKE_CUSTOM_LIMITS": '{"name": "x", "fields": {}}'}, "custom_enabled is None"),
    ],
    ids=["switch-missing", "switch-off", "switch-unset"],
)
def test_a_build_offering_pasting_stops_before_hosting_while_the_switch_is_off(
    release_repo: tuple[Path, dict[str, str]], env: dict[str, str], why: str
) -> None:
    """18 Oct: a build offering "Try your own clause" went live while config/custom_limits was missing, and
    every pasted clause failed. The release now stops before `firebase deploy`, not after it."""
    repo, shas = release_repo
    proc, calls = run_release(repo, "web", FAKE_API_SHA=shas["api"], FAKE_CUSTOM_OPEN="true", **env)
    assert proc.returncode != 0
    assert why in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    assert any("firestore.googleapis.com" in c and "config/custom_limits" in c for c in calls)
    assert not any(c.startswith("firebase") for c in calls), "nothing reaches Hosting"


def test_a_build_offering_pasting_deploys_when_the_switch_is_on(
    release_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, shas = release_repo
    proc, calls = run_release(
        repo,
        "web",
        FAKE_API_SHA=shas["api"],
        FAKE_CUSTOM_OPEN="true",
        FAKE_CUSTOM_LIMITS=_custom_limits(True),
    )
    assert proc.returncode == 0, proc.stderr
    read = next(i for i, c in enumerate(calls) if "documents/config/custom_limits" in c)
    assert _index(calls, "npm run build") < read < _index(calls, "firebase deploy")


def test_a_build_with_pasting_closed_does_not_need_the_switch(
    release_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, shas = release_repo
    proc, calls = run_release(repo, "web", FAKE_API_SHA=shas["api"])
    assert proc.returncode == 0, proc.stderr
    assert not any("firestore.googleapis.com" in c for c in calls)


def test_a_stamp_with_a_non_boolean_customclauseopen_stops_before_hosting(
    release_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, shas = release_repo
    proc, calls = run_release(repo, "web", FAKE_API_SHA=shas["api"], FAKE_CUSTOM_OPEN='"true"')
    assert proc.returncode != 0
    assert "customClauseOpen is 'true', not true or false" in proc.stdout, proc.stdout
    assert not any(c.startswith("firebase") for c in calls)


def test_eval_only_changes_do_not_block_a_web_release(release_repo: tuple[Path, dict[str, str]]) -> None:
    repo, shas = release_repo
    (repo / "backend" / "argus_api" / "eval").mkdir()
    (repo / "backend" / "argus_api" / "eval" / "cases.py").write_text("cases\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "eval only")
    proc, _ = run_release(repo, "web", FAKE_API_SHA=shas["api"])
    assert proc.returncode == 0, proc.stderr


def test_promote_releases_the_web_through_the_same_stamp_check(
    release_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, shas = release_repo
    release_dir = (
        repo.parent / "verification" / dt.date.today().isoformat() / "release"
    )  # as `date +%Y-%m-%d`
    release_dir.mkdir(parents=True)
    (release_dir / "release_state.env").write_text(
        f"SHA={shas['head']}\nOLD=argus-api-00008-nev\nNEW=argus-api-00009-new\nCANDIDATE_URL=https://c\n",
        encoding="utf-8",
    )
    proc, calls = run_release(repo, "promote", FAKE_API_SHA=shas["head"])
    assert proc.returncode == 0, proc.stderr
    check = calls[_index(calls, "python scripts/check.py deploy")]
    assert "--expect-web-sha HEAD" in check and f"--expect-sha {shas['head']}" in check
    assert (
        _index(calls, "gcloud run services update-traffic")
        < _index(calls, "firebase deploy")
        < calls.index(check)
    )


def test_usage_names_the_web_subcommand(release_repo: tuple[Path, dict[str, str]]) -> None:
    repo, _ = release_repo
    proc, _ = run_release(repo)
    assert proc.returncode != 0 and "candidate|promote|web" in proc.stderr


def test_release_script_parses() -> None:
    assert subprocess.run(["bash", "-n", str(REPO / "scripts" / "release.sh")], check=False).returncode == 0


def _predeploy(tmp_path: Path, env: dict[str, str], stamp: bool) -> subprocess.CompletedProcess[str]:
    (tmp_path / "web" / "dist").mkdir(parents=True)
    if stamp:
        (tmp_path / "web" / "dist" / "version.json").write_text("{}")
    clean = {k: v for k, v in os.environ.items() if k != "ARGUS_RELEASE"}
    return subprocess.run(
        ["bash", str(REPO / "scripts" / "web-predeploy.sh")],
        cwd=tmp_path,
        env={**clean, **env},
        capture_output=True,
        text=True,
    )


def test_a_hand_run_hosting_deploy_is_refused(tmp_path: Path) -> None:
    # On 18 Oct a hand-run `firebase deploy` put a stale build live; firebase.json's predeploy now stops that.
    r = _predeploy(tmp_path, {}, stamp=True)
    assert r.returncode == 1 and "scripts/release.sh web" in r.stderr
    assert json.loads((REPO / "firebase.json").read_text())["hosting"]["predeploy"] == [
        "bash scripts/web-predeploy.sh"
    ]


def test_release_sh_web_deploys_pass_the_predeploy_only_with_a_stamp(tmp_path: Path) -> None:
    assert _predeploy(tmp_path, {"ARGUS_RELEASE": "web"}, stamp=True).returncode == 0
    assert _predeploy(tmp_path / "nostamp", {"ARGUS_RELEASE": "web"}, stamp=False).returncode == 1
    assert "ARGUS_RELEASE=web firebase deploy" in (REPO / "scripts" / "release.sh").read_text()
