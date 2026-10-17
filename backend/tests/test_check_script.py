"""scripts/check.py: every commit-stage check passes on a small clean repo and fails on a planted violation.

Each test builds a throwaway git repo in tmp_path, so nothing here touches the real history or the network.
Secret-looking strings are assembled at run time so this file itself never matches the secret patterns.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from argus_api.runs import SAMPLE_PROVIDER

REPO = Path(__file__).resolve().parents[2]
VENV_BIN = Path(sys.executable).parent
DATE = "2026-10-16T12:00:00+05:30"


def _load_check() -> ModuleType:
    spec = importlib.util.spec_from_file_location("argus_check", REPO / "scripts" / "check.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["argus_check"] = module
    spec.loader.exec_module(module)
    return module


check = _load_check()


@pytest.fixture(autouse=True)
def _no_outer_git_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # When pytest itself runs inside a git hook, these would point every git call at the outer repo.
    for name in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX"):
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------------------------------------- tiny repos
def git(repo: Path, *args: str, date: str = DATE) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Tester",
        GIT_AUTHOR_EMAIL="tester@example.com",
        GIT_COMMITTER_NAME="Tester",
        GIT_COMMITTER_EMAIL="tester@example.com",
        GIT_AUTHOR_DATE=date,
        GIT_COMMITTER_DATE=date,
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    proc = subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True)
    return proc.stdout


# The real copy.ts, so the tagline checks run against the tagline the product ships.
COPY_TS = (REPO / "web" / "src" / "start" / "copy.ts").read_text(encoding="utf-8")
TAGLINE = check.read_tagline(COPY_TS)
assert TAGLINE is not None

# Seed scripts in the two shapes the real ones use: a guard called first in main(), and one called on import.
SEED_FAKE_RUN = '''"""Writes one FAKE run."""

import os

from google.cloud import firestore

EMULATOR_ENV = "FIRESTORE_EMULATOR_HOST"
LABEL = "FAKE run"


def require_emulator() -> str:
    host = os.environ.get(EMULATOR_ENV, "").strip()
    if not host:
        raise SystemExit("seed refused: emulator only")
    return host


def main() -> int:
    require_emulator()
    firestore.Client(project="tiny")
    return 0
'''
SEED_DEMO = """import os


def require_emulator() -> str:
    if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        raise SystemExit("seed refused")
    return os.environ["FIRESTORE_EMULATOR_HOST"]


EMULATOR_HOST = require_emulator()

from google.cloud import firestore  # noqa: E402


def main() -> None:
    firestore.Client(project="tiny")
"""

CLEAN_FILES: dict[str, str] = {
    "README.md": "# Tiny\n\nA clean repo for the checker tests.\n",
    "firestore.rules": """rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // Clients never write: allow write is deliberately absent.
    match /runs/{runId} {
      allow read: if request.auth != null && resource.data.owner_uid == request.auth.uid;
    }
    match /samples/{runId} {
      allow read: if true;
    }
    match /eval_results/{docId} {
      allow read: if true;
    }
  }
}
""",
    "backend/pyproject.toml": """[project]
name = "tiny"
version = "0.0.0"

[tool.ruff]
line-length = 110

[tool.mypy]
strict = true
""",
    "backend/requirements.txt": "fastapi==0.143.2\nhttpx==0.28.1\n",
    "backend/requirements-dev.txt": "pytest==9.1.1\nhttpx==0.28.1\n",
    "backend/.env.example": "ARGUS_MODE=local\nOPENROUTER_API_KEY=\nGEMINI_API_KEY=\n",
    "backend/argus_api/__init__.py": "",
    "backend/argus_api/app.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
    "backend/argus_api/seed_fake_run.py": SEED_FAKE_RUN,
    "web/scripts/seed_demo.py": SEED_DEMO,
    "web/src/start/copy.ts": COPY_TS,
    "backend/argus_api/samples/policy_a.json": json.dumps({"synthetic": True, "title": "Sample NBFC A"}),
    "backend/argus_api/eval/cases/clauses.json": json.dumps(
        [
            {
                "case_id": "c-01",
                "clause_text": "Interest is charged at 18% p.a. under para 10 of the 2025 Directions.",
            }
        ]
    ),
    "backend/tests/__init__.py": "",
    "backend/tests/test_config.py": (
        "from argus_api.app import add\n\n\ndef test_cloud_mode_is_vertex_only() -> None:\n"
        "    assert add(1, 1) == 2\n"
    ),
    "backend/tests/test_fixture_words.py": '"""Tests may say FAKE and TODO."""\n\nTEXT = "FAKE TODO"\n',
    "web/package.json": json.dumps(
        {"name": "tiny-web", "dependencies": {"react": "19.3.0"}, "devDependencies": {"vitest": "5.0.3"}}
    ),
    "web/package-lock.json": json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {"": {"dependencies": {"react": "19.3.0"}, "devDependencies": {"vitest": "5.0.3"}}},
        }
    ),
    "web/.env.example": (
        "VITE_USE_EMULATORS=true\nVITE_FIREBASE_PROJECT_ID=tiny-local\n"
        "VITE_FIREBASE_API_KEY=fake-api-key-for-emulator\n"
    ),
    "web/src/App.tsx": "export const title = 'Tiny';\n",
    "web/src/App.test.tsx": "// TODO and FAKE are fine in tests\nexport {};\n",
}


def write(repo: Path, files: dict[str, str | None]) -> None:
    for rel, content in files.items():
        path = repo / rel
        if content is None:
            if path.exists():
                git(repo, "rm", "-q", "--cached", rel)
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        git(repo, "add", "-f", rel)


def make_repo(tmp_path: Path, changes: dict[str, str | None] | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    write(repo, dict(CLEAN_FILES))
    git(repo, "commit", "-q", "-m", "Start the tiny repo")
    if changes:
        write(repo, changes)
    return repo


def ctx_for(repo: Path, **args: Any) -> Any:
    import argparse

    return check.Ctx(repo, venv_bin=VENV_BIN, args=argparse.Namespace(**args))


def run_one(repo: Path, fn: Callable[[Any], Any], **args: Any) -> Any:
    [result] = check.run_checks(ctx_for(repo, **args), [check.Check("T", (), fn)])
    return result


def key(prefix: str, length: int, alphabet: str = "A") -> str:
    return prefix + alphabet * length


# ------------------------------------------------------------------------------------------- clean repo
STATIC_CHECKS = [c for c in check.COMMIT_CHECKS if not c.check_id.startswith("C-WEB")]


@pytest.mark.parametrize("item", STATIC_CHECKS, ids=lambda c: c.check_id)
def test_clean_repo_passes(tmp_path: Path, item: Any) -> None:
    repo = make_repo(tmp_path)
    result = run_one(repo, item.fn)
    assert result.status == check.PASS, (result.reason, result.details)


# ------------------------------------------------------------------------------------------- violations
PEM = "-----" + "BEGIN RSA PRIVATE KEY" + "-----\nMIIE\n"
SERVICE_ACCOUNT = '{"type": "service' + '_account", "project_id": "x"}'
GOOD_RULES = CLEAN_FILES["firestore.rules"]

VIOLATIONS: list[tuple[str, Callable[[Any], Any], dict[str, str | None]]] = [
    ("ruff-unused-import", check.check_py_lint, {"backend/argus_api/bad.py": "import os\n"}),
    ("ruff-format", check.check_py_format, {"backend/argus_api/bad.py": "def f( ) -> int :\n    return 1\n"}),
    (
        "mypy-wrong-return",
        check.check_py_types,
        {"backend/argus_api/bad.py": "def f() -> int:\n    return 'x'\n"},
    ),
    (
        "pytest-failing",
        check.check_py_tests,
        {"backend/tests/test_bad.py": "def test_bad() -> None:\n    assert 0\n"},
    ),
    (
        "pytest-no-tests",
        check.check_py_tests,
        {"backend/tests/test_config.py": None, "backend/tests/test_fixture_words.py": None},
    ),
    ("req-range", check.check_deps_pinned, {"backend/requirements.txt": "fastapi>=0.100\n"}),
    ("req-unpinned", check.check_deps_pinned, {"backend/requirements-dev.txt": "pytest\n"}),
    ("req-conflicting-pins", check.check_deps_pinned, {"backend/requirements-dev.txt": "httpx==0.27.0\n"}),
    (
        "npm-caret",
        check.check_deps_pinned,
        {
            "web/package.json": json.dumps(
                {"name": "t", "dependencies": {"react": "^19.3.0"}, "devDependencies": {"vitest": "5.0.3"}}
            )
        },
    ),
    (
        "npm-tilde-dev",
        check.check_deps_pinned,
        {
            "web/package.json": json.dumps(
                {"name": "t", "dependencies": {"react": "19.3.0"}, "devDependencies": {"vitest": "~5.0.3"}}
            )
        },
    ),
    ("npm-lock-untracked", check.check_deps_pinned, {"web/package-lock.json": None}),
    (
        "npm-lock-out-of-sync",
        check.check_deps_pinned,
        {
            "web/package-lock.json": json.dumps(
                {"lockfileVersion": 3, "packages": {"": {"dependencies": {"react": "19.2.0"}}}}
            )
        },
    ),
    ("env-file", check.check_forbidden_files, {"backend/.env": "ARGUS_MODE=local\n"}),
    ("env-local", check.check_forbidden_files, {"web/.env.local": "VITE_X=1\n"}),
    ("mypy-cache", check.check_forbidden_files, {"backend/.mypy_cache/x.json": "{}"}),
    ("pytest-cache", check.check_forbidden_files, {".pytest_cache/v/x": "1"}),
    ("ruff-cache", check.check_forbidden_files, {"backend/.ruff_cache/x": "1"}),
    ("node-modules", check.check_forbidden_files, {"web/node_modules/x/index.js": "1"}),
    ("dist", check.check_forbidden_files, {"web/dist/index.html": "<html></html>"}),
    ("corpus", check.check_forbidden_files, {"backend/data/corpus/paragraphs.json": "[]"}),
    ("eval-results", check.check_forbidden_files, {"backend/argus_api/eval/results/r.json": "{}"}),
    ("pdf", check.check_forbidden_files, {"docs/rbi.PDF": "%PDF-1.4"}),
    ("rules-md", check.check_forbidden_files, {"RULES.md": "# rules"}),
    ("status-md", check.check_forbidden_files, {"docs/STATUS.md": "# status"}),
    ("decisions-md", check.check_forbidden_files, {"DECISIONS.md": "# d"}),
    ("council", check.check_forbidden_files, {"council/run.md": "notes"}),
    ("research", check.check_forbidden_files, {"docs/research/notes.md": "notes"}),
    ("google-key", check.check_secrets_tree, {"web/src/key.ts": f"export const k = '{key('AIza', 35)}';\n"}),
    ("openrouter-key", check.check_secrets_tree, {"backend/k.txt": key("sk-" + "or-v1-", 40, "a")}),
    ("openai-project-key", check.check_secrets_tree, {"backend/k.txt": key("sk-" + "proj-", 40, "b")}),
    ("openai-key", check.check_secrets_tree, {"backend/k.txt": key("sk-", 48, "c")}),
    ("anthropic-key", check.check_secrets_tree, {"backend/k.txt": key("sk-" + "ant-api03-", 40, "d")}),
    ("pem-key", check.check_secrets_tree, {"backend/key.pem": PEM}),
    ("service-account", check.check_secrets_tree, {"backend/sa.json": SERVICE_ACCOUNT}),
    (
        "example-real-key",
        check.check_env_examples,
        {"backend/.env.example": "GEMINI_API_KEY=abc123realvalue\n"},
    ),
    (
        "example-not-emulator",
        check.check_env_examples,
        {"web/.env.example": ("VITE_USE_EMULATORS=false\nVITE_FIREBASE_API_KEY=fake-api-key-for-emulator\n")},
    ),
    ("verify-in-readme", check.check_placeholders, {"README.md": "Accuracy is [VERIFY] percent.\n"}),
    ("todo-in-web", check.check_placeholders, {"web/src/App.tsx": "// TODO wire this up\nexport {};\n"}),
    ("todo-in-backend", check.check_placeholders, {"backend/argus_api/app.py": "# TODO\nX = 1\n"}),
    ("lorem-in-web", check.check_placeholders, {"web/src/Copy.tsx": "export const t = 'Lorem ipsum';\n"}),
    (
        "sample-without-marker",
        check.check_synthetic,
        {"backend/argus_api/samples/policy_a.json": json.dumps({"title": "Sample NBFC A"})},
    ),
    (
        "sample-with-fake",
        check.check_synthetic,
        {"backend/argus_api/samples/policy_a.json": json.dumps({"synthetic": True, "title": "FAKE policy"})},
    ),
    (
        "sample-list-without-object-marker",
        check.check_synthetic,
        {"backend/argus_api/samples/policy_a.json": json.dumps([{"synthetic": True}])},
    ),
    (
        "email-in-eval-case",
        check.check_synthetic,
        {
            "backend/argus_api/eval/cases/clauses.json": json.dumps(
                [{"clause_text": "Write to ravi.k@gmail.com"}]
            )
        },
    ),
    (
        "mobile-in-sample",
        check.check_synthetic,
        {
            "backend/argus_api/samples/policy_a.json": json.dumps(
                {"synthetic": True, "clauses": [{"text": "Grievance officer: +91 98450 12345"}]}
            )
        },
    ),
    (
        "pan-in-eval-case",
        check.check_synthetic,
        {
            "backend/argus_api/eval/cases/attacks.json": json.dumps(
                [{"clause_text": "Borrower PAN ABCPK1234L"}]
            )
        },
    ),
    (
        "aadhaar-in-eval-case",
        check.check_synthetic,
        {
            "backend/argus_api/eval/cases/attacks.json": json.dumps(
                [{"clause_text": "Aadhaar 2345 6789 0123"}]
            )
        },
    ),
    ("fake-in-app-code", check.check_synthetic, {"backend/argus_api/app.py": "LABEL = 'FAKE'\n"}),
    ("boto3-import", check.check_google_only, {"backend/argus_api/store.py": "import boto3\n"}),
    (
        "azure-import",
        check.check_google_only,
        {"backend/argus_api/store.py": "from azure.storage import blob\n"},
    ),
    (
        "aws-package",
        check.check_google_only,
        {"backend/requirements.txt": "fastapi==0.143.2\nboto3==1.40.0\n"},
    ),
    (
        "vertex-test-fails",
        check.check_google_only,
        {
            "backend/tests/test_config.py": (
                "def test_cloud_mode_is_vertex_only() -> None:\n    assert False\n"
            )
        },
    ),
    (
        "vertex-test-missing",
        check.check_google_only,
        {"backend/tests/test_config.py": ("def test_something_else() -> None:\n    assert True\n")},
    ),
    (
        "allow-write",
        check.check_firestore_rules,
        {
            "firestore.rules": GOOD_RULES.replace(
                "allow read: if true;\n    }\n    match /eval_results",
                "allow read, write: if true;\n    }\n    match /eval_results",
            )
        },
    ),
    (
        "allow-create-update",
        check.check_firestore_rules,
        {
            "firestore.rules": GOOD_RULES.replace(
                "allow read: if request.auth", "allow create, update: if request.auth"
            )
        },
    ),
    (
        "allow-delete",
        check.check_firestore_rules,
        {
            "firestore.rules": GOOD_RULES.replace(
                "allow read: if true;\n    }\n  }",
                "allow read: if true;\n      allow delete: if true;\n    }\n  }",
            )
        },
    ),
    (
        "no-samples-rule",
        check.check_firestore_rules,
        {"firestore.rules": GOOD_RULES.replace("match /samples/{runId}", "match /other/{runId}")},
    ),
    ("rules-missing", check.check_firestore_rules, {"firestore.rules": None}),
    ("fake-in-web-src", check.check_synthetic, {"web/src/App.tsx": "export const title = 'FAKE run';\n"}),
    ("fake-in-web-src-markdown", check.check_synthetic, {"web/src/start/NOTES.md": "Shows a FAKE run.\n"}),
    *[
        (
            f"banned-{phrase}",
            check.check_banned_phrases,
            {"web/src/App.tsx": f"export const title = 'Argus uses {phrase} for every review';\n"},
        )
        for phrase in check.BANNED_PHRASES
    ],
    (
        "banned-in-readme",
        check.check_banned_phrases,
        {"README.md": f"# Tiny\n\nArgus is {check.BANNED_PHRASES[-1]}.\n"},
    ),
    ("banned-in-web-readme", check.check_banned_phrases, {"web/README.md": "Search uses vector search.\n"}),
    (
        "banned-in-docs",
        check.check_banned_phrases,
        {"docs/architecture.md": "Files live in Cloud\nStorage.\n"},
    ),
    (
        "banned-retired-empty-citation-sentence",
        check.check_banned_phrases,
        {
            "web/src/review/view.ts": "export const NO_VERIFIED_CITATION =\n"
            '  "No RBI paragraph supports a conclusion for this clause. Argus does not guess.";\n'
        },
    ),
    (
        "banned-absolute-claim-negated",
        check.check_banned_phrases,
        {"web/src/App.tsx": "export const t = 'Each step is checked, so no finding rests on a guess';\n"},
    ),
    (
        "tagline-copied-in-web-src",
        check.check_tagline,
        {"web/src/Hero.tsx": f"export const h = {TAGLINE!r};\n"},
    ),
    (
        "tagline-copied-in-readme-reflowed",
        check.check_tagline,
        {"README.md": "# Tiny\n\n> " + TAGLINE[:120].replace(" ", "\n> ", 3) + "\n"},
    ),
    ("tagline-file-missing", check.check_tagline, {"web/src/start/copy.ts": None}),
    (
        "tagline-not-a-literal",
        check.check_tagline,
        {"web/src/start/copy.ts": "export const TAGLINE = make();\n"},
    ),
    (
        "seed-without-guard",
        check.check_seed_guard,
        {"backend/argus_api/seed_fake_run.py": SEED_FAKE_RUN.replace("raise SystemExit", "print")},
    ),
    (
        "seed-guard-after-client",
        check.check_seed_guard,
        {
            "backend/argus_api/seed_fake_run.py": SEED_FAKE_RUN.replace(
                '    require_emulator()\n    firestore.Client(project="tiny")',
                '    firestore.Client(project="tiny")\n    require_emulator()',
            )
        },
    ),
    (
        "seed-guard-never-called",
        check.check_seed_guard,
        {
            "web/scripts/seed_demo.py": SEED_DEMO.replace(
                "EMULATOR_HOST = require_emulator()", "EMULATOR_HOST = ''"
            )
        },
    ),
    (
        "seed-client-at-import-before-guard",
        check.check_seed_guard,
        {"web/scripts/seed_more.py": "from google.cloud import firestore\n\nDB = firestore.Client()\n"},
    ),
]


@pytest.mark.parametrize(("name", "fn", "changes"), VIOLATIONS, ids=[v[0] for v in VIOLATIONS])
def test_planted_violation_fails(
    tmp_path: Path, name: str, fn: Callable[[Any], Any], changes: dict[str, str | None]
) -> None:
    repo = make_repo(tmp_path, changes)
    result = run_one(repo, fn)
    assert result.status == check.FAIL, (name, result.reason, result.details)


def test_every_static_commit_check_has_a_planted_violation() -> None:
    planted = {fn for _, fn, _ in VIOLATIONS}
    planted |= {check.check_secrets_history, check.check_provenance}  # tested below with real commits
    missing = [c.check_id for c in STATIC_CHECKS if c.fn not in planted]
    assert missing == []


def test_secrets_are_redacted_in_the_report(tmp_path: Path) -> None:
    secret = key("AIza", 35, "Q")
    repo = make_repo(tmp_path, {"web/src/key.ts": f"const k = '{secret}';\n"})
    result = run_one(repo, check.check_secrets_tree)
    assert result.status == check.FAIL
    assert secret not in " ".join(result.details) + result.reason
    assert all(secret not in run.output for run in result.runs)


def test_firestore_rules_comments_are_ignored(tmp_path: Path) -> None:
    rules = GOOD_RULES.replace("// Clients never write", "/* allow write: if true; */ // allow delete: if x")
    repo = make_repo(tmp_path, {"firestore.rules": rules})
    assert run_one(repo, check.check_firestore_rules).status == check.PASS


def test_placeholders_allowed_in_tests_and_seed_files(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        {
            "web/src/review/view.test.ts": "// TODO lorem [VERIFY]\n",
            "web/src/data/fixtures.test-util.ts": "// TODO\n",
            "backend/argus_api/seed_demo_data.py": "# TODO\n",
            "backend/tests/test_more.py": "# [VERIFY]\n",
        },
    )
    assert run_one(repo, check.check_placeholders).status == check.PASS


def test_missing_readme_is_a_warning_not_a_pass(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"README.md": None})
    assert run_one(repo, check.check_placeholders).status == check.WARN


def test_eval_cases_need_no_marker_but_no_personal_data(tmp_path: Path) -> None:
    """Eval cases are test fixtures, not sample policies: B-09 asks for no personal data,
    not a per-item marker. Paragraph numbers, years, amounts and rates are not identifiers."""
    cases = [
        {
            "case_id": "a-07",
            "clause_text": (
                "Loans up to Rs 10,00,000 follow para 14(9) of DoR.FIN.REC.123/2025-26 dated 2025-07-15."
            ),
            "gold_citation": "RBI/2025-26/45 para 10.2",
        }
    ]
    repo = make_repo(tmp_path, {"backend/argus_api/eval/cases/attacks.json": json.dumps(cases)})
    assert run_one(repo, check.check_synthetic).status == check.PASS


def test_fake_in_documentation_is_a_warning(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"web/README.md": "npm run seed-demo  # FAKE demo data\n"})
    assert run_one(repo, check.check_synthetic).status == check.WARN


def test_staged_but_uncommitted_violation_is_caught(tmp_path: Path) -> None:
    """The pre-commit hook runs before the commit exists: the checks read the index."""
    repo = make_repo(tmp_path)
    write(repo, {"backend/.env": "GEMINI_API_KEY=x\n"})  # staged only
    assert run_one(repo, check.check_forbidden_files).status == check.FAIL


# ------------------------------------------------------------------------------------------- copy checks
@pytest.mark.parametrize(
    "sentence",
    [
        "Search is keyword search (BM25) over the library; there are no embeddings.",
        "Argus uses keyword search, not vector search.",
        "No embeddings, no vector search and no Cloud Storage.",
        "Runs on gemini-3.8-flash rather than Gemini Pro.",
        "The monitor does not claim it prevents injection.",
        "Argus is not FREE-AI compliant; it is a review aid.",
        "Policies are reviewed whole, without clause splitting.",
        "There is no uploaded policy: Argus reviews the two sample files.",
        "Argus never splits the policy; the sample files are already split into clauses.",
    ],
)
def test_negated_mentions_of_retired_features_pass(tmp_path: Path, sentence: str) -> None:
    assert check.banned_phrase_hits(sentence) == []
    repo = make_repo(tmp_path, {"README.md": f"# Tiny\n\n## Limitations\n\n{sentence}\n"})
    assert run_one(repo, check.check_banned_phrases).status == check.PASS


def test_negation_in_an_earlier_sentence_does_not_excuse(tmp_path: Path) -> None:
    text = "There is no upload button. Argus stores policies in Cloud Storage."
    assert [phrase for _, phrase, _ in check.banned_phrase_hits(text)] == ["Cloud Storage"]
    for phrase in check.NEVER_NEGATED:
        assert check.banned_phrase_hits(f"It is not true that {phrase} anything.") != [], phrase


def test_banned_phrases_allowed_outside_the_shipped_copy(tmp_path: Path) -> None:
    """Tests may name a phrase to assert it is gone, and backend prompts are not product copy."""
    repo = make_repo(
        tmp_path,
        {
            "web/src/start/copy.test.ts": "// 'no finding rests on' is gone\nexport {};\n",
            "backend/argus_api/notes.py": '"""The library is a snapshot as on a date."""\n',
        },
    )
    assert run_one(repo, check.check_banned_phrases).status == check.PASS


def test_tagline_is_read_from_the_real_copy_file() -> None:
    assert TAGLINE == " ".join(TAGLINE.split()) and len(TAGLINE) > check.TAGLINE_PIECE
    assert check.read_tagline("export const TAGLINE =\n  \"One \" +\n  'two';\n") == "One two"


def test_tagline_may_be_imported_and_quoted_in_tests(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        {
            "web/src/start/Start.tsx": 'import { TAGLINE } from "./copy";\nexport const t = TAGLINE;\n',
            "web/src/start/copy.test.ts": f"export const expected = {TAGLINE!r};\n",
        },
    )
    assert run_one(repo, check.check_tagline).status == check.PASS


def test_real_seed_scripts_have_the_emulator_guard() -> None:
    for rel in ("backend/argus_api/seed_fake_run.py", "web/scripts/seed_demo.py"):
        assert check.seed_guard_problems((REPO / rel).read_text(encoding="utf-8")) == [], rel


def test_no_seed_script_is_a_warning(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"backend/argus_api/seed_fake_run.py": None, "web/scripts/seed_demo.py": None})
    assert run_one(repo, check.check_seed_guard).status == check.WARN


def test_submission_markdown_is_read_as_the_description(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    desc = tmp_path / "SUBMISSION.md"
    desc.write_text("Argus runs Gemini on Cloud Run with Firestore and Firebase. It prevents injection.\n")
    result = run_one(repo, check.check_submission_phrases, submission=str(desc))
    assert result.status == check.FAIL and "prevents injection" in result.details[0]
    desc.write_text("Argus runs Gemini on Cloud Run with Firestore and Firebase; no embeddings.\n")
    result = run_one(repo, check.check_submission_phrases, submission=str(desc))
    assert result.status == check.BLOCKED  # the deck was not given, so the check is not complete
    assert run_one(repo, check.check_description, submission=str(desc)).status == check.PASS


def test_submission_phrases_read_the_deck(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (tmp_path / "SUBMISSION.md").write_text("Gemini, Cloud Run, Firestore, Firebase.\n")
    sub = tmp_path / "submission.json"
    sub.write_text(json.dumps({"description_file": "SUBMISSION.md", "deck_pdf": "deck.pdf"}))
    (tmp_path / "deck.pdf").write_bytes(make_pdf([FILLED_SLIDE1, ["Architecture", "Gemini Pro agent"]]))
    result = run_one(repo, check.check_submission_phrases, submission=str(sub))
    assert result.status == check.FAIL and "Gemini Pro" in " ".join(result.details)
    (tmp_path / "deck.pdf").write_bytes(make_pdf([FILLED_SLIDE1, ["Architecture", "Gemini Flash agent"]]))
    assert run_one(repo, check.check_submission_phrases, submission=str(sub)).status == check.PASS


# ------------------------------------------------------------------------------------------- history
def test_secret_removed_from_tree_still_fails_history(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"backend/leak.txt": key("sk-" + "ant-api03-", 40, "z")})
    git(repo, "commit", "-q", "-m", "Add a file")
    write(repo, {"backend/leak.txt": None})
    git(repo, "commit", "-q", "-m", "Remove the file")
    assert run_one(repo, check.check_secrets_tree).status == check.PASS
    result = run_one(repo, check.check_secrets_history)
    assert result.status == check.FAIL
    assert "backend/leak.txt" in result.details[0]
    assert all(not run.store_raw for run in result.runs if run.command.startswith("git log"))


def test_secret_in_a_commit_message_fails_history(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"backend/argus_api/x.py": "X = 1\n"})
    git(repo, "commit", "-q", "-m", "Use key " + key("AIza", 35, "m"))
    assert run_one(repo, check.check_secrets_history).status == check.FAIL


def test_commit_before_the_launch_fails_provenance(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"backend/argus_api/x.py": "X = 1\n"})
    git(repo, "commit", "-q", "-m", "Old work", date="2026-10-15T23:00:00+05:30")
    assert run_one(repo, check.check_provenance).status == check.FAIL


def test_provenance_dates_are_read_in_ist(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"backend/argus_api/x.py": "X = 1\n"})
    git(repo, "commit", "-q", "-m", "Late night in UTC", date="2026-10-15T20:00:00+00:00")  # 01:30 IST Oct 16
    assert run_one(repo, check.check_provenance).status == check.PASS


def test_co_authored_by_line_fails_provenance(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"backend/argus_api/x.py": "X = 1\n"})
    git(repo, "commit", "-q", "-m", "Add x\n\nCo-authored-by: Someone <s@example.com>")
    result = run_one(repo, check.check_provenance)
    assert result.status == check.FAIL
    assert "Co-Authored-By" in result.details[0]


def test_no_commits_is_blocked_not_pass(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    git(repo, "init", "-q")
    assert run_one(repo, check.check_provenance).status == check.BLOCKED


# ------------------------------------------------------------------------------------------- web checks
NODE = shutil.which("node") or (os.environ.get("ARGUS_NODE_DIR", "") + "/node")
REAL_NODE_MODULES = REPO / "web" / "node_modules"
needs_node = pytest.mark.skipif(
    not Path(NODE).is_file() or not (REAL_NODE_MODULES / ".bin" / "tsc").exists(),
    reason="node or web/node_modules not available",
)
WEB_FILES = {
    "web/tsconfig.json": json.dumps(
        {
            "compilerOptions": {
                "strict": True,
                "noEmit": True,
                "module": "ESNext",
                "moduleResolution": "bundler",
                "target": "ES2022",
                "skipLibCheck": True,
                "types": [],
            },
            "include": ["src"],
        }
    ),
    "web/eslint.config.js": (
        'import js from "@eslint/js";\nimport tseslint from "typescript-eslint";\n'
        'export default tseslint.config({ files: ["**/*.ts"], extends: [js.configs.recommended, '
        "...tseslint.configs.recommended] });\n"
    ),
    "web/src/math.ts": "export function add(a: number, b: number): number {\n  return a + b;\n}\n",
    "web/src/math.test.ts": (
        'import { expect, test } from "vitest";\nimport { add } from "./math";\n\n'
        'test("adds", () => {\n  expect(add(1, 2)).toBe(3);\n});\n'
    ),
    "web/src/App.tsx": None,
    "web/src/App.test.tsx": None,
}


def make_web_repo(tmp_path: Path, changes: dict[str, str | None] | None = None) -> Path:
    repo = make_repo(tmp_path, {**WEB_FILES, **(changes or {})})
    (repo / "web" / "package.json").write_text(json.dumps({"name": "tiny-web", "type": "module"}))
    (repo / "web" / "node_modules").symlink_to(REAL_NODE_MODULES)
    return repo


def web_ctx(repo: Path) -> Any:
    ctx = ctx_for(repo)
    ctx.node_dir = Path(NODE).parent
    return ctx


WEB_CASES: list[tuple[str, Callable[[Any], Any], dict[str, str | None]]] = [
    ("tsc-type-error", check.check_web_types, {"web/src/bad.ts": "export const n: number = 'x';\n"}),
    ("eslint-unused", check.check_web_lint, {"web/src/bad.ts": "const unused = 1;\nexport {};\n"}),
    (
        "vitest-failing",
        check.check_web_tests,
        {
            "web/src/bad.test.ts": (
                'import { expect, test } from "vitest";\n\ntest("bad", () => {\n  expect(1).toBe(2);\n});\n'
            )
        },
    ),
]


@needs_node
@pytest.mark.parametrize(("name", "fn", "changes"), WEB_CASES, ids=[c[0] for c in WEB_CASES])
def test_web_check_passes_clean_and_fails_planted(
    tmp_path: Path, name: str, fn: Callable[[Any], Any], changes: dict[str, str | None]
) -> None:
    clean = make_web_repo(tmp_path / "clean")
    [ok] = check.run_checks(web_ctx(clean), [check.Check("T", (), fn)])
    assert ok.status == check.PASS, (name, ok.reason, ok.details)
    bad = make_web_repo(tmp_path / "bad", changes)
    [failed] = check.run_checks(web_ctx(bad), [check.Check("T", (), fn)])
    assert failed.status == check.FAIL, (name, failed.reason, failed.details)


def test_web_checks_blocked_without_node_modules(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    for item in (check.check_web_types, check.check_web_lint, check.check_web_tests):
        assert run_one(repo, item).status == check.BLOCKED


# ------------------------------------------------------------------------------------------- the CLI
def run_cli(repo: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    out = repo.parent / "out.json"
    code = check.main(
        ["commit", "--fast", "--repo", str(repo), "--venv", str(VENV_BIN), "--json", str(out), *extra]
    )
    return code, json.loads(out.read_text())


def test_cli_exit_codes_follow_fail_and_strict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)
    code, data = run_cli(repo)
    by_id = {r["id"]: r["status"] for r in data["results"]}
    assert code == 0 and data["summary"]["FAIL"] == 0
    assert by_id["C-WEB-TYPES"] == check.BLOCKED  # no node_modules in the tiny repo: blocked, never PASS
    assert "C-PY-TESTS" not in by_id and data["skipped"] == ["C-PY-TESTS", "C-WEB-TESTS"]
    strict_code, _ = run_cli(repo, "--strict")
    assert strict_code == 1
    printed = capsys.readouterr().out
    assert "C-PROVENANCE" in printed and "result: NOT COMPLETE" in printed

    write(repo, {"backend/.env": "X=1\n"})
    code, data = run_cli(repo)
    assert code == 1
    assert {r["id"]: r["status"] for r in data["results"]}["C-FORBIDDEN-FILES"] == check.FAIL


def test_evidence_records_every_command(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    evidence = tmp_path / "evidence"
    check.main(
        ["commit", "--fast", "--repo", str(repo), "--venv", str(VENV_BIN), "--evidence-dir", str(evidence)]
    )
    rows = [json.loads(line) for line in (evidence / "evidence.jsonl").read_text().splitlines()]
    head = git(repo, "rev-parse", "HEAD").strip()
    assert {r["check_id"] for r in rows} == {c.check_id for c in check.COMMIT_CHECKS if not c.slow}
    for row in rows:
        assert row["git_sha"] == head and row["utc"].endswith("Z")
        assert row["commands"], row["check_id"]
        for cmd in row["commands"]:
            assert len(cmd["output_sha256"]) == 64 and "exit_code" in cmd
            if cmd["raw_file"]:
                assert (evidence / cmd["raw_file"]).is_file()


def test_missing_tools_are_blocked(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    ctx = check.Ctx(repo, venv_bin=tmp_path / "no-venv")
    results = check.run_checks(ctx, [c for c in STATIC_CHECKS if c.check_id in {"C-PY-LINT", "C-PY-TYPES"}])
    assert [r.status for r in results] == [check.BLOCKED, check.BLOCKED]


# ------------------------------------------------------------------------------------------- coverage
def test_every_rule_is_checked_or_human() -> None:
    expected = {f"E-0{i}" for i in range(1, 7)} | {f"T-0{i}" for i in range(1, 7)}
    expected |= {f"B-0{i}" for i in range(1, 10)} | {f"S-0{i}" for i in range(1, 9)}
    cov = check.coverage()
    assert set(check.RULES) == expected
    assert [rule for rule in expected if not cov[rule]] == []


def test_rules_coverage_flags_a_rule_the_script_does_not_know(tmp_path: Path) -> None:
    rules_md = tmp_path / "RULES.md"
    rows = "\n".join(f"| {rule} | x | y | z |" for rule in [*check.RULES, "S-09"])
    rules_md.write_text(rows + "\n")
    assert check.main(["rules-coverage", "--rules-file", str(rules_md)]) == 1
    rules_md.write_text("\n".join(f"| {rule} | x | y | z |" for rule in check.RULES) + "\n")
    assert check.main(["rules-coverage", "--rules-file", str(rules_md)]) == 0


# ---------------------------------------------------------------------------------- submission (offline)
def test_slide1_fields_are_read_between_labels() -> None:
    text = (
        "Team Details Team name: Argus Team leader name: Hemant Kumar BK Problem Statement: Lenders cite "
        "withdrawn RBI rules in their policies and nobody notices until an audit."
    )
    values = check.slide1_fields(text)
    assert values["team name"] == "Argus"
    assert values["team leader"] == "Hemant Kumar BK"
    assert values["problem statement"].startswith("Lenders cite")
    assert (
        check.slide1_fields("Team Details Team name: Team leader name: Problem Statement:")["team name"] == ""
    )


def test_submission_without_inputs_is_blocked(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    for item in check.SUBMISSION_CHECKS:
        if item.check_id == "S-README":
            continue
        assert run_one(repo, item.fn, submission=None).status == check.BLOCKED, item.check_id
    sub = tmp_path / "submission.json"
    sub.write_text(json.dumps({"deck_pdf": "missing.pdf", "video_url": "https://example.com/v.mp4"}))
    assert run_one(repo, check.check_deck, submission=str(sub)).status == check.BLOCKED
    assert run_one(repo, check.check_video_public, submission=str(sub)).status == check.FAIL


def test_description_must_name_all_four(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    desc = tmp_path / "description.md"
    sub = tmp_path / "submission.json"
    sub.write_text(json.dumps({"description_file": "description.md"}))
    desc.write_text("Argus uses Gemini on Cloud Run and stores runs in Firestore.")
    assert run_one(repo, check.check_description, submission=str(sub)).status == check.FAIL
    desc.write_text("Argus uses Gemini on Cloud Run, Firestore for runs, Firebase Hosting and Auth.")
    assert run_one(repo, check.check_description, submission=str(sub)).status == check.PASS
    desc.write_text("Gemini, Cloud Run, Firestore, Firebase. Accuracy: not measured yet.")
    assert run_one(repo, check.check_description, submission=str(sub)).status == check.FAIL


def make_pdf(pages: list[list[str]]) -> bytes:
    """A minimal text PDF, so the deck checks run offline without a template export."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        content = "BT /F1 12 Tf 50 750 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
        objs.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>"
        )
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = "%PDF-1.4\n", []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(
        f"{o:010d} 00000 n \n" for o in offsets
    )
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


FILLED_SLIDE1 = [
    "Team Details",
    "Team name: Argus",
    "Team leader name: Hemant Kumar BK",
    "Problem Statement: Lenders cite withdrawn RBI rules in policies and nobody notices",
]
TEMPLATE_SLIDE1 = ["Team Details", "Team name:", "Team leader name:", "Problem Statement:"]


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        ([FILLED_SLIDE1, ["Architecture"]], (check.PASS, check.PASS)),
        ([TEMPLATE_SLIDE1, ["Note: Kindly refrain from sending edit requests."]], (check.FAIL, check.FAIL)),
        ([FILLED_SLIDE1[:2] + FILLED_SLIDE1[3:]], (check.PASS, check.FAIL)),  # no team leader
    ],
    ids=["filled", "template", "no-leader"],
)
def test_deck_checks(tmp_path: Path, pages: list[list[str]], expected: tuple[str, str]) -> None:
    repo = make_repo(tmp_path)
    (tmp_path / "deck.pdf").write_bytes(make_pdf(pages))
    sub = tmp_path / "submission.json"
    sub.write_text(
        json.dumps({"deck_pdf": "deck.pdf", "team_name": "Argus", "team_leader": "Hemant Kumar BK"})
    )
    deck = run_one(repo, check.check_deck, submission=str(sub))
    slide1 = run_one(repo, check.check_deck_slide1, submission=str(sub))
    assert (deck.status, slide1.status) == expected, (deck.reason, slide1.details)


# ---------------------------------------------------------------------------------- deploy (offline)
SA = "argus-api@p.iam.gserviceaccount.com"


def describe_json(
    env: dict[str, str],
    min_i: str | None = "1",
    max_i: str = "3",
    timeout: int = 300,
    account: str = SA,
    traffic: list[dict[str, Any]] | None = None,
) -> str:
    """`gcloud run services describe --format=json`, trimmed to the fields check.py reads (shape as recorded
    from argus-api on 2026-10-17: no minScale annotation at all when min instances is 0)."""
    annotations = {"autoscaling.knative.dev/maxScale": max_i}
    if min_i is not None:
        annotations["autoscaling.knative.dev/minScale"] = min_i
    return json.dumps(
        {
            "metadata": {"name": "argus-api", "annotations": {"run.googleapis.com/ingress": "all"}},
            "spec": {
                "template": {
                    "metadata": {"annotations": annotations},
                    "spec": {
                        "timeoutSeconds": timeout,
                        "serviceAccountName": account,
                        "containers": [{"env": [{"name": k, "value": v} for k, v in env.items()]}],
                    },
                }
            },
            "status": {
                "latestReadyRevisionName": "argus-api-00003-6xj",
                "traffic": traffic
                if traffic is not None
                else [{"latestRevision": True, "percent": 100, "revisionName": "argus-api-00003-6xj"}],
            },
        }
    )


CLOUD_ENV = {
    "ARGUS_MODE": "cloud",
    "GOOGLE_CLOUD_PROJECT": "p",
    "GOOGLE_CLOUD_LOCATION": "global",
    "ARGUS_ALLOWED_ORIGINS": "https://p.web.app",
    "ARGUS_GIT_SHA": "abc",
}
OLD_REVISION = [
    {"latestRevision": False, "percent": 100, "revisionName": "argus-api-00002-qqg"},
    {"latestRevision": True, "percent": 0, "revisionName": "argus-api-00003-6xj"},
]
# scripts/release.sh promote: the tested revision pinned by name, still tagged "candidate".
PINNED_LATEST = [
    {
        "percent": 100,
        "revisionName": "argus-api-00003-6xj",
        "tag": "candidate",
        "url": "https://candidate---x",
    }
]
SPLIT = [
    {"latestRevision": True, "percent": 50, "revisionName": "argus-api-00003-6xj"},
    {"percent": 50, "revisionName": "argus-api-00002-qqg"},
]


@pytest.mark.parametrize(
    ("env", "kwargs", "allow_min_zero", "expected"),
    [
        (CLOUD_ENV, {}, False, check.PASS),
        ({**CLOUD_ENV, "ARGUS_MODE": "local"}, {}, False, check.FAIL),
        ({**CLOUD_ENV, "OPENROUTER_API_KEY": "x"}, {}, False, check.FAIL),
        ({**CLOUD_ENV, "ARGUS_AGENT_MODEL": "x"}, {}, False, check.FAIL),
        ({**CLOUD_ENV, "SOME_API_KEY": "x"}, {}, False, check.FAIL),
        ({**CLOUD_ENV, "FIRESTORE_EMULATOR_HOST": "x"}, {}, False, check.FAIL),
        ({**CLOUD_ENV, "OPENROUTER_BASE": "x"}, {}, False, check.FAIL),
        (CLOUD_ENV, {"min_i": "0"}, False, check.FAIL),
        (CLOUD_ENV, {"min_i": None}, False, check.FAIL),
        (CLOUD_ENV, {"min_i": None}, True, check.PASS),
        (CLOUD_ENV, {"min_i": "0"}, True, check.PASS),
        (CLOUD_ENV, {"min_i": "2"}, True, check.FAIL),
        (CLOUD_ENV, {"max_i": "10"}, False, check.FAIL),
        (CLOUD_ENV, {"timeout": 60}, False, check.FAIL),
        (CLOUD_ENV, {"account": "419264532549-compute@developer.gserviceaccount.com"}, False, check.FAIL),
        (CLOUD_ENV, {"traffic": OLD_REVISION}, False, check.FAIL),
        (CLOUD_ENV, {"traffic": PINNED_LATEST}, False, check.PASS),
        (CLOUD_ENV, {"traffic": SPLIT}, False, check.FAIL),
    ],
    ids=[
        "good",
        "local-mode",
        "openrouter-key",
        "model-override",
        "any-key-name",
        "emulator",
        "openrouter-other",
        "min-0",
        "min-unset",
        "min-unset-allowed",
        "min-0-allowed",
        "min-2-allowed-flag",
        "max-10",
        "timeout-60",
        "default-compute-sa",
        "traffic-on-old-revision",
        "traffic-pinned-to-the-latest-ready-revision",
        "traffic-split",
    ],
)
def test_run_config_reads_the_service_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    kwargs: dict[str, Any],
    allow_min_zero: bool,
    expected: str,
) -> None:
    def fake_gcloud(ctx: Any, *args: str) -> Any:
        return check.CmdRun("gcloud " + " ".join(args), ".", 0, describe_json(env, **kwargs), "t", 0.0)

    monkeypatch.setattr(check, "_gcloud", fake_gcloud)
    result = run_one(
        tmp_path,
        check.check_deploy_run_config,
        project="p",
        service_url="https://x.run.app",
        service="argus-api",
        region="asia-south1",
        allow_min_zero=allow_min_zero,
    )
    assert result.status == expected, (result.reason, result.details)
    assert "argus-api-00003-6xj" in result.reason


def test_deploy_without_project_is_blocked(tmp_path: Path) -> None:
    for item in check.DEPLOY_CHECKS:
        assert run_one(tmp_path, item.fn, project=None, service_url=None).status == check.BLOCKED


# ---------------------------------------------------------------------------------- deploy: public samples
def firestore_doc(run_id: str, **fields: Any) -> dict[str, Any]:
    def value(v: Any) -> dict[str, Any]:
        if v is None:
            return {"nullValue": None}
        return {"booleanValue": v} if isinstance(v, bool) else {"stringValue": v}

    return {
        "name": f"projects/p/databases/(default)/documents/samples/{run_id}",
        "fields": {k: value(v) for k, v in fields.items()},
    }


@pytest.mark.parametrize(
    ("docs", "expected"),
    [
        ([firestore_doc("r1", seeded=False, provider=SAMPLE_PROVIDER)], check.PASS),
        ([firestore_doc("r1", seeded=True, provider=SAMPLE_PROVIDER)], check.FAIL),
        ([firestore_doc("r1", seeded=False, provider="openrouter")], check.FAIL),
        ([firestore_doc("r1", seeded=False, provider=None)], check.FAIL),
        ([firestore_doc("r1")], check.FAIL),  # written before provider existed: not shown to be a Vertex run
        ([], check.WARN),
    ],
    ids=["vertex", "seeded", "openrouter", "provider-null", "provider-missing", "empty"],
)
def test_deploy_samples_reads_production_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, docs: list[dict[str, Any]], expected: str
) -> None:
    pages = [docs[:1], docs[1:]] if len(docs) > 1 else [docs]
    seen: list[str] = []

    def fake_http(ctx: Any, url: str, **kwargs: Any) -> Any:
        seen.append(url)
        i = len(seen) - 1
        body: dict[str, Any] = {"documents": pages[i]} if pages[i] else {}
        if i + 1 < len(pages):
            body["nextPageToken"] = "next"
        return check.HttpResult(200, url, {}, json.dumps(body).encode())

    monkeypatch.setattr(check.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(check, "_gcloud_token", lambda ctx: "token")
    monkeypatch.setattr(check, "http", fake_http)
    result = run_one(tmp_path, check.check_deploy_samples, project="p")
    assert result.status == expected, (result.reason, result.details)
    assert all("/projects/p/databases/(default)/documents/samples?" in u for u in seen)


def test_deploy_samples_pages_through_the_collection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        [firestore_doc("r1", seeded=False, provider=SAMPLE_PROVIDER)],
        [firestore_doc("r2", seeded=True)],
    ]
    calls: list[str] = []

    def fake_http(ctx: Any, url: str, **kwargs: Any) -> Any:
        calls.append(url)
        body: dict[str, Any] = {"documents": pages[len(calls) - 1]}
        if len(calls) < len(pages):
            body["nextPageToken"] = "tok2"
        return check.HttpResult(200, url, {}, json.dumps(body).encode())

    monkeypatch.setattr(check.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(check, "_gcloud_token", lambda ctx: "token")
    monkeypatch.setattr(check, "http", fake_http)
    result = run_one(tmp_path, check.check_deploy_samples, project="p")
    assert result.status == check.FAIL and len(calls) == 2 and "pageToken=tok2" in calls[1]
    assert [d.split(":")[0] for d in result.details] == ["samples/r2", "samples/r2"]


@pytest.mark.parametrize("problem", ["no-gcloud", "no-token", "http-403", "unreachable"])
def test_deploy_samples_blocked_without_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    monkeypatch.setattr(check.shutil, "which", lambda name: None if problem == "no-gcloud" else "/x/" + name)
    monkeypatch.setattr(check, "_gcloud_token", lambda ctx: None if problem == "no-token" else "token")
    status = {"http-403": 403, "unreachable": None}.get(problem, 200)
    monkeypatch.setattr(
        check,
        "http",
        lambda ctx, url, **kw: check.HttpResult(status, url, {}, b"{}", "" if status else "down"),
    )
    assert run_one(tmp_path, check.check_deploy_samples, project="p").status == check.BLOCKED


def test_sample_provider_matches_the_backend() -> None:
    assert check.SAMPLE_PROVIDER == SAMPLE_PROVIDER


# ---------------------------------------------------------------------------------- deploy: recorded fakes
# Responses below copy the shape of what the live services returned on 2026-10-17 (values shortened).
LIVE = {
    "project": "p",
    "service_url": "https://argus-api-1.asia-south1.run.app",
    "hosting_origin": "https://p.web.app",
}
PROJECT_NUMBER = "419264532549"
WEB_KEY = key("AIza", 35, "W")


def route_http(
    monkeypatch: pytest.MonkeyPatch, routes: dict[str, tuple[int | None, Any]]
) -> list[tuple[str, dict[str, Any]]]:
    """Answer check.http from routes {url substring: (status, body)}; the longest matching substring wins.
    A dict or list body is sent as JSON. An unmatched URL is unreachable, so a missed route shows up."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_http(ctx: Any, url: str, **kwargs: Any) -> Any:
        calls.append((url, kwargs))
        matches = [k for k in routes if k in url]
        if not matches:
            return check.HttpResult(None, url, {}, b"", "no route in test")
        status, body = routes[max(matches, key=len)]
        raw = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        return check.HttpResult(status, url, {}, raw, "" if status else "down")

    monkeypatch.setattr(check, "http", fake_http)
    return calls


def route_gcloud(monkeypatch: pytest.MonkeyPatch, routes: dict[str, tuple[int, Any]]) -> list[str]:
    """Answer check._gcloud from routes {joined-args prefix: (exit code, stdout)}."""
    calls: list[str] = []

    def fake_gcloud(ctx: Any, *args: str) -> Any:
        joined = " ".join(args)
        calls.append(joined)
        for prefix, (code, out) in routes.items():
            if joined.startswith(prefix):
                text = json.dumps(out) if isinstance(out, (dict, list)) else str(out)
                return check.CmdRun("gcloud " + joined, ".", code, text, "t", 0.0)
        return check.CmdRun("gcloud " + joined, ".", 1, "no route in test", "t", 0.0)

    monkeypatch.setattr(check, "_gcloud", fake_gcloud)
    return calls


@pytest.fixture
def logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(check, "_gcloud_token", lambda ctx: "token")


# ---------------------------------------------------------------------------------- D-HEALTH
MODELS = ("gemini-agent-x", "gemini-monitor-y")
CORPUS = "c" * 64
SHA = "a" * 40
HEALTH = {
    "status": "ok",
    "git_sha": SHA,
    "mode": "cloud",
    "provider": "vertex",
    "agent_model": MODELS[0],
    "monitor_model": MODELS[1],
    "location": "global",
    "corpus_hash": CORPUS,
}


@pytest.mark.parametrize(
    ("change", "expect_sha", "expected"),
    [
        ({}, SHA, check.PASS),
        ({}, None, check.PASS),
        ({"git_sha": "b" * 40}, SHA, check.FAIL),
        ({"git_sha": "unknown"}, None, check.FAIL),
        ({"mode": "local"}, SHA, check.FAIL),
        ({"provider": "openrouter"}, SHA, check.FAIL),
        ({"agent_model": "gemini-latest"}, SHA, check.FAIL),
        ({"monitor_model": "other"}, SHA, check.FAIL),
        ({"location": "asia-south1"}, SHA, check.FAIL),
        ({"corpus_hash": "d" * 64}, SHA, check.FAIL),
    ],
    ids=[
        "good",
        "good-no-sha",
        "other-sha",
        "sha-unknown",
        "local",
        "openrouter",
        "agent",
        "monitor",
        "location",
        "corpus",
    ],
)
def test_deploy_health_compares_every_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, str],
    expect_sha: str | None,
    expected: str,
) -> None:
    route_http(monkeypatch, {"/health": (200, {**HEALTH, **change})})
    monkeypatch.setattr(check, "repo_pinned_models", lambda ctx: MODELS)
    monkeypatch.setattr(check, "repo_corpus_hash", lambda ctx: CORPUS)
    result = run_one(tmp_path, check.check_deploy_health, **LIVE, expect_sha=expect_sha)
    assert result.status == expected, (result.reason, result.details)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [(None, b"", check.BLOCKED), (503, b"{}", check.FAIL), (200, b"<html>", check.FAIL)],
    ids=["unreachable", "503", "not-json"],
)
def test_deploy_health_bad_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int | None, body: bytes, expected: str
) -> None:
    route_http(monkeypatch, {"/health": (status, body)})
    monkeypatch.setattr(check, "repo_pinned_models", lambda ctx: MODELS)
    monkeypatch.setattr(check, "repo_corpus_hash", lambda ctx: CORPUS)
    assert run_one(tmp_path, check.check_deploy_health, **LIVE).status == expected


def test_repo_pinned_models_are_the_vertex_pins() -> None:
    from argus_api.config import PINNED_MODELS

    assert check.repo_pinned_models(ctx_for(REPO)) == PINNED_MODELS["vertex"]


@pytest.mark.skipif(not (REPO / "backend/data/corpus/paragraphs.json").is_file(), reason="no local corpus")
def test_repo_corpus_hash_is_the_loaded_corpus_hash() -> None:
    from argus_api.corpus.store import Corpus

    assert check.repo_corpus_hash(ctx_for(REPO)) == Corpus.load().corpus_hash


# ---------------------------------------------------------------------------------- D-WEB, D-WEB-SECRETS
INDEX_HTML = (
    b'<!doctype html><link rel="icon" href="/favicon.svg" />'
    b'<script type="module" crossorigin src="/assets/index-abc.js"></script>'
    b'<link rel="stylesheet" crossorigin href="/assets/index-def.css">'
)


def bundle(extra: str = "") -> bytes:
    config = f'const c={{apiKey:"{WEB_KEY}",authDomain:"p.firebaseapp.com"}};'
    api = 'const API="https://argus-api-1.asia-south1.run.app";'
    return (config + api + extra + "\n//# sourceMappingURL=index-abc.js.map\n").encode()


def web_repo(tmp_path: Path, js: bytes, *, dist: bool = True, env_key: str | None = WEB_KEY) -> Path:
    repo = tmp_path / "repo"
    (repo / "web" / "dist" / "assets").mkdir(parents=True)
    if dist:
        (repo / "web" / "dist" / "index.html").write_bytes(INDEX_HTML)
        (repo / "web" / "dist" / "assets" / "index-abc.js").write_bytes(js)
        (repo / "web" / "dist" / "assets" / "index-def.css").write_bytes(b"body{}")
    if env_key is not None:
        (repo / "web" / ".env.production.local").write_text(
            f"# public web config\nVITE_USE_EMULATORS=false\nVITE_FIREBASE_API_KEY={env_key}\n",
            encoding="utf-8",
        )
    return repo


def site_routes(
    js: bytes, source_map: Any = None, index: tuple[int, bytes] = (200, INDEX_HTML)
) -> dict[str, Any]:
    routes: dict[str, Any] = {
        "web.app/": index,
        "/assets/index-abc.js": (200, js),
        "/assets/index-def.css": (200, b"body{}"),
        "/assets/index-abc.js.map": (404, b"") if source_map is None else (200, source_map),
    }
    return routes


@pytest.mark.parametrize(
    ("served_js", "index", "dist", "expected"),
    [
        (bundle(), (200, INDEX_HTML), True, check.PASS),
        (bundle("changed"), (200, INDEX_HTML), True, check.FAIL),
        (bundle(), (404, b"not found"), True, check.FAIL),
        (bundle(), (None, b""), True, check.BLOCKED),
        (bundle(), (200, INDEX_HTML), False, check.BLOCKED),
        (bundle(), (200, INDEX_HTML.replace(b"index-def.css", b"index-new.css")), True, check.FAIL),
    ],
    ids=["equal", "asset-differs", "index-404", "unreachable", "no-dist", "asset-not-in-dist"],
)
def test_deploy_web_serves_web_dist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    served_js: bytes,
    index: tuple[int | None, bytes],
    dist: bool,
    expected: str,
) -> None:
    repo = web_repo(tmp_path, bundle(), dist=dist)
    routes = site_routes(served_js, index=index)  # type: ignore[arg-type]
    routes["/assets/index-new.css"] = (200, b"body{}")
    route_http(monkeypatch, routes)
    result = run_one(repo, check.check_deploy_web, **LIVE)
    assert result.status == expected, (result.reason, result.details)


@pytest.mark.parametrize(
    ("js", "source_map", "env_key", "expected"),
    [
        (bundle(), None, WEB_KEY, check.PASS),
        (bundle(), b'{"sourcesContent":["export const x = 1"]}', WEB_KEY, check.PASS),
        (bundle(f'const k="{key("AIza", 35, "Z")}";'), None, WEB_KEY, check.FAIL),
        (bundle(), None, key("AIza", 35, "Z"), check.FAIL),
        (
            bundle(),
            json.dumps({"sourcesContent": [f'k="{key("sk-or-v1-", 40, "b")}"']}).encode(),
            WEB_KEY,
            check.FAIL,
        ),
        (bundle('const n="OPENROUTER_' + 'API_KEY";'), None, WEB_KEY, check.FAIL),
        (bundle('{"private_' + 'key": "x"}'), None, WEB_KEY, check.FAIL),
        (bundle(), None, None, check.BLOCKED),
    ],
    ids=[
        "web-key-only",
        "clean-source-map",
        "second-google-key",
        "key-not-the-web-key",
        "secret-in-source-map",
        "server-env-name",
        "private-key-field",
        "no-env-file",
    ],
)
def test_deploy_web_secrets_allow_only_the_web_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    js: bytes,
    source_map: bytes | None,
    env_key: str | None,
    expected: str,
) -> None:
    repo = web_repo(tmp_path, js, env_key=env_key)
    route_http(monkeypatch, site_routes(js, source_map))
    result = run_one(repo, check.check_deploy_web_secrets, **LIVE)
    assert result.status == expected, (result.reason, result.details)
    report = result.reason + " ".join(result.details)
    assert key("sk-or-v1-", 40, "b") not in report and key("AIza", 35, "Z") not in report


def test_served_assets_reads_script_and_style_links() -> None:
    assert check.served_assets(INDEX_HTML.decode()) == ["/assets/index-abc.js", "/assets/index-def.css"]


# ---------------------------------------------------------------------------------- D-NO-TOKEN
@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, check.PASS), (200, check.FAIL), (422, check.FAIL), (500, check.FAIL), (None, check.BLOCKED)],
)
def test_deploy_no_token_post_is_401(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int | None, expected: str
) -> None:
    calls = route_http(monkeypatch, {"/api/runs": (status, {"detail": "Missing bearer token"})})
    assert run_one(tmp_path, check.check_deploy_no_token, **LIVE).status == expected
    [(url, kwargs)] = calls
    assert kwargs["method"] == "POST" and "Authorization" not in kwargs.get("headers", {})


# ---------------------------------------------------------------------------------- D-SA-ROLES
def iam_policy(roles: list[str]) -> dict[str, Any]:
    member = "serviceAccount:argus-api@p.iam.gserviceaccount.com"
    bindings = [{"role": r, "members": [member]} for r in roles]
    bindings.append({"role": "roles/owner", "members": ["user:owner@example.com"]})
    return {"bindings": bindings, "etag": "x", "version": 1}


FOUR_ROLES = [
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/firebaseauth.viewer",
    "roles/logging.logWriter",
]


@pytest.mark.parametrize(
    ("code", "roles", "expected"),
    [
        (0, FOUR_ROLES, check.PASS),
        (0, [*FOUR_ROLES, "roles/editor"], check.FAIL),
        (0, FOUR_ROLES[:3], check.FAIL),
        (1, FOUR_ROLES, check.BLOCKED),
    ],
    ids=["exact", "extra-editor", "missing-logwriter", "gcloud-fails"],
)
def test_deploy_sa_roles_are_exactly_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, roles: list[str], expected: str
) -> None:
    route_gcloud(monkeypatch, {"projects get-iam-policy p": (code, iam_policy(roles))})
    result = run_one(tmp_path, check.check_deploy_sa_roles, **LIVE)
    assert result.status == expected, (result.reason, result.details)


# ---------------------------------------------------------------------------------- D-RULES
RULES_TEXT = "rules_version = '2';\nservice cloud.firestore {\n}\n"


def rules_routes(deployed: str | None, status: int = 200) -> dict[str, Any]:
    release = {"name": "projects/p/releases/cloud.firestore", "rulesetName": "projects/p/rulesets/rs-1"}
    return {
        "/releases/cloud.firestore": (status, release if deployed is not None else {}),
        "/projects/p/rulesets/rs-1": (
            200,
            {
                "name": "projects/p/rulesets/rs-1",
                "source": {"files": [{"name": "firestore.rules", "content": deployed}]},
            },
        ),
    }


@pytest.mark.parametrize(
    ("deployed", "status", "expected"),
    [
        (RULES_TEXT, 200, check.PASS),
        (RULES_TEXT.replace("{\n}", "{ match /{d=**} { allow read; } }"), 200, check.FAIL),
        (None, 200, check.FAIL),
        (RULES_TEXT, 403, check.BLOCKED),
    ],
    ids=["equal", "differs", "never-deployed", "forbidden"],
)
def test_deploy_rules_equal_the_repo_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_in: None,
    deployed: str | None,
    status: int,
    expected: str,
) -> None:
    (tmp_path / "firestore.rules").write_text(RULES_TEXT, encoding="utf-8")
    route_http(monkeypatch, rules_routes(deployed, status))
    result = run_one(tmp_path, check.check_deploy_rules, **LIVE)
    assert result.status == expected, (result.reason, result.details)


# ---------------------------------------------------------------------------------- D-ANON-AUTH
@pytest.mark.parametrize(
    ("status", "config", "expected"),
    [
        (
            200,
            {
                "signIn": {"anonymous": {"enabled": True}},
                "authorizedDomains": ["p.firebaseapp.com", "p.web.app"],
            },
            check.PASS,
        ),
        (200, {"signIn": {"anonymous": {"enabled": False}}, "authorizedDomains": ["p.web.app"]}, check.FAIL),
        (200, {"signIn": {}, "authorizedDomains": ["p.web.app"]}, check.FAIL),
        (200, {"signIn": {"anonymous": {"enabled": True}}, "authorizedDomains": ["localhost"]}, check.FAIL),
        (403, {}, check.BLOCKED),
    ],
    ids=["enabled", "disabled", "provider-absent", "domain-missing", "forbidden"],
)
def test_deploy_anon_auth_and_domains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_in: None,
    status: int,
    config: dict[str, Any],
    expected: str,
) -> None:
    calls = route_http(monkeypatch, {"/admin/v2/projects/p/config": (status, config)})
    result = run_one(tmp_path, check.check_deploy_anon_auth, **LIVE)
    assert result.status == expected, (result.reason, result.details)
    assert calls[0][1]["headers"]["Authorization"] == "Bearer token"


# ---------------------------------------------------------------------------------- D-API-KEY
KEY_NAME = f"projects/{PROJECT_NUMBER}/locations/global/keys/k1"
FIREBASE_TARGETS = [{"service": "identitytoolkit.googleapis.com"}, {"service": "firestore.googleapis.com"}]


def api_key(referrers: list[str] | None, targets: list[dict[str, str]]) -> dict[str, Any]:
    restrictions: dict[str, Any] = {"apiTargets": targets, "browserKeyRestrictions": {}}
    if referrers is not None:
        restrictions["browserKeyRestrictions"] = {"allowedReferrers": referrers}
    return {
        "name": KEY_NAME,
        "displayName": "Browser key (auto created by Firebase)",
        "restrictions": restrictions,
    }


@pytest.mark.parametrize(
    ("lookup_name", "key_doc", "expected"),
    [
        (
            KEY_NAME,
            api_key(["https://p.web.app/*", "https://p.firebaseapp.com/*"], FIREBASE_TARGETS),
            check.PASS,
        ),
        (KEY_NAME, api_key(None, FIREBASE_TARGETS), check.FAIL),  # as live on 2026-10-17
        (KEY_NAME, api_key(["https://evil.example/*"], FIREBASE_TARGETS), check.FAIL),
        (KEY_NAME, api_key(["p.web.app/*"], []), check.FAIL),
        (KEY_NAME, api_key(["p.web.app/*"], [{"service": "generativelanguage.googleapis.com"}]), check.FAIL),
        ("projects/999/locations/global/keys/k9", api_key(["p.web.app/*"], FIREBASE_TARGETS), check.FAIL),
    ],
    ids=[
        "restricted",
        "no-referrers",
        "foreign-referrer",
        "no-api-targets",
        "gemini-target",
        "other-project",
    ],
)
def test_deploy_api_key_is_restricted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_in: None,
    lookup_name: str,
    key_doc: dict[str, Any],
    expected: str,
) -> None:
    repo = web_repo(tmp_path, bundle(), dist=False)
    calls = route_http(
        monkeypatch,
        {
            "keys:lookupKey": (200, {"parent": lookup_name.rsplit("/keys/", 1)[0], "name": lookup_name}),
            f"/v2/{KEY_NAME}": (200, key_doc),
        },
    )
    route_gcloud(monkeypatch, {"projects describe p": (0, PROJECT_NUMBER + "\n")})
    result = run_one(repo, check.check_deploy_api_key, **LIVE)
    assert result.status == expected, (result.reason, result.details)
    lookup_url, kwargs = calls[0]
    assert WEB_KEY in lookup_url and WEB_KEY not in kwargs["shown_url"]  # the evidence never holds the key
    assert "x-goog-user-project" not in kwargs["headers"]  # API Keys need not be enabled on the project


# ---------------------------------------------------------------------------------- D-FIRESTORE-CORPUS
def corpus_docs(collection: str, n: int) -> list[dict[str, Any]]:
    docs = []
    for i in range(n):
        fields = {}
        if collection == "rbi_paragraphs":
            fields = {"id": {"stringValue": f"para-{i:03d}"}, "content_hash": {"stringValue": f"h{i}"}}
        docs.append({"name": f"projects/p/databases/(default)/documents/{collection}/d{i}", "fields": fields})
    return docs


def corpus_hash_of(n: int) -> str:
    from types import SimpleNamespace

    from argus_api.corpus.store import corpus_hash

    # corpus_hash reads only .id and .content_hash, so stand-ins are enough to compute the real function.
    paragraphs: list[Any] = [SimpleNamespace(id=f"para-{i:03d}", content_hash=f"h{i}") for i in range(n)]
    return corpus_hash(paragraphs)


@pytest.mark.parametrize(
    ("counts", "local_hash_n", "expected"),
    [
        ((365, 4, 15), 365, check.PASS),
        ((364, 4, 15), 364, check.FAIL),
        ((365, 3, 15), 365, check.FAIL),
        ((365, 4, 16), 365, check.FAIL),
        ((365, 4, 15), 364, check.FAIL),
    ],
    ids=["full", "paragraph-missing", "instrument-missing", "extra-row", "hash-differs"],
)
def test_deploy_firestore_corpus_counts_and_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_in: None,
    counts: tuple[int, int, int],
    local_hash_n: int,
    expected: str,
) -> None:
    names = ("rbi_paragraphs", "rbi_instruments", "supersession")
    routes: dict[str, Any] = {}
    for name, n in zip(names, counts, strict=True):
        docs = corpus_docs(name, n)
        # two pages for the paragraphs, as the REST API pages at 300
        routes[f"/documents/{name}?"] = (
            200,
            {"documents": docs[:300], **({"nextPageToken": "t2"} if n > 300 else {})},
        )
        routes[
            f"/documents/{name}?pageSize=300&mask.fieldPaths=id&mask.fieldPaths=content_hash&pageToken=t2"
        ] = (200, {"documents": docs[300:]})
    route_http(monkeypatch, routes)
    monkeypatch.setattr(check, "repo_corpus_hash", lambda ctx: corpus_hash_of(local_hash_n))
    result = run_one(tmp_path, check.check_deploy_firestore_corpus, **LIVE)
    assert result.status == expected, (result.reason, result.details)


# ---------------------------------------------------------------------------------- D-LIMITS
def limits_doc(**values: Any) -> dict[str, Any]:
    def v(x: Any) -> dict[str, Any]:
        return {"booleanValue": x} if isinstance(x, bool) else {"integerValue": str(x)}

    return {
        "name": "projects/p/databases/(default)/documents/config/limits",
        "fields": {k: v(x) for k, x in values.items()},
    }


GOOD_LIMITS = {"daily_run_cap": 60, "hourly_run_cap": 20, "per_user_daily_run_cap": 5, "enabled": True}


@pytest.mark.parametrize(
    ("status", "values", "expected"),
    [
        (200, GOOD_LIMITS, check.PASS),
        (200, {**GOOD_LIMITS, "per_user_daily_run_cap": 1}, check.FAIL),
        (200, {**GOOD_LIMITS, "enabled": False}, check.FAIL),
        (200, {**GOOD_LIMITS, "extra": 1}, check.FAIL),
        (404, {}, check.FAIL),
        (403, {}, check.BLOCKED),
    ],
    ids=["60-20-5-on", "user-cap-1", "kill-switch-on", "extra-field", "missing", "forbidden"],
)
def test_deploy_limits_read_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_in: None,
    status: int,
    values: dict[str, Any],
    expected: str,
) -> None:
    route_http(monkeypatch, {"/documents/config/limits": (status, limits_doc(**values))})
    result = run_one(tmp_path, check.check_deploy_limits, **LIVE)
    assert result.status == expected, (result.reason, result.details)


# ---------------------------------------------------------------------------------- D-UPTIME
def uptime_config(host: str, path: str, check_id: str = "argus-health-x1") -> dict[str, Any]:
    return {
        "name": f"projects/p/uptimeCheckConfigs/{check_id}",
        "displayName": "argus health",
        "monitoredResource": {"type": "uptime_url", "labels": {"host": host, "project_id": "p"}},
        "httpCheck": {"path": path, "port": 443, "useSsl": True},
        "period": "300s",
    }


def alert_policy(check_id: str, enabled: bool = True) -> dict[str, Any]:
    return {
        "name": "projects/p/alertPolicies/1",
        "displayName": "argus down",
        "enabled": enabled,
        "conditions": [
            {
                "conditionThreshold": {
                    "filter": 'metric.type="monitoring.googleapis.com/uptime_check/check_passed" '
                    f'AND metric.label.check_id="{check_id}" AND resource.type="uptime_url"'
                }
            }
        ],
    }


API_HOST = "argus-api-1.asia-south1.run.app"


@pytest.mark.parametrize(
    ("configs", "policies", "expected"),
    [
        ([uptime_config(API_HOST, "/health")], [alert_policy("argus-health-x1")], check.PASS),
        ([], [], check.FAIL),  # as live on 2026-10-17
        ([uptime_config(API_HOST, "/")], [alert_policy("argus-health-x1")], check.FAIL),
        ([uptime_config("p.web.app", "/health")], [alert_policy("argus-health-x1")], check.FAIL),
        ([uptime_config(API_HOST, "/health")], [], check.FAIL),
        ([uptime_config(API_HOST, "/health")], [alert_policy("argus-health-x1", enabled=False)], check.FAIL),
        ([uptime_config(API_HOST, "/health")], [alert_policy("other-check")], check.FAIL),
    ],
    ids=["check-and-alert", "none", "wrong-path", "wrong-host", "no-alert", "alert-disabled", "alert-other"],
)
def test_deploy_uptime_check_and_alert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_in: None,
    configs: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    expected: str,
) -> None:
    route_gcloud(monkeypatch, {"monitoring uptime list-configs": (0, configs)})
    route_http(
        monkeypatch, {"/v3/projects/p/alertPolicies": (200, {"alertPolicies": policies} if policies else {})}
    )
    result = run_one(tmp_path, check.check_deploy_uptime, **LIVE)
    assert result.status == expected, (result.reason, result.details)
    if not configs:
        assert "no uptime check" in result.reason and "/health" in result.reason


# ---------------------------------------------------------------------------------- deploy defaults
def test_deploy_defaults_are_the_live_stack() -> None:
    args = check.parse_args(["deploy", "--strict", "--allow-min-zero", "--expect-sha", SHA])
    assert (args.project, args.hosting_origin) == ("argus-aibc", "https://argus-aibc.web.app")
    assert (
        args.service_url.endswith(".asia-south1.run.app") and args.allow_min_zero and args.expect_sha == SHA
    )
    assert check.parse_args(["deploy"]).allow_min_zero is False


def test_every_deploy_check_has_an_offline_test() -> None:
    tested = {
        "D-HEALTH",
        "D-WEB",
        "D-WEB-SECRETS",
        "D-CORS",
        "D-NO-TOKEN",
        "D-RUN-CONFIG",
        "D-SA-ROLES",
        "D-RULES",
        "D-ANON-AUTH",
        "D-API-KEY",
        "D-FIRESTORE-CORPUS",
        "D-LIMITS",
        "D-BUDGET",
        "D-UPTIME",
        "D-SAMPLES",
    }
    assert {c.check_id for c in check.DEPLOY_CHECKS} == tested


# ---------------------------------------------------------------------------------- D-CORS, D-BUDGET
@pytest.mark.parametrize(
    ("good_allow", "evil_allow", "expected"),
    [
        ("https://p.web.app", None, check.PASS),
        (None, None, check.FAIL),
        ("https://p.web.app", "https://evil.example", check.FAIL),
        ("https://p.web.app", "*", check.FAIL),
    ],
    ids=["echoed-and-refused", "not-echoed", "evil-echoed", "wildcard"],
)
def test_deploy_cors_echoes_only_the_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    good_allow: str | None,
    evil_allow: str | None,
    expected: str,
) -> None:
    def fake_http(ctx: Any, url: str, **kwargs: Any) -> Any:
        origin = kwargs["headers"]["Origin"]
        allow = good_allow if origin == "https://p.web.app" else evil_allow
        status = 200 if allow else 400  # Starlette answers a refused preflight with 400 and no allow header
        return check.HttpResult(status, url, {"access-control-allow-origin": allow} if allow else {}, b"")

    monkeypatch.setattr(check, "http", fake_http)
    assert run_one(tmp_path, check.check_deploy_cors, **LIVE).status == expected


@pytest.mark.parametrize(
    ("billing", "budgets", "expected"),
    [
        (
            {"billingEnabled": True, "billingAccountName": "billingAccounts/A-1"},
            [
                {
                    "displayName": "argus-real-cost",
                    "budgetFilter": {"projects": [f"projects/{PROJECT_NUMBER}"]},
                    "amount": {"specifiedAmount": {"units": "200", "currencyCode": "INR"}},
                }
            ],
            check.PASS,
        ),
        (
            {"billingEnabled": True, "billingAccountName": "billingAccounts/A-1"},
            [{"displayName": "other", "budgetFilter": {"projects": ["projects/1"]}}],
            check.FAIL,
        ),
        ({"billingEnabled": False, "billingAccountName": ""}, [], check.FAIL),
    ],
    ids=["budget-on-project", "budget-elsewhere", "billing-off"],
)
def test_deploy_budget_covers_the_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    billing: dict[str, Any],
    budgets: list[Any],
    expected: str,
) -> None:
    route_gcloud(
        monkeypatch,
        {
            "billing projects describe p": (0, billing),
            "projects describe p": (0, PROJECT_NUMBER + "\n"),
            "billing budgets list": (0, budgets),
        },
    )
    result = run_one(tmp_path, check.check_deploy_budget, **LIVE)
    assert result.status == expected, (result.reason, result.details)


# ------------------------------------------------------------------------------------------- pre-commit hook
# A stand-in for scripts/check.py: it records which copy ran (its label, its path, its arguments and the
# directory it ran in) and exits with the given code. The hook must run the copy in the index.
FAKE_CHECKER = """import json, os, sys
run = {{"label": {label!r}, "path": sys.argv[0], "args": sys.argv[1:], "cwd": os.getcwd()}}
with open(os.environ["ARGUS_HOOK_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(run) + "\\n")
sys.exit({code})
"""


def _hook_repo(tmp_path: Path) -> Path:
    """A repo with the real install-hooks.sh, a backend/.venv/bin/python that runs this interpreter, a
    committed scripts/check.py (labelled "committed"), and the hook installed."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "install-hooks.sh", repo / "scripts" / "install-hooks.sh")
    venv_bin = repo / "backend" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)
    (repo / ".gitignore").write_text("backend/.venv/\n", encoding="utf-8")
    (repo / "scripts" / "check.py").write_text(
        FAKE_CHECKER.format(label="committed", code=0), encoding="utf-8"
    )
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "Start")
    subprocess.run(
        ["sh", "scripts/install-hooks.sh"], cwd=repo, check=True, capture_output=True, env=_hook_env(tmp_path)
    )
    return repo


def _hook_env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Tester",
        GIT_AUTHOR_EMAIL="tester@example.com",
        GIT_COMMITTER_NAME="Tester",
        GIT_COMMITTER_EMAIL="tester@example.com",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        ARGUS_HOOK_LOG=str(tmp_path / "hook.log"),
        TMPDIR=str(tmp_path / "tmp"),
    )
    (tmp_path / "tmp").mkdir(exist_ok=True)
    return env


def _commit(repo: Path, tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    log = tmp_path / "hook.log"
    log.unlink(missing_ok=True)
    proc = subprocess.run(
        ["git", "commit", "-q", "-m", "Change"],
        cwd=repo,
        env=_hook_env(tmp_path),
        capture_output=True,
        text=True,
    )
    runs = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return proc, runs


@pytest.mark.parametrize(
    ("staged_code", "worktree_code", "committed"),
    [(0, 1, True), (1, 0, False)],
    ids=["staged checker passes, working tree would fail", "staged checker fails, working tree would pass"],
)
def test_pre_commit_hook_runs_the_staged_checker(
    tmp_path: Path, staged_code: int, worktree_code: int, committed: bool
) -> None:
    repo = _hook_repo(tmp_path)
    checker = repo / "scripts" / "check.py"
    checker.write_text(FAKE_CHECKER.format(label="staged", code=staged_code), encoding="utf-8")
    git(repo, "add", "scripts/check.py")
    checker.write_text(FAKE_CHECKER.format(label="worktree", code=worktree_code), encoding="utf-8")
    head = git(repo, "rev-parse", "HEAD")

    proc, runs = _commit(repo, tmp_path)

    assert [r["label"] for r in runs] == ["staged"], proc.stderr
    assert runs[0]["args"] == ["commit", "--fast"]
    assert (
        Path(runs[0]["cwd"]).resolve() == repo.resolve()
    )  # check.py --repo defaults to the working directory
    assert not Path(runs[0]["path"]).exists(), "the hook leaves its copy of the staged checker behind"
    assert list((tmp_path / "tmp").iterdir()) == []
    assert (proc.returncode == 0) is committed, proc.stderr
    assert (git(repo, "rev-parse", "HEAD") != head) is committed


def test_pre_commit_hook_runs_the_staged_checker_when_only_other_files_change(tmp_path: Path) -> None:
    repo = _hook_repo(tmp_path)
    (repo / "scripts" / "check.py").write_text(
        FAKE_CHECKER.format(label="worktree", code=1), encoding="utf-8"
    )
    (repo / "notes.txt").write_text("FAKE change\n", encoding="utf-8")
    git(repo, "add", "notes.txt")

    proc, runs = _commit(repo, tmp_path)

    assert [r["label"] for r in runs] == ["committed"], proc.stderr
    assert proc.returncode == 0, proc.stderr


def test_pre_commit_hook_stops_when_the_checker_is_not_staged(tmp_path: Path) -> None:
    repo = _hook_repo(tmp_path)
    git(repo, "rm", "-q", "--cached", "scripts/check.py")

    proc, runs = _commit(repo, tmp_path)

    assert runs == []
    assert proc.returncode != 0
    assert "scripts/check.py is not staged" in proc.stderr
