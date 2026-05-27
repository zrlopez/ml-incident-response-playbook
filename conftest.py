"""Root conftest.py — path hygiene before any test imports.

Removes .mypy_cache subdirectories from sys.path to prevent namespace package
shadowing (e.g. prometheus_client resolving as a namespace package instead of
the real installed package).
"""
from __future__ import annotations
import sys

sys.path = [
    p for p in sys.path
    if ".mypy_cache" not in p
]
