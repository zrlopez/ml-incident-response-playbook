# =============================================================================
# Makefile — ML Incident Response Playbook
# =============================================================================
# Targets:
#   make install        Install production + dev dependencies
#   make deps-compile   Regenerate requirements.txt from pyproject.toml
#   make lint           Run ruff (check) + mypy across all source trees
#   make fmt            Run ruff format + ruff --fix (auto-fix)
#   make test           Run unit tests (SQLite, fast, parallel)
#   make test-cov       Run unit tests with HTML + XML coverage report
#   make test-int       Run integration tests (requires Postgres + Redis)
#   make ci-local       Mirror full CI run locally (CI-65)
#   make audit          Run pip-audit security scan
#   make pre-commit     Run all pre-commit hooks against all files
#   make docs           Serve MkDocs locally
#   make clean          Remove build/cache artifacts
# =============================================================================

.PHONY: install deps-compile lint fmt test test-cov test-int ci-local \
        audit pre-commit docs clean

PYTHON ?= python3
PIP    ?= pip

# ── Dependencies ──────────────────────────────────────────────────────────────────
install:
	$(PIP) install -r requirements-dev.txt

deps-compile:  ## Regenerate requirements.txt from pyproject.toml
	$(PYTHON) -m pip install --quiet pip-tools
	pip-compile pyproject.toml \
	  --output-file requirements.txt \
	  --no-emit-trusted-host \
	  --no-header \
	  --no-annotate \
	  --quiet
	@echo "requirements.txt updated. Review changes and commit."

# ── Lint + type check (all source trees) ──────────────────────────────────────
lint:  ## Run ruff linter + mypy (api/ observability/ pipelines/ src/ tests/)
	# R-P2 (Cycle 1): single target; pipelines/ added to both ruff and mypy.
	# Previously two duplicate lint: targets existed; the second (which lacked
	# pipelines/) silently shadowed the first under GNU Make semantics.
	ruff check api/ observability/ pipelines/ src/ tests/
	mypy api/ observability/ pipelines/ src/

fmt:  ## Auto-fix formatting with ruff
	ruff format api/ observability/ pipelines/ src/ tests/
	ruff check --fix api/ observability/ pipelines/ src/ tests/

# ── Unit tests ───────────────────────────────────────────────────────────────────
test:
	pytest tests/unit/ -n auto -v

test-cov:
	pytest tests/unit/ \
		--cov=src --cov=api --cov=observability --cov=pipelines \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		--cov-fail-under=75 \
		-n auto -v

# ── Integration tests (requires live Postgres + Redis) ───────────────────────────
test-int:
	# CI-67 COMPLETE: integration inference tests added (test_inference_integration.py,
	# 10 tests, IT-INF-01..10). Gate raised 53% -> 65% as planned.
	# See docs/REMEDIATION_LOG.md Phase 12 (ML-09).
	pytest tests/integration/ \
		--cov=api --cov=observability --cov=src \
		--cov-report=term-missing \
		--cov-fail-under=65 \
		-v

# ── ci-local: mirrors the full CI pipeline locally (CI-65) ─────────────────────────
# Runs in the same order as secured_ci.yml:
#   1. pip-audit dependency audit
#   2. Bandit SAST (api/ + observability/ + src/)
#   3. mypy type checking (all source trees including pipelines/)
#   4. ruff linting (all source trees including pipelines/)
#   5. Unit tests (SQLite, parallel)
#   6. Integration tests (requires Postgres + Redis via docker-compose)
# Usage: make ci-local
# Note: Set DATABASE_URL, REDIS_URL, JWT_SECRET_KEY in your .env before running.
ci-local:
	@echo "════════════════════════════════════════"
	@echo "  CI-LOCAL: Full CI mirror (CI-65)"
	@echo "════════════════════════════════════════"
	@echo ""
	@echo "[1/6] pip-audit — dependency security scan"
	$(PIP) install pip-audit --quiet
	pip-audit --requirement requirements.txt
	@echo ""
	@echo "[2/6] Bandit — SAST hard gate"
	bandit -r api/ observability/ src/ --severity-level medium --confidence-level medium
	@echo ""
	@echo "[3/6] mypy — type checking (all source trees including pipelines/)"
	mypy api/ observability/ pipelines/ src/
	@echo ""
	@echo "[4/6] ruff — linting (all source trees including pipelines/)"
	ruff check api/ observability/ pipelines/ src/ tests/
	@echo ""
	@echo "[5/6] Unit tests — SQLite, parallel (-n auto)"
	pytest tests/unit/ \
		--cov=src --cov=api --cov=observability --cov=pipelines \
		--cov-report=term-missing \
		--cov-fail-under=75 \
		-n auto -v
	@echo ""
	@echo "[6/6] Integration tests — requires Postgres + Redis"
	@echo "      Tip: run 'docker-compose up -d postgres redis' first."
	pytest tests/integration/ \
		--cov=api --cov=observability --cov=src \
		--cov-report=term-missing \
		--cov-fail-under=53 \
		-v
	@echo ""
	@echo "════════════════════════════════════════"
	@echo "  CI-LOCAL PASSED — all gates green"
	@echo "════════════════════════════════════════"

# ── Security audit ────────────────────────────────────────────────────────────────────
audit:
	pip-audit --requirement requirements.txt

# ── Pre-commit ─────────────────────────────────────────────────────────────────────────
pre-commit:
	pre-commit run --all-files

# ── Docs ──────────────────────────────────────────────────────────────────────────────
docs:
	mkdocs serve

# ── Clean ──────────────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	find . -name 'coverage*.xml' -delete 2>/dev/null || true
	@echo "Clean complete."
