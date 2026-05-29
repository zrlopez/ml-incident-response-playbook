"""alter_users_id_to_uuid_string

Revision ID: e959384f9f01
Revises: 20260525_add_keyset_composite_index
Create Date: 2026-05-27

Changes users.id from INTEGER (autoincrement) to VARCHAR(36) to match
the UserRecord model which generates UUID4 string IDs.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e959384f9f01"
down_revision: Union[str, Sequence[str], None] = "20260525_keyset"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop PK constraint, alter column type, re-add PK
    op.execute("ALTER TABLE users DROP CONSTRAINT users_pkey CASCADE")
    op.alter_column(
        "users",
        "id",
        existing_type=sa.Integer(),
        type_=sa.String(36),
        existing_nullable=False,
        postgresql_using="id::text",
    )
    op.execute("ALTER TABLE users ADD PRIMARY KEY (id)")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT users_pkey CASCADE")
    op.alter_column(
        "users",
        "id",
        existing_type=sa.String(36),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="id::integer",
    )
    op.execute("ALTER TABLE users ADD PRIMARY KEY (id)")
