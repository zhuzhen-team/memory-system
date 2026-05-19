"""SQLite index module tests."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoryd.index import (
    DEFAULT_DB_PATH,
    Index,
    fingerprint_body,
    open_index,
)
from memoryd.schema import Frontmatter, SessionMemory


def _build_memory(slug: str = "2026-05-14-t", scope: str = "h", type_: str = "session") -> SessionMemory:
    return SessionMemory(
        frontmatter=Frontmatter(
            title="t",
            slug=slug,
            type=type_,
            scope_hash=scope,
            triggers=["k1", "k2"],
            source="manual",
            created_at=datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc),
        ),
        body="some body content",
    )


def test_open_index_creates_db_and_runs_migrations(tmp_path: Path):
    db = tmp_path / "x.db"
    idx = open_index(db)
    # tables exist
    cur = idx.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    assert "memories" in tables
    assert "triggers" in tables
    assert "promotions" in tables
    idx.close()


def test_index_memory_inserts_row_and_triggers(tmp_path: Path):
    idx = open_index(tmp_path / "x.db")
    mem = _build_memory()
    idx.index_memory(mem, body_path="scopes/h/sessions/2026-05-14-t.md")

    row = idx.conn.execute("SELECT slug, type, fingerprint FROM memories WHERE slug=?", ("2026-05-14-t",)).fetchone()
    assert row is not None
    assert row[0] == "2026-05-14-t"
    assert row[1] == "session"
    expected_fp = hashlib.sha1("some body content"[:500].encode()).hexdigest()
    assert row[2] == expected_fp

    triggers = idx.conn.execute("SELECT trigger FROM triggers WHERE slug=? ORDER BY trigger", ("2026-05-14-t",)).fetchall()
    assert [t[0] for t in triggers] == ["k1", "k2"]
    idx.close()


def test_index_memory_is_upsert(tmp_path: Path):
    """Re-indexing the same slug updates fields instead of duplicating."""
    idx = open_index(tmp_path / "x.db")
    mem1 = _build_memory()
    idx.index_memory(mem1, body_path="path1.md")

    mem2 = _build_memory()
    mem2 = mem2.model_copy(update={
        "frontmatter": mem2.frontmatter.model_copy(update={"title": "updated"})
    })
    idx.index_memory(mem2, body_path="path1.md")

    rows = idx.conn.execute("SELECT slug, title FROM memories WHERE slug=?", ("2026-05-14-t",)).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "updated"
    idx.close()


def test_get_memory_returns_row(tmp_path: Path):
    idx = open_index(tmp_path / "x.db")
    idx.index_memory(_build_memory(), body_path="p.md")
    row = idx.get_memory("2026-05-14-t")
    assert row is not None
    assert row["slug"] == "2026-05-14-t"
    assert row["type"] == "session"


def test_get_memory_returns_none_when_missing(tmp_path: Path):
    idx = open_index(tmp_path / "x.db")
    assert idx.get_memory("nope") is None


def test_list_by_type_filters_correctly(tmp_path: Path):
    idx = open_index(tmp_path / "x.db")
    idx.index_memory(_build_memory(slug="s1", type_="session"), body_path="s1.md")
    idx.index_memory(_build_memory(slug="d1", type_="decision"), body_path="d1.md")
    idx.index_memory(_build_memory(slug="d2", type_="decision"), body_path="d2.md")

    sessions = idx.list_by_type("session", scope_hash="h")
    decisions = idx.list_by_type("decision", scope_hash="h")
    assert len(sessions) == 1 and sessions[0]["slug"] == "s1"
    assert len(decisions) == 2


def test_list_by_type_filters_decay_state_by_default(tmp_path: Path):
    """Default include_decayed=False excludes soft-forgotten."""
    idx = open_index(tmp_path / "x.db")
    idx.index_memory(_build_memory(slug="alive1"), body_path="a.md")
    idx.index_memory(_build_memory(slug="forgotten1"), body_path="f.md")
    idx.conn.execute(
        "UPDATE memories SET decay_state='soft-forgotten' WHERE slug=?", ("forgotten1",)
    )
    idx.conn.commit()

    default = idx.list_by_type("session", scope_hash="h")
    assert {r["slug"] for r in default} == {"alive1"}

    all_states = idx.list_by_type("session", scope_hash="h", include_decayed=True)
    assert {r["slug"] for r in all_states} == {"alive1", "forgotten1"}


def test_fingerprint_body_uses_first_500_chars(tmp_path: Path):
    long_body = "x" * 600
    fp1 = fingerprint_body(long_body)
    fp2 = fingerprint_body(long_body[:500])
    assert fp1 == fp2
    assert fp1 != fingerprint_body("y")


def test_record_recall_updates_last_recalled_and_count(tmp_path: Path):
    idx = open_index(tmp_path / "x.db")
    idx.index_memory(_build_memory(), body_path="p.md")
    idx.record_recall("2026-05-14-t")
    row = idx.get_memory("2026-05-14-t")
    assert row["recall_count"] == 1
    assert row["last_recalled_at"] is not None
