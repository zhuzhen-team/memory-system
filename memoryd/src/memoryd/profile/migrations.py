"""SQLite schema for the profile self-learning module.

The actual DDL lives in
``memoryd/migrations/005_profile_self_learning.sql`` and is applied by the
global migration runner in :func:`memoryd.index.open_index`. This module
exposes the SQL as a module-level constant and a helper so callers can
idempotently create the tables when they're handed a raw ``sqlite3``
connection that has *not* been opened via ``open_index`` (e.g. tests that
build their own in-memory DB).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


_SQL_PATH = (
    Path(__file__).parent.parent / "migrations" / "005_profile_self_learning.sql"
)


def profile_schema_sql() -> str:
    """Return the bundled SQL DDL for the profile tables."""
    return _SQL_PATH.read_text(encoding="utf-8")


def ensure_profile_schema(conn: sqlite3.Connection) -> None:
    """Create the profile tables if they do not already exist.

    Safe to call repeatedly; idempotent because the DDL uses
    ``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS``.
    Most production callers won't need this — ``open_index`` runs the
    bundled migration automatically — but tests that build their own
    connection can call it directly.
    """
    conn.executescript(profile_schema_sql())
    conn.commit()
