"""Tests for the Plan 11 /trends page + /api/trends/* JSON endpoints."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memoryd.profile.migrations import ensure_profile_schema
from memoryd.profile.trends import increment_trigger
from memoryd.web import create_app


def _seed_full_index(tmp_path: Path, *, with_kg: bool = False) -> sqlite3.Connection:
    """Create an index.db with profile + memories + (optionally) entities tables."""
    db = tmp_path / "index.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_profile_schema(conn)
    # memories table (subset of columns recall_hot reads)
    conn.executescript(
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
    if with_kg:
        from memoryd.knowledge_graph import ensure_kg_schema, KnowledgeGraphStore
        ensure_kg_schema(conn)
        store = KnowledgeGraphStore(conn)
        # seed an entity so the activity list is non-empty
        for _ in range(3):
            store.upsert_entity("abble", "person", scope_hash="h1")
        for _ in range(2):
            store.upsert_entity("memoryd", "project", scope_hash="h1")
    conn.commit()
    return conn


def test_trends_page_renders_empty_db(tmp_path):
    """Missing index.db → page renders 'unavailable' notice."""
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/trends?token=t")
    assert r.status_code == 200
    assert "趋势" in r.text
    assert "未启用" in r.text


def test_trends_page_renders_with_data(tmp_path):
    conn = _seed_full_index(tmp_path)
    # bump some triggers
    increment_trigger(conn, "logo", "h1", day="2026-05-18", hits=5)
    increment_trigger(conn, "wolin", "h1", day="2026-05-17", hits=3)
    increment_trigger(conn, "logo", "h1", day="2026-05-15", hits=2)
    # add a recall hot memory
    conn.execute(
        "INSERT INTO memories (slug, title, type, scope_hash, recall_count, "
        "                      last_recalled_at, created_at) "
        "VALUES ('m1', 'logo direction', 'decision', 'h1', 5, "
        "        '2026-05-19', '2026-05-10')"
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/trends?token=t&window=14")
    assert r.status_code == 200
    assert "logo" in r.text
    assert "wolin" in r.text
    assert "logo direction" in r.text
    # The trends section header is present
    assert "Top Triggers" in r.text or "top triggers" in r.text.lower()
    # No 'unavailable' notice
    assert "未启用" not in r.text


def test_trends_window_param_changes_window(tmp_path):
    conn = _seed_full_index(tmp_path)
    increment_trigger(conn, "x", "h1", day="2026-05-18", hits=4)
    conn.close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/trends?token=t&window=30")
    assert r.status_code == 200
    # window=30 should appear in the page heading
    assert "30" in r.text


def test_api_trends_triggers_returns_data(tmp_path):
    conn = _seed_full_index(tmp_path)
    increment_trigger(conn, "wolin", "h1", day="2026-05-18", hits=3)
    increment_trigger(conn, "memoryd", "h1", day="2026-05-18", hits=2)
    conn.close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/trends/triggers?token=t&window=30")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    triggers = {row["trigger"]: row["hits"] for row in data["triggers"]}
    assert triggers.get("wolin") == 3
    assert triggers.get("memoryd") == 2


def test_api_trends_triggers_unavailable_no_db(tmp_path):
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/trends/triggers?token=t")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert data["triggers"] == []


def test_api_trends_entities_returns_data(tmp_path):
    _seed_full_index(tmp_path, with_kg=True).close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/trends/entities?token=t&window=365")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    names = {e["name"] for e in data["entities"]}
    assert "abble" in names or "memoryd" in names


def test_api_trends_entities_unavailable_no_kg(tmp_path):
    """No entities table → API returns available=False."""
    _seed_full_index(tmp_path, with_kg=False).close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/trends/entities?token=t")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False


def test_trends_requires_token(tmp_path):
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/trends")
    assert r.status_code == 401


def test_trends_renders_active_entities_when_kg_present(tmp_path):
    _seed_full_index(tmp_path, with_kg=True).close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/trends?token=t")
    assert r.status_code == 200
    # 'memoryd' or 'abble' should appear in the top_entities section
    assert ("memoryd" in r.text) or ("abble" in r.text)


def test_trends_bars_use_max_for_scale(tmp_path):
    """Test that the rendered HTML at least includes a bar-fill style width."""
    conn = _seed_full_index(tmp_path)
    increment_trigger(conn, "biggest", "h1", day="2026-05-18", hits=10)
    increment_trigger(conn, "smallest", "h1", day="2026-05-18", hits=1)
    conn.close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/trends?token=t&window=30")
    assert r.status_code == 200
    assert "bar-fill" in r.text
    assert "100.0%" in r.text  # biggest = 100% of max
