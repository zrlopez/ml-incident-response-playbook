"""
0001_initial_schema.py — Initial schema: incidents + users tables
=================================================================
Revision: 0001
Create Date: 2026-05-23

Creates the two core tables for the ML Incident Response Platform:
  - incidents  (from src.incident_tracker.Incident)
  - users      (from src.users.repository.UserRecord)

This migration creates the schema from scratch. For greenfield deployments
run `alembic upgrade head` on an empty database.

For databases created by SQLAlchemy create_all() (pre-migration), use
the stamp command to mark this migration as applied without running it:
    alembic stamp 0001

Down migration drops both tables. Use with caution.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0001"
down_revision: str | None = None  # This is the root migration
branch_labels: str | tuple | None = None
depends_on: str | tuple | None = None


def upgrade() -> None:
    # ── incidents table ─────────────────────────────────────────────────────
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "severity",
            sa.Enum("SEV_1", "SEV_2", "SEV_3", "SEV_4", name="severitylevel"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "open", "investigating", "mitigating", "resolved", "closed",
                name="incidentstatus",
            ),
            nullable=False,
            server_default="open",
        ),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            onupdate=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    # Index for common query patterns
    op.create_index("ix_incidents_status", "incidents", ["status"])
    op.create_index("ix_incidents_severity", "incidents", ["severity"])
    op.create_index("ix_incidents_owner", "incidents", ["owner"])
    op.create_index("ix_incidents_created_at", "incidents", ["created_at"])

    # ── users table ─────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.Text(), nullable=False),
        sa.Column(
            "hash_algorithm",
            sa.String(20),
            nullable=False,
            server_default="bcrypt",
            comment="Track hashing algo for ARCH-02 rehash-on-login migration progress",
        ),
        sa.Column("role", sa.String(50), nullable=False, server_default="analyst"),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_hash_algorithm", "users", ["hash_algorithm"],
                    comment="Monitor ARCH-02 migration: SELECT hash_algorithm, COUNT(*) FROM users GROUP BY 1")


def downgrade() -> None:
    # Drop indexes first, then tables
    op.drop_index("ix_users_hash_algorithm", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_incidents_created_at", table_name="incidents")
    op.drop_index("ix_incidents_owner", table_name="incidents")
    op.drop_index("ix_incidents_severity", table_name="incidents")
    op.drop_index("ix_incidents_status", table_name="incidents")
    op.drop_table("incidents")

    # Drop enums (PostgreSQL only; SQLite ignores this)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="incidentstatus").drop(bind, checkfirst=True)
        sa.Enum(name="severitylevel").drop(bind, checkfirst=True)
