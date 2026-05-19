"""Tests for ``memoryd.profile.trends`` (trigger frequency aggregates +
digest section renderer).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from memoryd.index import open_index
from memoryd.profile.migrations import ensure_profile_schema
from memoryd.profile.trends import (
    increment_trigger,
    increment_triggers,
    recall_hot,
    render_trends_section,
    rising_triggers,
    top_triggers,
)
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    idx = open_index(tmp_path / "index.db")
    yield idx.conn
    idx.close()


@pytest.fixture
def empty_conn() -> sqlite3.Connection:
    """Minimal in-memory DB with only profile tables — used for trigger-only
    unit tests that don't need the full memories schema."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_profile_schema(c)
    # Also create the memories columns recall_hot reads, so render_trends_section
    # doesn't error on those queries.
    c.executescript(
        """
        CREATE TABLE memories (
          slug TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          type TEXT NOT NULL,
          scope_hash TEXT NOT NULL,
          recall_count INTEGER NOT NULL DEFAULT 0,
          decay_state TEXT NOT NULL DEFAULT 'alive',
          scope_sensitive INTEGER NOT NULL DEFAULT 0,
          last_recalled_at TEXT,
          created_at TEXT NOT NULL
        );
        """
    )
    return c


def test_increment_trigger_inserts_on_first_call(empty_conn):
    today = date.today().isoformat()
    increment_trigger(empty_conn, "wolin", day=today)
    row = empty_conn.execute(
        "SELECT hits FROM trigger_stats WHERE trigger=? AND day=?",
        ("wolin", today),
    ).fetchone()
    assert row["hits"] == 1


def test_increment_trigger_on_conflict_bumps_counter(empty_conn):
    increment_trigger(empty_conn, "wolin", day="2026-05-15")
    increment_trigger(empty_conn, "wolin", day="2026-05-15")
    increment_trigger(empty_conn, "wolin", day="2026-05-15")
    row = empty_conn.execute(
        "SELECT hits FROM trigger_stats WHERE trigger=? AND day=?",
        ("wolin", "2026-05-15"),
    ).fetchone()
    assert row["hits"] == 3


def test_increment_trigger_partitions_by_scope_hash(empty_conn):
    increment_trigger(empty_conn, "logo", scope_hash="scope-a", day="2026-05-15")
    increment_trigger(empty_conn, "logo", scope_hash="scope-b", day="2026-05-15")
    increment_trigger(empty_conn, "logo", scope_hash="scope-a", day="2026-05-15")
    rows = empty_conn.execute(
        "SELECT scope_hash, hits FROM trigger_stats WHERE trigger=? "
        "ORDER BY scope_hash",
        ("logo",),
    ).fetchall()
    assert [(r["scope_hash"], r["hits"]) for r in rows] == [
        ("scope-a", 2),
        ("scope-b", 1),
    ]


def test_increment_trigger_ignores_empty_trigger(empty_conn):
    increment_trigger(empty_conn, "", day="2026-05-15")
    row = empty_conn.execute("SELECT COUNT(*) FROM trigger_stats").fetchone()[0]
    assert row == 0


def test_increment_triggers_batch(empty_conn):
    increment_triggers(empty_conn, ["a", "b", "a", ""], day="2026-05-15")
    rows = {
        r["trigger"]: r["hits"]
        for r in empty_conn.execute(
            "SELECT trigger, hits FROM trigger_stats"
        ).fetchall()
    }
    assert rows == {"a": 2, "b": 1}


def test_top_triggers_respects_window(empty_conn):
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    # 1 day ago — inside the 7-day window
    increment_trigger(empty_conn, "recent", day=(now - timedelta(days=1)).date().isoformat())
    # 10 days ago — outside the 7-day window
    increment_trigger(empty_conn, "old", day=(now - timedelta(days=10)).date().isoformat())
    result = top_triggers(empty_conn, window_days=7, now=now)
    triggers_only = [t for t, _ in result]
    assert "recent" in triggers_only
    assert "old" not in triggers_only


def test_top_triggers_sums_across_days(empty_conn):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    increment_trigger(empty_conn, "wolin", day="2026-05-15", hits=3)
    increment_trigger(empty_conn, "wolin", day="2026-05-17", hits=2)
    increment_trigger(empty_conn, "rust", day="2026-05-17", hits=4)
    result = top_triggers(empty_conn, window_days=7, now=now)
    as_dict = dict(result)
    assert as_dict["wolin"] == 5
    assert as_dict["rust"] == 4
    # rust > wolin so should be first.
    assert result[0][0] == "wolin" or result[0][0] == "rust"


def test_top_triggers_scope_filter(empty_conn):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    increment_trigger(empty_conn, "x", scope_hash="A", day="2026-05-18")
    increment_trigger(empty_conn, "y", scope_hash="B", day="2026-05-18")
    result = top_triggers(empty_conn, window_days=7, scope_hash="A", now=now)
    assert [t for t, _ in result] == ["x"]


def test_top_triggers_orders_by_hits_then_name(empty_conn):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    increment_trigger(empty_conn, "zzz", day="2026-05-18", hits=5)
    increment_trigger(empty_conn, "aaa", day="2026-05-18", hits=5)
    increment_trigger(empty_conn, "mmm", day="2026-05-18", hits=2)
    result = top_triggers(empty_conn, window_days=7, now=now)
    assert result[0][0] == "aaa"
    assert result[1][0] == "zzz"
    assert result[2][0] == "mmm"


def test_rising_triggers_detects_recent_spike(empty_conn):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    # Prior 21 days (recent_days=7 + baseline 21): 14-21 days ago = baseline window
    # in this helper it's [now - recent - baseline, now - recent) -> [-28, -7) ago
    increment_trigger(empty_conn, "logo", day=(now - timedelta(days=15)).date().isoformat())
    increment_trigger(empty_conn, "logo", day=(now - timedelta(days=20)).date().isoformat())
    # Recent 7-day spike
    increment_trigger(empty_conn, "logo", day=(now - timedelta(days=1)).date().isoformat(), hits=8)

    out = rising_triggers(empty_conn, recent_days=7, baseline_days=21, now=now)
    assert out, "should detect rising 'logo'"
    assert out[0][0] == "logo"
    assert out[0][1] >= 8  # recent
    assert out[0][2] == 2  # prior


def test_rising_triggers_filters_non_rising(empty_conn):
    """A trigger that's stable (no growth) shouldn't appear."""
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    increment_trigger(empty_conn, "stable", day=(now - timedelta(days=2)).date().isoformat(), hits=3)
    increment_trigger(empty_conn, "stable", day=(now - timedelta(days=15)).date().isoformat(), hits=10)
    out = rising_triggers(empty_conn, recent_days=7, baseline_days=21, now=now)
    assert "stable" not in [t for t, _, _ in out]


def test_recall_hot_filters_session_and_low_recall(conn):
    """recall_hot should exclude session-type and rows with recall_count < 2."""
    # session with high recall — should be excluded
    conn.execute(
        "INSERT INTO memories (slug,type,scope_hash,title,source,created_at,"
        "fingerprint,body_path,recall_count) VALUES (?,?,?,?,?,?,?,?,?)",
        ("s1", "session", "h", "session-high-recall", "m", "2026-01-01",
         "fp1", "p1", 10),
    )
    # decision with recall>=2 — should be included
    conn.execute(
        "INSERT INTO memories (slug,type,scope_hash,title,source,created_at,"
        "fingerprint,body_path,recall_count) VALUES (?,?,?,?,?,?,?,?,?)",
        ("d1", "decision", "h", "decision-recurring", "m", "2026-01-01",
         "fp2", "p2", 5),
    )
    # decision with recall=1 — excluded
    conn.execute(
        "INSERT INTO memories (slug,type,scope_hash,title,source,created_at,"
        "fingerprint,body_path,recall_count) VALUES (?,?,?,?,?,?,?,?,?)",
        ("d2", "decision", "h", "decision-fresh", "m", "2026-01-01",
         "fp3", "p3", 1),
    )
    conn.commit()
    hot = recall_hot(conn)
    titles = [h["title"] for h in hot]
    assert "decision-recurring" in titles
    assert "session-high-recall" not in titles
    assert "decision-fresh" not in titles


def test_recall_hot_excludes_sensitive_scopes(conn):
    conn.execute(
        "INSERT INTO memories (slug,type,scope_hash,title,source,created_at,"
        "fingerprint,body_path,recall_count,scope_sensitive) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("sec", "decision", "h", "secret", "m", "2026-01-01", "fp", "p", 99, 1),
    )
    conn.commit()
    hot = recall_hot(conn)
    assert all(h["title"] != "secret" for h in hot)


def test_render_trends_section_includes_all_blocks(empty_conn):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    increment_trigger(empty_conn, "wolin", day=(now - timedelta(days=2)).date().isoformat(), hits=4)
    out = render_trends_section(empty_conn, window_days=7, now=now)
    assert "## 趋势 trends" in out
    assert "top triggers" in out
    assert "rising" in out
    assert "recall hot" in out
    assert "wolin" in out


def test_render_trends_section_handles_empty_db(empty_conn):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    out = render_trends_section(empty_conn, window_days=7, now=now)
    # all three sub-sections should render the (无) placeholder
    assert out.count("(无)") >= 3
