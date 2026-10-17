PY := backend/.venv/bin/python

setup:
	python3.11 -m venv backend/.venv
	$(PY) -m pip install -q -r backend/requirements.txt -r backend/requirements-dev.txt

test:
	cd backend && .venv/bin/python -m pytest -q

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy argus_api

# Firebase Local Emulator Suite: Auth (9099) + Firestore (8081) with firestore.rules enforced.
up:
	docker compose up -d --build firebase

down:
	docker compose down --remove-orphans

# Firebase Auth + Firestore rules tests; need `make up` first. Hosts can be overridden for another port.
FIRESTORE_EMULATOR_HOST ?= localhost:8081
FIREBASE_AUTH_EMULATOR_HOST ?= localhost:9099
test-emulator:
	cd backend && FIRESTORE_EMULATOR_HOST=$(FIRESTORE_EMULATOR_HOST) \
		FIREBASE_AUTH_EMULATOR_HOST=$(FIREBASE_AUTH_EMULATOR_HOST) .venv/bin/python -m pytest -q tests_emulator

run:
	cd backend && set -a && . ./.env && set +a && .venv/bin/uvicorn argus_api.main:app --reload --port 8080

models:
	cd backend && set -a && . ./.env && set +a && .venv/bin/python -c "from google import genai; import os; \
	[print(m.name) for m in genai.Client(api_key=os.environ['GEMINI_API_KEY']).models.list() if 'gemini' in m.name]"

seed:
	cd backend && set -a && . ./.env && set +a && .venv/bin/python -m argus_api.seed_fake_run

# Needs a Firebase ID token: TOKEN=<idToken from the Auth emulator or the web app> make smoke
smoke:
	curl -s -X POST -H "Authorization: Bearer $(TOKEN)" localhost:8080/api/smoke; echo

# RBI library: fetch rbi.org.in pages and parse them into backend/data/corpus (OFFLINE=1 re-parses the cache).
corpus:
	cd backend && .venv/bin/python -m argus_api.corpus.build_corpus $(if $(OFFLINE),--offline)

# The corpus hash of backend/data/corpus, as GET /health reports it (DIR=<corpus dir> for another library).
corpus-hash:
	$(PY) scripts/corpus_hash.py $(DIR)

# Write backend/data/corpus to Firestore (rbi_instruments, rbi_paragraphs, supersession).
load-corpus:
	cd backend && set -a && . ./.env && set +a && .venv/bin/python -m argus_api.admin load-corpus

# Kill switch and run caps in config/limits (no arguments: print):
#   make limits CAP=40 HOURLY_CAP=10 USER_CAP=3 ENABLED=true
limits:
	cd backend && set -a && . ./.env && set +a && .venv/bin/python -m argus_api.admin limits \
		$(if $(CAP),--cap $(CAP)) $(if $(HOURLY_CAP),--hourly-cap $(HOURLY_CAP)) \
		$(if $(USER_CAP),--user-cap $(USER_CAP)) $(if $(ENABLED),--enabled $(ENABLED))

# Copy a finished live run to the public samples/ collection: make save-sample RUN=<run_id>
save-sample:
	cd backend && set -a && . ./.env && set +a && .venv/bin/python -m argus_api.save_sample $(RUN)

# Automatic rule checks (scripts/check.py): lint, types, tests, pins, secrets, provenance, data, rules.
check:
	$(PY) scripts/check.py commit

# Git pre-commit hook: runs `scripts/check.py commit --fast` and stops the commit on any FAIL.
install-hooks:
	sh scripts/install-hooks.sh

.PHONY: setup test test-emulator lint up down run models seed smoke corpus corpus-hash load-corpus limits \
	save-sample check install-hooks
