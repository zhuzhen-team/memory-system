"""Tests for ``memoryd.profile.store`` (DAO over profile_versions /
profile_change_reports). Pure SQLite — no LLM, no filesystem.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from memoryd.profile.migrations import ensure_profile_schema
from memoryd.profile.store import ProfileStore, ProfileVersion


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_profile_schema(c)
    return c


@pytest.fixture
def store(conn: sqlite3.Connection) -> ProfileStore:
    return ProfileStore(conn)


def test_latest_version_none_when_empty(store: ProfileStore):
    assert store.latest_version() is None


def test_save_version_assigns_monotonic_version_num(store: ProfileStore):
    v1 = store.save_version("a", trigger="manual")
    v2 = store.save_version("b", trigger="manual")
    v3 = store.save_version("c", trigger="weekly_cron")
    assert (v1.version_num, v2.version_num, v3.version_num) == (1, 2, 3)


def test_save_version_persists_all_metadata(store: ProfileStore):
    start = datetime(2026, 5, 12, tzinfo=timezone.utc)
    end = datetime(2026, 5, 19, tzinfo=timezone.utc)
    v = store.save_version(
        "## who\nuser\n",
        trigger="weekly_cron",
        diff_from_prev="@@\n-old\n+new\n",
        change_summary="dropped one belief, added two",
        sources_count=17,
        sources_window_start=start,
        sources_window_end=end,
        written_at=end,
    )
    fetched = store.latest_version()
    assert fetched is not None
    assert fetched.id == v.id
    assert fetched.version_num == 1
    assert fetched.trigger == "weekly_cron"
    assert fetched.content_md == "## who\nuser\n"
    assert fetched.diff_from_prev == "@@\n-old\n+new\n"
    assert fetched.change_summary == "dropped one belief, added two"
    assert fetched.sources_count == 17
    assert fetched.sources_window_start == start
    assert fetched.sources_window_end == end
    assert fetched.written_at == end


def test_latest_version_returns_highest_version_num(store: ProfileStore):
    store.save_version("v1", trigger="manual")
    store.save_version("v2", trigger="manual")
    v3 = store.save_version("v3", trigger="manual")
    latest = store.latest_version()
    assert latest is not None
    assert latest.version_num == 3
    assert latest.id == v3.id


def test_list_versions_since_filters_by_written_at(store: ProfileStore):
    t0 = datetime(2026, 4, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 5, 10, tzinfo=timezone.utc)
    store.save_version("apr", trigger="weekly_cron", written_at=t0)
    store.save_version("may1", trigger="weekly_cron", written_at=t1)
    store.save_version("may10", trigger="weekly_cron", written_at=t2)
    rows = store.list_versions(since=datetime(2026, 5, 1, tzinfo=timezone.utc))
    assert [v.content_md for v in rows] == ["may1", "may10"]


def test_list_versions_until_excludes_endpoint(store: ProfileStore):
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    store.save_version("may", trigger="weekly_cron", written_at=t0)
    store.save_version("jun", trigger="weekly_cron", written_at=t1)
    rows = store.list_versions(
        since=t0, until=datetime(2026, 6, 1, tzinfo=timezone.utc)
    )
    assert [v.content_md for v in rows] == ["may"]


def test_save_change_report_upserts(store: ProfileStore):
    store.save_change_report(
        "2026-04",
        "# April\nstable",
        versions_count=4,
        supersedes_count=2,
        entities_added=12,
        entities_dropped=3,
    )
    # Same key — should overwrite, not insert a second row.
    store.save_change_report(
        "2026-04",
        "# April (revised)",
        versions_count=5,
        supersedes_count=3,
        entities_added=15,
        entities_dropped=4,
    )
    rep = store.get_change_report("2026-04")
    assert rep is not None
    assert rep["content_md"] == "# April (revised)"
    assert rep["versions_count"] == 5
    assert rep["supersedes_count"] == 3
    assert rep["entities_added"] == 15
    assert rep["entities_dropped"] == 4
    all_reports = store.list_change_reports()
    assert len(all_reports) == 1


def test_get_change_report_missing_returns_none(store: ProfileStore):
    assert store.get_change_report("1999-01") is None


def test_list_change_reports_orders_newest_first(store: ProfileStore):
    store.save_change_report("2026-03", "mar")
    store.save_change_report("2026-04", "apr")
    store.save_change_report("2026-02", "feb")
    rows = store.list_change_reports()
    assert [r["period"] for r in rows] == ["2026-04", "2026-03", "2026-02"]


def test_profile_version_from_row_handles_naive_iso(store: ProfileStore):
    """ProfileVersion.written_at should be tz-aware even if written_at column
    happens to be a naive ISO string (defensive parsing)."""
    naive = datetime(2026, 5, 1, 10, 0, 0)
    store.conn.execute(
        """
        INSERT INTO profile_versions
          (version_num, written_at, trigger, content_md, sources_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        (99, naive.isoformat(), "manual", "x", 0),
    )
    store.conn.commit()
    v = store.latest_version()
    assert v is not None
    assert v.written_at.tzinfo is not None


def test_ensure_profile_schema_is_idempotent():
    c = sqlite3.connect(":memory:")
    ensure_profile_schema(c)
    ensure_profile_schema(c)  # should not raise
    cols = {r[1] for r in c.execute("PRAGMA table_info(profile_versions)")}
    assert {"id", "version_num", "trigger", "content_md"} <= cols
