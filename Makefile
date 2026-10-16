PY := backend/.venv/bin/python

setup:
	python3.11 -m venv backend/.venv
	$(PY) -m pip install -q -r backend/requirements.txt -r backend/requirements-dev.txt

test:
	cd backend && .venv/bin/python -m pytest -q

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy argus_api

up:
	docker compose up -d firestore

down:
	docker compose down

run:
	cd backend && set -a && . ./.env && set +a && .venv/bin/uvicorn argus_api.main:app --reload --port 8080

models:
	cd backend && set -a && . ./.env && set +a && .venv/bin/python -c "from google import genai; import os; \
	[print(m.name) for m in genai.Client(api_key=os.environ['GEMINI_API_KEY']).models.list() if 'gemini' in m.name]"

smoke:
	curl -s -X POST localhost:8080/api/smoke; echo

.PHONY: setup test lint up down run models smoke
