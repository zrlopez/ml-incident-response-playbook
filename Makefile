# =============================================================================
# Makefile — ML Incident Response Playbook
# =============================================================================
# Targets:
#   make install        Install production + dev dependencies
#   make lint           Run ruff + mypy
#   make test           Run unit tests (SQLite, fast)
#   make test-cov       Run unit tests with coverage report
#   make test-int       Run integration tests (requires Postgres + Redis)
#   make ci-local       Mirror full CI run locally (CI-65)
#   make audit          Run pip-audit security scan
#   make pre-commit     Run all pre-commit hooks
#   make docs           Serve MkDocs locally
#   make clean          Remove build/cache artifacts
# =============================================================================

.PHONY: install lint test test-cov test-int ci-local audit pre-commit docs clean

PYTHON ?= python3
PIP    ?= pip

# ── Dependencies ──────────────────────────────────────────────────────────────
install:
	$(PIP) install -r requirements-dev.txt

# ── Lint ──────────────────────────────────────────────────────────────────────
lint:
	ruff check api/ observability/ src/ tests/
	mypy api/ observability/ src/

# ── Unit tests ────────────────────────────────────────────────────────────────
test:
	pytest tests/unit/ -n auto -v

test-cov:
	pytest tests/unit/ \
		--cov=src --cov=api --cov=observability --cov=pipelines \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		--cov-fail-under=75 \
		-n auto -v

# ── Integration tests (requires live Postgres + Redis) ────────────────────────
test-int:
	pytest tests/integration/ \
		--cov=api --cov=observability --cov=src \
		--cov-report=term-missing \
		--cov-fail-under=65 \
		-v

# ── ci-local: mirrors the full CI pipeline locally (CI-65) ───────────────────
# Runs in the same order as secured_ci.yml:
#   1. TruffleHog secret scan (skipped locally — requires git history access)
#   2. pip-audit dependency audit
#   3. Bandit SAST (api/ + observability/ + src/)
#   4. mypy type checking
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
	@echo "[3/6] mypy — type checking"
	mypy api/ observability/ src/
	@echo ""
	@echo "[4/6] ruff — linting"
	ruff check api/ observability/ src/ tests/
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
		--cov-fail-under=65 \
		-v
	@echo ""
	@echo "════════════════════════════════════════"
	@echo "  CI-LOCAL PASSED — all gates green"
	@echo "════════════════════════════════════════"

# ── Security audit ────────────────────────────────────────────────────────────
audit:
	pip-audit --requirement requirements.txt

# ── Pre-commit ────────────────────────────────────────────────────────────────
pre-commit:
	pre-commit run --all-files

# ── Docs ──────────────────────────────────────────────────────────────────────
docs:
	mkdocs serve

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	find . -name 'coverage*.xml' -delete 2>/dev/null || true
	@echo "Clean complete."
