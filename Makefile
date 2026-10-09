.PHONY: help install dev test lint fmt typecheck check seed run-api run-ui eval precommit clean

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

install: ## create venv and install all deps
	uv venv --python 3.12
	uv pip install -e ".[dev]"

check: lint typecheck test ## lint + typecheck + test

lint: ## ruff lint
	.venv/bin/ruff check src tests
	.venv/bin/ruff format --check src tests

fmt: ## ruff autofix + format
	.venv/bin/ruff check --fix src tests
	.venv/bin/ruff format src tests

typecheck: ## mypy strict
	.venv/bin/mypy

test: ## run tests, excluding live API tests
	.venv/bin/pytest -m "not live"

test-live: ## run tests including live OpenRouter calls (costs money)
	.venv/bin/pytest -m "live"

cov: ## tests with coverage
	.venv/bin/pytest -m "not live" --cov=forensics --cov-report=term-missing

dev: ## run API with reload
	.venv/bin/uvicorn forensics.api:app --reload --host $${HOST:-127.0.0.1} --port $${PORT:-8000}

run-api: ## alias for dev
	@$(MAKE) dev

run-ui: ## frontend dev server (Phase 4; not scaffolded yet)
	cd frontend && npm run dev

seed: ## generate the 50-document dataset with ground-truth labels
	.venv/bin/python -m forensics.cli seed

eval: ## re-run the accumulated eval dataset
	.venv/bin/python -m forensics.cli eval

precommit: ## install git hooks
	.venv/bin/pre-commit install

clean: ## remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +