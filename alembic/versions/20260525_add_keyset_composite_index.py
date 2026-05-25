"""
Add ix_incidents_keyset composite index.

Revision ID: 20260525_keyset
Revises: (set to the current head revision before running)
Create Date: 2026-05-25

Rationale (KEYSET-01):
  The compound keyset cursor predicate introduced in IncidentRepository._keyset_cursor_clause()
  uses the form:

      WHERE (created_at < :cursor_created_at)
         OR (created_at = :cursor_created_at AND id < :cursor_id)

  Without a composite index covering (created_at, id), PostgreSQL must perform
  a full sequential scan for every paginated list request once the table grows
  beyond a few thousand rows.  A btree index on (created_at DESC, id DESC) matches
  the ORDER BY clause and allows the planner to use an index-only scan for the
  compound WHERE predicate.

  On SQLite (local/test): SQLite does not support CREATE INDEX ... USING btree;
  the postgresql_using kwarg is silently ignored by SQLAlchemy on non-PostgreSQL
  backends, so this migration is safe to run in all environments.

How to run:
    alembic upgrade 20260525_keyset
    # or simply:
    alembic upgrade head
"""

from alembic import op


# ── Alembic metadata ──────────────────────────────────────────────────────────────
revision = "20260525_keyset"
down_revision = None  # IMPORTANT: Set this to the actual current head before running.
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_incidents_keyset",
        "incidents",
        ["created_at", "id"],
        unique=False,
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("ix_incidents_keyset", table_name="incidents")
