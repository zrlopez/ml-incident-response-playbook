#!/usr/bin/env bash
# scripts/bootstrap_db.sh
#
# Bootstraps the database for a fresh deployment:
#   1. Waits for PostgreSQL to be reachable
#   2. Runs Alembic migrations to head
#   3. Verifies migration state
#   4. Optionally seeds a first admin user (SEED_ADMIN=1)
#
# Environment variables:
#   DATABASE_URL     — required; must start with postgresql
#   SEED_ADMIN       — optional; set to 1 to create the first admin user
#   ADMIN_USERNAME   — required when SEED_ADMIN=1 (default: admin)
#   ADMIN_EMAIL      — required when SEED_ADMIN=1
#   ADMIN_PASSWORD   — required when SEED_ADMIN=1
#   MAX_WAIT         — seconds to wait for DB (default: 60)
#
# Usage:
#   DATABASE_URL=postgresql+asyncpg://... ./scripts/bootstrap_db.sh
#   SEED_ADMIN=1 ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=secret \
#     DATABASE_URL=postgresql+asyncpg://... ./scripts/bootstrap_db.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# ── Colour output ────────────────────────────────────────────────────────────
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

step()  { echo -e "${BLUE}[STEP]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*" >&2; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }

# ── Validate environment ──────────────────────────────────────────────────────
if [[ -z "${DATABASE_URL:-}" ]]; then
  error "DATABASE_URL is not set."
  error "Example: export DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname"
  exit 1
fi

if [[ "${DATABASE_URL}" != postgresql* ]]; then
  error "DATABASE_URL must start with 'postgresql'. Got: ${DATABASE_URL%%:*}"
  error "SQLite does not require bootstrap — Alembic runs automatically at startup."
  exit 1
fi

MAX_WAIT="${MAX_WAIT:-60}"
SEED_ADMIN="${SEED_ADMIN:-0}"

# ── Wait for PostgreSQL ───────────────────────────────────────────────────────
step "Waiting for PostgreSQL to become reachable (max ${MAX_WAIT}s)..."

# Extract host and port from DATABASE_URL for pg_isready
# Handles: postgresql+asyncpg://user:pass@host:5432/db
DB_HOST=$(echo "${DATABASE_URL}" | python3 -c "
import sys, urllib.parse
url = sys.stdin.read().strip().replace('postgresql+asyncpg://', 'postgresql://')
p = urllib.parse.urlparse(url)
print(p.hostname or 'localhost')
")
DB_PORT=$(echo "${DATABASE_URL}" | python3 -c "
import sys, urllib.parse
url = sys.stdin.read().strip().replace('postgresql+asyncpg://', 'postgresql://')
p = urllib.parse.urlparse(url)
print(p.port or 5432)
")
DB_USER=$(echo "${DATABASE_URL}" | python3 -c "
import sys, urllib.parse
url = sys.stdin.read().strip().replace('postgresql+asyncpg://', 'postgresql://')
p = urllib.parse.urlparse(url)
print(p.username or '')
")

ELAPSED=0
until pg_isready -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -q 2>/dev/null; do
  if [[ ${ELAPSED} -ge ${MAX_WAIT} ]]; then
    error "PostgreSQL did not become ready within ${MAX_WAIT}s."
    error "Host: ${DB_HOST}:${DB_PORT}  User: ${DB_USER}"
    exit 1
  fi
  echo -n "."
  sleep 2
  ELAPSED=$((ELAPSED + 2))
done
echo ""
ok "PostgreSQL is ready at ${DB_HOST}:${DB_PORT}"

# ── Run Alembic migrations ────────────────────────────────────────────────────
step "Running Alembic migrations..."
alembic upgrade head
ok "Migrations applied."

step "Verifying current migration state..."
alembic current

# ── Optional: seed first admin user ──────────────────────────────────────────
if [[ "${SEED_ADMIN}" == "1" ]]; then
  echo ""
  step "Seeding first admin user..."

  ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
  ADMIN_EMAIL="${ADMIN_EMAIL:-}"
  ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

  if [[ -z "${ADMIN_EMAIL}" ]]; then
    error "ADMIN_EMAIL is required when SEED_ADMIN=1"
    exit 1
  fi
  if [[ -z "${ADMIN_PASSWORD}" ]]; then
    error "ADMIN_PASSWORD is required when SEED_ADMIN=1"
    exit 1
  fi

  python3 scripts/seed_users.py \
    --username "${ADMIN_USERNAME}" \
    --email    "${ADMIN_EMAIL}" \
    --password "${ADMIN_PASSWORD}" \
    --role     admin

  ok "Admin user '${ADMIN_USERNAME}' created."
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
ok "Database bootstrap complete."

if [[ "${SEED_ADMIN}" != "1" ]]; then
  warn "No admin user seeded. Re-run with SEED_ADMIN=1 to create one:"
  warn "  SEED_ADMIN=1 ADMIN_EMAIL=you@example.com ADMIN_PASSWORD=... ./scripts/bootstrap_db.sh"
fi
