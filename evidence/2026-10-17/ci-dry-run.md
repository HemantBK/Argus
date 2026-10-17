# Dry run of an offline CI workflow (17 Oct 2026)

Before adding a GitHub Actions workflow we ran its intended steps by hand on a clean checkout of commit
`7a4a5d1`, to see whether it would pass. **It would not, so no workflow was added.** This page records what
was run and what failed.

Intended workflow, offline only (no network calls beyond installing packages, no emulators, no cloud):

- Python 3.11: `ruff check`, `ruff format --check`, `mypy`, `pytest tests` (not `tests_emulator`).
- Web, Node 22.12: `npm ci`, `tsc --noEmit`, `eslint . --max-warnings 0`, `vitest run`.

## Setup

- A `git clone` of this repository, checked out at `7a4a5d1`, in a scratch directory. No untracked files.
- A fresh Python 3.11.15 venv with `pip install -r backend/requirements.txt -r backend/requirements-dev.txt`.
  (`requirements-dev.txt` alone holds only the tools; the tests also need the app's own packages.)
- Node from the official nodejs.org tarballs, checked against their published SHA-256 sums.

## Python: lint and types pass; tests fail without the RBI library

| Command (in `backend/` unless noted) | Result |
|---|---|
| `ruff check .` | pass |
| `ruff format --check .` | pass, 66 files already formatted |
| `mypy argus_api` | pass, no issues in 38 source files |
| `ruff check scripts`, `ruff format --check scripts`, `mypy --strict --python-version 3.11 scripts/check.py` (repo root) | pass |
| `python -m pytest -q -p no:cacheprovider tests` on the clean clone | **fail: 1 failed, 861 passed, 4 skipped, 19 errors** |
| the same, after copying a built library into `backend/data/corpus` (corpus hash `4ba88a4ca446...`, the one the live API reports) | pass: 882 passed, 3 skipped |

The 20 tests that fail or error all stop on `CorpusError: .../backend/data/corpus/instruments.json not
found`: 19 in `tests/test_cloud_e2e_offline.py` and `tests/test_health.py::test_corpus_hash_script_prints_what_health_reports`.
The library is deliberately not committed (`.gitignore`: it is rebuilt by `build_corpus.py`), and building it
(`make corpus`) fetches the RBI pages from rbi.org.in. So an offline CI cannot run these tests as the code
stands.

## Web: Node 22.12 cannot run the jsdom tests

| Node | `npm ci` | `tsc --noEmit` | `eslint . --max-warnings 0` | `vitest run` |
|---|---|---|---|---|
| 22.12.0 (npm 10.9.0) | exit 0, 18 "unsupported engine" warnings | pass | pass | **fail**: 7 test files cannot start, 244 tests in the other 23 files pass |
| 22.22.3 | exit 0, no warnings | pass | pass | pass: 30 files, 340 tests (with `backend/.venv` present) |
| 24.19.0 | exit 0, no warnings | pass | pass | pass: 30 files, 340 tests (with `backend/.venv` present) |

- On 22.12.0 the seven files that use jsdom fail to start with `TypeError: Cannot read properties of undefined
  (reading 'bind')` in `undici/lib/interceptor/decompress.js`. `npm ci` warns why: `undici@8.12.2` needs Node
  `>=22.19.0`, and `jsdom@30.1.1` and some of its dependencies need `^22.22.2 || ^24.15.0 || >=26.0.0`.
  `web/package.json` says `"node": ">=22.12.0"`, which is lower than its own dependencies need.
- `src/data/seedGuard.test.ts` runs `backend/.venv/bin/python`. Without that venv its 4 tests fail (seen on
  24.19.0: 336 passed, 4 failed); a workflow has to create the backend venv at that path first.

## What would make the workflow pass

1. Make the library available offline to the tests, for example as a committed fixture or a cached build step;
   that is a change to the test setup, not to this page.
2. Raise `web/package.json` `engines.node` to what the dependencies need (`^22.22.2 || >=24.15.0`) and run CI
   on such a version; Node 22.22.3 and 24.19.0 both passed here.
3. Create `backend/.venv` before `vitest run`.

Until then, the checks run locally and in the pre-commit hook (`scripts/install-hooks.sh`, which runs
`scripts/check.py commit --fast`).
