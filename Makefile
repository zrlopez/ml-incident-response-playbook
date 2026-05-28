.PHONY: help install lint typecheck format security audit pre-commit \
        test test-unit test-integration test-fast \
        migrate migrate-check migrate-history migrate-down migrate-generate \
        docker/build docker/up docker/up-prod docker/down docker/logs docker/shell docker/clean-volumes \
        docs docs-serve \
        clean

PYTHON   ?= python3
PYTEST   ?= $(PYTHON) -m pytest
ALEMBIC  ?= $(PYTHON) -m alembic
COMPOSE  ?= docker compose

# -- Default target ------------------------------------------------------------
help:  ## Show this help
	@grep -E '^[a-zA-Z_/-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2}'

# -- Dependencies --------------------------------------------------------------
install:  ## Install dev dependencies
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt
	pre-commit install

# -- Code quality --------------------------------------------------------------
lint:  ## Run ruff linter (check only)
	$(PYTHON) -m ruff check src/ api/ observability/ pipelines/

format:  ## Run ruff formatter (in-place)
	$(PYTHON) -m ruff format src/ api/ observability/ pipelines/ tests/

typecheck:  ## Run mypy type checker
	$(PYTHON) -m mypy src/ api/ observability/ pipelines/

# -- Security & Dependency Audit -----------------------------------------------
security:  ## Run Bandit SAST against application code
	$(PYTHON) -m bandit -r src/ api/ observability/ \
	  --severity-level medium --confidence-level medium

audit:  ## Run pip-audit against production dependencies
	$(PYTHON) -m pip_audit --requirement requirements.txt

pre-commit:  ## Run all pre-commit hooks against every file
	pre-commit run --all-files

# -- Tests ---------------------------------------------------------------------
test:  ## Run full test suite with coverage (aligned to CI gate: fail_under=68)
	$(PYTEST) tests/ -v --tb=short \
	  --cov=src --cov=api --cov=observability --cov=pipelines \
	  --cov-report=term-missing \
	  --cov-fail-under=68

test-unit:  ## Run unit tests only (no DB required)
	$(PYTEST) tests/unit/ -v --tb=short

test-integration:  ## Run integration tests (requires DATABASE_URL)
	$(PYTEST) tests/integration/ -v --tb=short -m integration

test-fast:  ## Run unit tests fast (no coverage)
	$(PYTEST) tests/unit/ -q --tb=short

# -- Database / Alembic --------------------------------------------------------
migrate:  ## Apply all pending migrations (alembic upgrade head)
	$(ALEMBIC) upgrade head

migrate-check:  ## Show current migration revision
	$(ALEMBIC) current

migrate-history:  ## Show migration history
	$(ALEMBIC) history --verbose

migrate-down:  ## Roll back one migration step
	$(ALEMBIC) downgrade -1

migrate-generate:  ## Autogenerate a new migration (MSG= required)
	$(ALEMBIC) revision --autogenerate -m "$(MSG)"

# -- Docker --------------------------------------------------------------------
docker/build:  ## Build the API image (no cache)
	$(COMPOSE) build --no-cache api

docker/up:  ## Start full dev stack (auto-merges docker-compose.override.yml)
	$(COMPOSE) up --build

docker/up-prod:  ## Start stack WITHOUT override (no --reload, production CMD)
	$(COMPOSE) -f docker-compose.yml up --build

docker/down:  ## Stop and remove containers (keeps volumes)
	$(COMPOSE) down

docker/logs:  ## Follow logs for the api service
	$(COMPOSE) logs -f api

docker/shell:  ## Open a shell in the running api container
	$(COMPOSE) exec api /bin/sh

docker/clean-volumes:  ## WARNING: destroy all compose volumes (redis data etc.)
	$(COMPOSE) down -v

# -- Documentation -------------------------------------------------------------
docs:  ## Build MkDocs Material documentation site
	$(PYTHON) -m mkdocs build --strict

docs-serve:  ## Serve documentation locally at http://127.0.0.1:8001
	$(PYTHON) -m mkdocs serve --dev-addr 127.0.0.1:8001

# -- Cleanup -------------------------------------------------------------------
clean:  ## Remove .pyc files, __pycache__, test/coverage artefacts
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.mypy_cache'   -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage coverage.xml coverage-unit.xml coverage-integration.xml
