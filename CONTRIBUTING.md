# Contributing to ML Incident Response Playbook

Thank you for your interest in contributing. This document covers everything
you need to get a working development environment, understand the branching
and commit conventions, and get a pull request merged.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Local Setup](#local-setup)
3. [Environment Variables](#environment-variables)
4. [Branching Conventions](#branching-conventions)
5. [Commit Format](#commit-format)
6. [Pull Request Checklist](#pull-request-checklist)
7. [Pre-Commit Hooks](#pre-commit-hooks)
8. [CI Gates](#ci-gates)
9. [Code Style](#code-style)
10. [Running Tests](#running-tests)
11. [Architecture Notes](#architecture-notes)

---

## Prerequisites

| Tool | Minimum Version | Notes |
|------|----------------|-------|
| Python | 3.11 | Use `pyenv` or system package |
| Docker + Compose | 24.x / v2 plugin | Required for `make ci-local` |
| Git | 2.40+ | |
| `pre-commit` | 3.x | `pip install pre-commit` |
| `pip-tools` | 7.x | `pip install pip-tools` (for lockfile regen) |

---

## Local Setup

```bash
# 1. Clone and enter the repo
git clone https://github.com/zrlopez/ml-incident-response-playbook.git
cd ml-incident-response-playbook

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install all dependencies (runtime + dev)
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 4. Install pre-commit hooks
pre-commit install

# 5. Copy and populate environment variables
cp .env.example .env               # then edit .env — see section below

# 6. Start backing services (Postgres 16 + Redis 7)
docker compose up -d

# 7. Run database migrations
alembic upgrade head

# 8. Start the development server
uvicorn api.app:app --reload --port 8080
```

> **Tip:** `make help` lists all available Makefile targets.

---

## Environment Variables

Copy `.env.example` to `.env` and set the following:

| Variable | Required | Example | Notes |
|----------|----------|---------|-------|
| `DATABASE_URL` | Yes | `postgresql+asyncpg://user:pass@localhost:5432/incidents` | SQLite fallback: `sqlite+aiosqlite:///./incidents.db` |
| `REDIS_URL` | Yes | `redis://localhost:6379/0` | |
| `JWT_SECRET_KEY` | Yes | `change-me-in-production` | Min 32 chars in production |
| `JWT_ALGORITHM` | No | `HS256` | `RS256` if RS256 key pair present |
| `ENVIRONMENT` | No | `development` | `production` disables `/docs` |
| `ALLOWED_ORIGINS` | No | `http://localhost:3000` | Comma-separated CORS origins |

---

## Branching Conventions

```
main          — production-ready; protected; requires PR + CI green
fix/<id>      — bug fix targeting a tracker ID  (e.g. fix/R-P22)
feat/<id>     — new feature or roadmap item     (e.g. feat/R-P23)
chore/<topic> — maintenance, refactor, deps     (e.g. chore/deps-update)
docs/<topic>  — documentation only              (e.g. docs/runbooks)
```

Rules:
- Branch from `main`; open a PR back to `main`.
- Delete feature branches after merge.
- Do **not** force-push to `main` or any shared branch.

---

## Commit Format

This repo uses [Conventional Commits](https://www.conventionalcommits.org/).

```
<type>(<scope>): <short summary>

[optional body]

[optional footer: Closes #issue / R-Pxx FIXED]
```

**Types:** `feat` · `fix` · `docs` · `test` · `chore` · `refactor` · `perf` · `ci` · `revert`

**Scope examples:** `auth` · `tracker` · `ci` · `health` · `security` · `deps`

**Examples:**
```
feat(tracker): add keyset pagination to list_open() and list_by_severity()

fix(security): replace get_remote_address with _rate_limit_key() hash

R-P11 FIXED

test(tracker): add characterization tests for incident_tracker.py (R-P22)
```

**Rules:**
- Subject line ≤ 72 characters.
- Imperative mood in subject line ("add", not "added" or "adds").
- Reference the tracker ID (`R-Pxx`) in the footer when fixing a tracked item.
- Do not mix unrelated changes in a single commit.

---

## Pull Request Checklist

Before opening a PR, ensure every item is ✅:

- [ ] Branch is up to date with `main` (`git rebase main`)
- [ ] `pre-commit run --all-files` passes locally
- [ ] `pytest tests/unit/ -x -q` passes locally
- [ ] No new `mypy` errors (`make lint` passes)
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] `MASTER_ACTION_TRACKER.md` updated if a tracker item is closed
- [ ] Commit messages follow Conventional Commits format
- [ ] PR description explains *what* and *why* (not just *what*)

---

## Pre-Commit Hooks

Installed automatically via `pre-commit install`. Hooks run on every `git commit`:

| Hook | What it checks |
|------|----------------|
| `ruff` | Linting + import sort |
| `mypy` | Static type checking (strict) |
| `trailing-whitespace` | No trailing whitespace |
| `end-of-file-fixer` | Ensures files end with newline |
| `check-yaml` | YAML syntax |
| `check-toml` | TOML syntax |
| `pip-audit` | Known CVEs in requirements files |

Run manually at any time:
```bash
pre-commit run --all-files
```

---

## CI Gates

All PRs must pass the following CI jobs before merge:

| Job | Failure = Block merge? |
|-----|------------------------|
| `lint` (ruff + mypy) | ✅ Yes |
| `unit-tests` (≥75% coverage) | ✅ Yes |
| `integration-tests` | ✅ Yes |
| `security-scan` (Bandit + TruffleHog) | ✅ Yes |
| `dependency-audit` (pip-audit) | ✅ Yes |
| `lockfile-check` | ✅ Yes |
| `container-build` | ✅ Yes |
| `semgrep` | ✅ Yes (skipped on forks) |

> `semgrep` is skipped on fork PRs and Dependabot branches where
> `SEMGREP_APP_TOKEN` is unavailable. The gate remains hard for
> all owner-branch runs.

---

## Code Style

- **Python:** PEP 8 via `ruff`; max line length 100.
- **Type annotations:** Required on all public functions and methods.
  `mypy --strict` must pass.
- **Imports:** `from __future__ import annotations` at the top of every
  module; stdlib → third-party → local, separated by blank lines.
- **Docstrings:** Google style. Required on all public classes and non-trivial functions.
- **No bare `except`:** Always catch a specific exception class.
- **Secrets:** Never hardcode credentials. Use environment variables via `src.config.get_settings()`.

---

## Running Tests

```bash
# Unit tests only (fast; no external services needed)
pytest tests/unit/ -x -q

# Unit tests with coverage report
pytest tests/unit/ --cov=src --cov=api --cov-report=term-missing

# Integration tests (requires Docker services running)
docker compose up -d
pytest tests/integration/ -x -q

# Full local CI simulation
make ci-local

# Parallel execution (if pytest-xdist installed)
pytest tests/unit/ -n auto
```

---

## Architecture Notes

This repo follows a clean layered architecture:

```
HTTP (FastAPI)
  └── api/routers/          — route handlers; thin; delegate to services
  └── api/dependencies.py   — FastAPI Depends() wiring (auth, session)
  └── api/middleware.py      — security headers, tracing, body size limits

Service Layer
  └── src/services/         — orchestration logic; owns transactions

Domain Layer
  └── src/domain/           — enums, state machine, value objects
  └── src/incident_tracker.py — ORM model + repository (R-P23: refactor target)

Infrastructure
  └── src/auth/             — JWT sign/verify helpers
  └── src/config.py         — settings via pydantic-settings
  └── alembic/              — schema migrations (owns DB schema)
```

> `src/incident_tracker.py` is scheduled for decomposition in **R-P23**.
> Do not add new logic to that file; new behaviour belongs in `src/services/`
> or `src/repositories/`.

See [`docs/architecture.md`](docs/architecture.md) and the ADRs in
[`docs/adr/`](docs/adr/) for detailed design decisions.
