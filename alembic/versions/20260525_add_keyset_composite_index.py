"""
Add ix_incidents_keyset composite index.

Revision ID:  20260525_keyset
Revises:      0001
Create Date:  2026-05-25

Rationale (KEYSET-01):
  The compound keyset cursor predicate in IncidentRepository._keyset_cursor_clause()
  uses the form:

      WHERE (created_at < :cursor_created_at)
         OR (created_at = :cursor_created_at AND id < :cursor_id)

  Without a composite index covering (created_at, id), PostgreSQL must perform
  a full sequential scan for every paginated list request once the table grows
  beyond a few thousand rows.  A btree index on (created_at DESC, id DESC)
  matches the ORDER BY clause exactly and allows the planner to use an
  index-only scan for the compound WHERE predicate.

  Column order and sort direction matter:
    - The ORDER BY in list_open() / list_by_severity() is:
        ORDER BY created_at DESC, id DESC
    - Using matching DESC directions on both index columns means PostgreSQL
      can scan the index forward without a sort step, and the compound WHERE
      predicate (created_at, id) is covered by the first two index columns.
    - On SQLite (local / test): the postgresql_using kwarg and
      postgresql_ops overrides are silently ignored. SQLite still creates
      a plain composite index on (created_at, id), which is sufficient for
      correctness in test environments.

Down migration:
  Drops ix_incidents_keyset. Safe to re-apply via `alembic upgrade head`.

How to run:
    alembic upgrade head          # applies 0001 then 20260525_keyset
    alembic downgrade -1          # rolls back keyset index only
    alembic downgrade base        # rolls back everything (test/dev only)
"""

from alembic import op


# ── Alembic metadata ────────────────────────────────────────────────────────
revision = "20260525_keyset"
down_revision = "0001"   # wires to initial schema migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create a covering btree index on (created_at DESC, id DESC).

    The postgresql_ops override sets per-column sort directions for the
    PostgreSQL planner. SQLite ignores this kwarg entirely.
    """
    op.create_index(
        "ix_incidents_keyset",
        "incidents",
        ["created_at", "id"],
        unique=False,
        postgresql_using="btree",
        postgresql_ops={
            "created_at": "DESC",
            "id": "DESC",
        },
    )


def downgrade() -> None:
    op.drop_index("ix_incidents_keyset", table_name="incidents")
