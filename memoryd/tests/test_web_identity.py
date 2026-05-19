"""Tests for the Plan 11 /identity page + /api/identity/* endpoints."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memoryd.profile.migrations import ensure_profile_schema
from memoryd.profile.store import ProfileStore
from memoryd.web import create_app


def _seed_profile(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "index.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_profile_schema(conn)
    return conn


def _write_identity_file(tmp_path: Path, body: str) -> Path:
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    f = profile_dir / "identity.md"
    f.write_text(body, encoding="utf-8")
    return f


def test_identity_page_empty(tmp_path):
    """No DB and no file → 'no versions yet' notice still 200."""
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/identity?token=t")
    assert r.status_code == 200
    assert "用户画像" in r.text
    assert "尚无任何画像" in r.text


def test_identity_page_with_current_file(tmp_path):
    body = "## 谁我是\n阿宝，独立开发者，做 memoryd。\n"
    _write_identity_file(tmp_path, body)
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/identity?token=t")
    assert r.status_code == 200
    assert "阿宝" in r.text
    assert "memoryd" in r.text
    # 'current' header
    assert "当前 identity.md" in r.text


def test_identity_page_with_versions(tmp_path):
    conn = _seed_profile(tmp_path)
    store = ProfileStore(conn)
    store.save_version(
        "## v1\nbody one\n",
        trigger="weekly_cron",
        change_summary="initial version",
    )
    store.save_version(
        "## v2\nbody two\n",
        trigger="manual",
        change_summary="refined preferences",
    )
    conn.close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/identity?token=t")
    assert r.status_code == 200
    assert "v1" in r.text
    assert "v2" in r.text
    assert "refined preferences" in r.text or "initial version" in r.text


def test_identity_version_page_shows_specific_version(tmp_path):
    conn = _seed_profile(tmp_path)
    store = ProfileStore(conn)
    store.save_version("## first\nALPHA\n", trigger="manual", change_summary="s1")
    store.save_version("## second\nBETA\n", trigger="manual", change_summary="s2")
    conn.close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/identity/version/1?token=t")
    assert r.status_code == 200
    assert "ALPHA" in r.text
    # The selected_version layout renders body, not the current body
    assert "v1" in r.text


def test_identity_diff_renders_diff(tmp_path):
    conn = _seed_profile(tmp_path)
    store = ProfileStore(conn)
    store.save_version("line a\nline b\nline c\n", trigger="manual")
    store.save_version("line a\nline b CHANGED\nline c\n", trigger="manual")
    conn.close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/identity/diff?from=1&to=2&token=t")
    assert r.status_code == 200
    assert "diff-view" in r.text
    # The diff should mention the changed line content
    assert "CHANGED" in r.text


def test_identity_diff_missing_versions(tmp_path):
    conn = _seed_profile(tmp_path)
    store = ProfileStore(conn)
    store.save_version("only v1\n", trigger="manual")
    conn.close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/identity/diff?from=1&to=99&token=t")
    assert r.status_code == 200
    # Should not crash; renders empty-diff state
    assert "无差异" in r.text or "不合法" in r.text or "diff-view" not in r.text


def test_api_identity_report_from_db(tmp_path):
    conn = _seed_profile(tmp_path)
    store = ProfileStore(conn)
    store.save_change_report("2026-04", "# 2026-04 报告\n本月很安静。\n")
    conn.close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/identity/report/2026-04?token=t")
    assert r.status_code == 200
    assert "2026-04 报告" in r.text


def test_api_identity_report_from_disk(tmp_path):
    """If SQLite doesn't have the row but the .md exists on disk, return it."""
    reports = tmp_path / "profile" / "change-reports"
    reports.mkdir(parents=True)
    (reports / "2026-03.md").write_text("# 2026-03 月报\n要点。\n", encoding="utf-8")
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/identity/report/2026-03?token=t")
    assert r.status_code == 200
    assert "2026-03 月报" in r.text


def test_api_identity_report_404(tmp_path):
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/identity/report/2026-12?token=t")
    assert r.status_code == 404


def test_identity_lists_reports(tmp_path):
    conn = _seed_profile(tmp_path)
    store = ProfileStore(conn)
    store.save_change_report(
        "2026-04",
        "# april\n",
        versions_count=4,
        supersedes_count=2,
        entities_added=10,
        entities_dropped=1,
    )
    conn.close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/identity?token=t")
    assert r.status_code == 200
    assert "2026-04" in r.text


def test_identity_requires_token(tmp_path):
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/identity")
    assert r.status_code == 401
