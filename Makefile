.PHONY: help install lint typecheck test test-unit test-integration migrate migrate-check migrate-down clean

PYTHON   ?= python3
PYTEST   ?= $(PYTHON) -m pytest
ALEMBIC  ?= $(PYTHON) -m alembic

# ── Default target ─────────────────────────────────────────────────────────────
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Dependencies ───────────────────────────────────────────────────────────────
install:  ## Install dev dependencies
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt

# ── Code quality ───────────────────────────────────────────────────────────────
lint:  ## Run flake8 linter
	$(PYTHON) -m flake8 src/ api/ observability/ --max-line-length 100

typecheck:  ## Run mypy type checker
	$(PYTHON) -m mypy src/ api/ --ignore-missing-imports

# ── Tests ─────────────────────────────────────────────────────────────────────
test:  ## Run full test suite with coverage
	$(PYTEST) tests/ -v --tb=short \
	  --cov=src --cov=api --cov=observability \
	  --cov-report=term-missing --cov-fail-under=85

test-unit:  ## Run unit tests only (no DB required)
	$(PYTEST) tests/unit/ -v --tb=short -m unit

test-integration:  ## Run integration tests (requires DATABASE_URL)
	$(PYTEST) tests/integration/ -v --tb=short -m integration

test-fast:  ## Run unit tests fast (no coverage)
	$(PYTEST) tests/unit/ -q --tb=short

# ── Database / Alembic ─────────────────────────────────────────────────────────
migrate:  ## Apply all pending migrations (alembic upgrade head)
	$(ALEMBIC) upgrade head

migrate-check:  ## Show current migration revision
	$(ALEMBIC) current

migrate-history:  ## Show migration history
	$(ALEMBIC) history --verbose

migrate-down:  ## Roll back one migration step
	$(ALEMBIC) downgrade -1

migrate-generate:  ## Autogenerate new migration (MSG required: make migrate-generate MSG="add team_id")
	$(ALEMBIC) revision --autogenerate -m "$(MSG)"

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:  ## Remove .pyc files and __pycache__ dirs
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.mypy_cache' -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage coverage.xml
