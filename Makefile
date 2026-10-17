.PHONY: help install dev test lint fmt typecheck check seed run-api run-ui run-web build-ui eval precommit clean

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

install: ## create venv and install all deps
	uv venv --python 3.12
	uv pip install -e ".[dev,otel]"
	cd frontend && npm install

check: lint typecheck test ## lint + typecheck + test

lint: ## ruff lint (src, tests, and the root-level app)
	.venv/bin/ruff check src tests streamlit_app.py
	.venv/bin/ruff format --check src tests streamlit_app.py

fmt: ## ruff autofix + format
	.venv/bin/ruff check --fix src tests streamlit_app.py
	.venv/bin/ruff format src tests streamlit_app.py

typecheck: ## mypy strict
	.venv/bin/mypy

test: ## run tests, excluding live API tests
	.venv/bin/pytest -m "not live"

test-live: ## run tests including live NVIDIA NIM calls (uses hosted quota)
	.venv/bin/pytest -m "live"

cov: ## tests with coverage
	.venv/bin/pytest -m "not live" --cov=forensics --cov-report=term-missing

build-ui: ## build React frontend production bundle
	cd frontend && npm run build

dev: ## run API with reload (serves frontend/dist at root)
	.venv/bin/uvicorn forensics.api:app --reload --host $${HOST:-127.0.0.1} --port $${PORT:-8000}

run-api: ## alias for dev
	@$(MAKE) dev

run-web: ## build and serve React frontend via FastAPI
	@$(MAKE) build-ui
	@$(MAKE) dev

run-ui: ## launch the Streamlit trace explorer
	.venv/bin/streamlit run streamlit_app.py

seed: ## dataset generation is not implemented; the dataset ships with the repo
	@echo "Dataset generation is not implemented; the labeled dataset is checked in."
	@.venv/bin/python -m forensics.cli validate-dataset

eval: ## re-run the accumulated eval dataset
	@.venv/bin/python -m forensics.cli eval

precommit: ## install git hooks
	.venv/bin/pre-commit install

clean: ## remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
