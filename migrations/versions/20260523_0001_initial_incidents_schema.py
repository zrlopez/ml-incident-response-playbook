"""Initial incidents schema.

Revision ID: 20260523_0001
Revises: 
Create Date: 2026-05-23 04:30:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260523_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "severity",
            # Values MUST match SeverityLevel.value in src/domain/incident_lifecycle.py
            # SAEnum uses .value for native DB enums; SeverityLevel values are "SEV-1".."SEV-4"
            sa.Enum("SEV-1", "SEV-2", "SEV-3", "SEV-4", name="severity_level"),
            nullable=False,
            server_default="SEV-3",
        ),
        sa.Column(
            "status",
            # Values MUST match IncidentStatus.value in src/domain/incident_lifecycle.py
            # IncidentStatus values are lowercase strings (open, investigating, etc.)
            sa.Enum(
                "open",
                "investigating",
                "mitigating",
                "resolved",
                "closed",
                name="incident_status",
            ),
            nullable=False,
            server_default="open",
        ),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_incidents_status", "incidents", ["status"], unique=False)
    op.create_index("ix_incidents_severity", "incidents", ["severity"], unique=False)
    op.create_index("ix_incidents_created_at", "incidents", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_incidents_created_at", table_name="incidents")
    op.drop_index("ix_incidents_severity", table_name="incidents")
    op.drop_index("ix_incidents_status", table_name="incidents")
    op.drop_table("incidents")
    sa.Enum(name="incident_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="severity_level").drop(op.get_bind(), checkfirst=True)
