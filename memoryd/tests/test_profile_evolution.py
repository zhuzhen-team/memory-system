"""Tests for ``memoryd.profile.evolution`` — monthly change report.

LLM always mocked. Coverage:
- generates report based on profile_versions in the target month
- counts supersedes from promotions table
- writes markdown file under ``profile/change-reports/YYYY-MM.md``
- writes row into ``profile_change_reports``
- ``dry_run`` returns preview only
- ``_month_window`` correctly bounds the month
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from memoryd.index import open_index
from memoryd.profile import evolution as evo
from memoryd.profile.evolution import (
    _month_window,
    _period_label,
    generate_monthly_change_report,
)
from memoryd.profile.store import ProfileStore


@pytest.fixture
def profile_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "profile"
    monkeypatch.setenv("MEMORYD_PROFILE_DIR", str(d))
    return d


@pytest.fixture
def memory_root(tmp_path: Path) -> Path:
    root = tmp_path / "memoryd_data"
    root.mkdir()
    return root


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, user: str, model: str | None = None) -> str:
        self.calls.append({"system": system, "user": user, "model": model})
        return self.response


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------


def test_month_window_april_2026():
    start, end = _month_window(2026, 4)
    assert start == datetime(2026, 4, 1, tzinfo=timezone.utc)
    assert end.day == 30
    assert end.hour == 23


def test_period_label_pads():
    assert _period_label(2026, 5) == "2026-05"
    assert _period_label(2026, 12) == "2026-12"


# ---------------------------------------------------------------------------
# generate_monthly_change_report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_writes_report_and_persists_row(
    profile_dir: Path, memory_root: Path
):
    idx = open_index(memory_root / "index.db")
    store = ProfileStore(idx.conn)
    # Seed two versions in April 2026
    store.save_version(
        "v1 body",
        trigger="weekly_cron",
        change_summary="确立 Rust 偏好",
        sources_count=5,
        written_at=datetime(2026, 4, 6, tzinfo=timezone.utc),
    )
    store.save_version(
        "v2 body",
        trigger="weekly_cron",
        change_summary="补充 logo 颜色决定",
        sources_count=7,
        written_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
    )

    llm = FakeLLM(response="# 2026-04 月度报告\n\n稳定，新增两条偏好。")

    result = await generate_monthly_change_report(
        idx.conn, store, llm=llm, year=2026, month=4
    )

    assert result["period"] == "2026-04"
    assert result["versions_count"] == 2
    assert "稳定" in result["content_md"]

    md = (profile_dir / "change-reports" / "2026-04.md").read_text(encoding="utf-8")
    assert "2026-04 月度报告" in md
    assert result["path"] == str(profile_dir / "change-reports" / "2026-04.md")

    rep = store.get_change_report("2026-04")
    assert rep is not None
    assert rep["content_md"].strip().startswith("# 2026-04")
    assert rep["versions_count"] == 2
    idx.close()


@pytest.mark.asyncio
async def test_generate_counts_supersedes_in_window(
    profile_dir: Path, memory_root: Path
):
    idx = open_index(memory_root / "index.db")
    store = ProfileStore(idx.conn)
    # Seed an approved supersede in April.
    in_april = datetime(2026, 4, 15, tzinfo=timezone.utc).isoformat()
    out_of_april = datetime(2026, 5, 2, tzinfo=timezone.utc).isoformat()
    # Memory rows must exist first because promotions has a FK on
    # source_session_slug → memories.slug.
    idx.conn.execute(
        "INSERT INTO memories (slug,type,scope_hash,title,source,created_at,"
        "fingerprint,body_path) VALUES (?,?,?,?,?,?,?,?)",
        ("s1", "session", "scope", "t", "m", in_april, "f1", "p1"),
    )
    idx.conn.execute(
        "INSERT INTO memories (slug,type,scope_hash,title,source,created_at,"
        "fingerprint,body_path) VALUES (?,?,?,?,?,?,?,?)",
        ("s2", "session", "scope", "t", "m", out_of_april, "f2", "p2"),
    )
    idx.conn.execute(
        """INSERT INTO promotions (source_session_slug, proposed_type, proposed_title,
           proposed_body, proposed_triggers, dura_score, reasoning,
           proposed_supersedes, scope_hash, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("s1", "decision", "新决定 A", "b", "[]", "{}", "", '["old-1"]',
         "scope", "approved", in_april),
    )
    idx.conn.execute(
        """INSERT INTO promotions (source_session_slug, proposed_type, proposed_title,
           proposed_body, proposed_triggers, dura_score, reasoning,
           proposed_supersedes, scope_hash, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("s2", "decision", "五月决定", "b", "[]", "{}", "", '["old-2"]',
         "scope", "approved", out_of_april),
    )
    idx.conn.commit()

    llm = FakeLLM(response="report\n")
    result = await generate_monthly_change_report(
        idx.conn, store, llm=llm, year=2026, month=4
    )
    idx.close()
    assert result["supersedes_count"] == 1


@pytest.mark.asyncio
async def test_generate_filters_supersedes_in_sensitive_scope(
    profile_dir: Path, memory_root: Path
):
    idx = open_index(memory_root / "index.db")
    idx.register_sensitive_scope("hidden-scope", "/fake/private")
    store = ProfileStore(idx.conn)
    when = datetime(2026, 4, 10, tzinfo=timezone.utc).isoformat()
    idx.conn.execute(
        "INSERT INTO memories (slug,type,scope_hash,title,source,created_at,"
        "fingerprint,body_path) VALUES (?,?,?,?,?,?,?,?)",
        ("sX", "session", "hidden-scope", "t", "m", when, "fX", "pX"),
    )
    idx.conn.execute(
        """INSERT INTO promotions (source_session_slug, proposed_type, proposed_title,
           proposed_body, proposed_triggers, dura_score, reasoning,
           proposed_supersedes, scope_hash, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("sX", "decision", "私密更替", "b", "[]", "{}", "", '["old-x"]',
         "hidden-scope", "approved", when),
    )
    idx.conn.commit()

    llm = FakeLLM(response="rpt\n")
    result = await generate_monthly_change_report(
        idx.conn, store, llm=llm, year=2026, month=4
    )
    idx.close()
    assert result["supersedes_count"] == 0
    # No leak into the LLM prompt either.
    assert "私密更替" not in llm.calls[0]["user"]


@pytest.mark.asyncio
async def test_generate_dry_run_skips_disk_and_db(
    profile_dir: Path, memory_root: Path
):
    idx = open_index(memory_root / "index.db")
    store = ProfileStore(idx.conn)
    store.save_version(
        "x", trigger="weekly_cron",
        written_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
    )

    llm = FakeLLM(response="dry preview\n")
    result = await generate_monthly_change_report(
        idx.conn, store, llm=llm, year=2026, month=4, dry_run=True
    )
    assert result["path"] is None
    assert not (profile_dir / "change-reports" / "2026-04.md").exists()
    assert store.get_change_report("2026-04") is None
    idx.close()


@pytest.mark.asyncio
async def test_generate_handles_empty_month(
    profile_dir: Path, memory_root: Path
):
    """No versions / supersedes — LLM still gets called and report saved."""
    idx = open_index(memory_root / "index.db")
    store = ProfileStore(idx.conn)
    llm = FakeLLM(response="本月画像无明显变化。\n")
    result = await generate_monthly_change_report(
        idx.conn, store, llm=llm, year=2026, month=4
    )
    idx.close()
    assert result["versions_count"] == 0
    assert result["supersedes_count"] == 0
    assert "无明显变化" in result["content_md"]


@pytest.mark.asyncio
async def test_generate_upsert_when_called_twice(
    profile_dir: Path, memory_root: Path
):
    idx = open_index(memory_root / "index.db")
    store = ProfileStore(idx.conn)
    llm = FakeLLM(response="first\n")
    await generate_monthly_change_report(
        idx.conn, store, llm=llm, year=2026, month=4
    )
    llm.response = "second (revised)\n"
    await generate_monthly_change_report(
        idx.conn, store, llm=llm, year=2026, month=4
    )
    rep = store.get_change_report("2026-04")
    assert rep is not None
    assert "second" in rep["content_md"]
    # Only one row for that period (upsert).
    assert len(store.list_change_reports()) == 1
    idx.close()
