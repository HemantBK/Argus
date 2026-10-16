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

.PHONY: setup test test-emulator lint up down run models seed smoke
