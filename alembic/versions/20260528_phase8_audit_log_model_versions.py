"""
Phase 8: incident_audit_log + model_versions tables

Revision ID: 20260528_phase8
Revises:     e959384f9f01
Create Date: 2026-05-28

Changes
-------
- CREATE TABLE incident_audit_log (OPEN-06)
- CREATE TABLE model_versions       (Phase 8 DB-backed registry)
- CREATE INDEX ix_audit_incident_id
- CREATE INDEX ix_audit_occurred_at
- CREATE INDEX ix_model_version_status
- CREATE TYPE audit_event_type   (Postgres; skipped for SQLite)
- CREATE TYPE model_version_status (Postgres; skipped for SQLite)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# ---------------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------------
revision: str = "20260528_phase8"
down_revision: str = "e959384f9f01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── audit_event_type enum ──────────────────────────────────────────────
    audit_event_type = sa.Enum(
        "status_transition",
        "metadata_update",
        "created",
        "quarantined",
        name="audit_event_type",
    )

    # ── model_version_status enum ──────────────────────────────────────────
    model_version_status = sa.Enum(
        "active",
        "inactive",
        "canary",
        "shadow",
        "quarantined",
        name="model_version_status",
    )

    # ── incident_audit_log ─────────────────────────────────────────────────
    op.create_table(
        "incident_audit_log",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "incident_id",
            sa.String(36),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", audit_event_type, nullable=False),
        sa.Column("old_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_incident_id", "incident_audit_log", ["incident_id"])
    op.create_index("ix_audit_occurred_at", "incident_audit_log", ["occurred_at"])

    # ── model_versions ─────────────────────────────────────────────────────
    op.create_table(
        "model_versions",
        sa.Column("version", sa.String(64), primary_key=True, nullable=False),
        sa.Column(
            "status",
            model_version_status,
            nullable=False,
            server_default="inactive",
        ),
        sa.Column("artifact_file", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("metrics_json", sa.Text, nullable=True),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_model_version_status", "model_versions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_model_version_status", table_name="model_versions")
    op.drop_table("model_versions")

    op.drop_index("ix_audit_occurred_at", table_name="incident_audit_log")
    op.drop_index("ix_audit_incident_id", table_name="incident_audit_log")
    op.drop_table("incident_audit_log")

    # Drop Postgres enum types (no-op on SQLite)
    sa.Enum(name="model_version_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="audit_event_type").drop(op.get_bind(), checkfirst=True)
