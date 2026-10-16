PY := backend/.venv/bin/python

setup:
	python3.11 -m venv backend/.venv
	$(PY) -m pip install -q -r backend/requirements.txt -r backend/requirements-dev.txt

test:
	cd backend && .venv/bin/python -m pytest -q

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy argus_api

run:
	cd backend && .venv/bin/uvicorn argus_api.main:app --reload --port 8080

.PHONY: setup test lint run
