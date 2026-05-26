.PHONY: help install lint typecheck test test-unit test-integration migrate migrate-check migrate-down clean

PYTHON   ?= python3
PYTEST   ?= $(PYTHON) -m pytest
ALEMBIC  ?= $(PYTHON) -m alembic

# -- Default target ------------------------------------------------------------
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# -- Dependencies --------------------------------------------------------------
install:  ## Install dev dependencies
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt

# -- Code quality --------------------------------------------------------------
# R-25: was `flake8` — flake8 is not in requirements-dev.txt or CI; replaced with ruff check.
lint:  ## Run ruff linter
	$(PYTHON) -m ruff check src/ api/ observability/ pipelines/

# R-25: expanded path list to match CI Bandit scope (observability/ pipelines/ were missing).
typecheck:  ## Run mypy type checker
	$(PYTHON) -m mypy src/ api/ observability/ pipelines/

# -- Tests ---------------------------------------------------------------------
test:  ## Run full test suite with coverage
	$(PYTEST) tests/ -v --tb=short \
	  --cov=src --cov=api --cov=observability --cov=pipelines \
	  --cov-report=term-missing \
	  --cov-fail-under=68

# R-25: dropped -m unit marker — most unit tests are not decorated @pytest.mark.unit;
#       the marker caused silent skips. Run by path instead.
test-unit:  ## Run unit tests only (no DB required)
	$(PYTEST) tests/unit/ \
	  tests/test_incident_service.py \
	  tests/test_incident_schema.py \
	  tests/test_key_store.py \
	  tests/test_incident_tracker.py \
	  -v --tb=short

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

migrate-generate:  ## Autogenerate new migration (MSG required: make migrate-generate MSG="add team_id")
	$(ALEMBIC) revision --autogenerate -m "$(MSG)"

# -- Cleanup -------------------------------------------------------------------
clean:  ## Remove .pyc files and __pycache__ dirs
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.mypy_cache' -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage coverage.xml
