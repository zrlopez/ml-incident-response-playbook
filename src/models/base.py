"""
src/models/base.py
==================
Shared SQLAlchemy DeclarativeBase for all ORM models.

All ORM models must inherit from Base defined here so Alembic autogenerate
sees the full schema in one metadata object.
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
