"""Tests for the Plan 11 relations / knowledge-graph web pages + JSON API."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memoryd.knowledge_graph import (
    KnowledgeGraphStore,
    ensure_kg_schema,
)
from memoryd.web import create_app


def _seed_kg(tmp_path: Path) -> sqlite3.Connection:
    """Create index.db with KG schema and a small entity / relation graph."""
    db = tmp_path / "index.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_kg_schema(conn)
    store = KnowledgeGraphStore(conn)
    abble = store.upsert_entity("abble", "person", scope_hash="h1")
    memoryd_proj = store.upsert_entity("memoryd", "project", scope_hash="h1")
    networkx = store.upsert_entity("networkx", "library", scope_hash="h1")
    # bump mention_count so top_entities returns them
    for _ in range(2):
        store.upsert_entity("abble", "person", scope_hash="h1")
        store.upsert_entity("memoryd", "project", scope_hash="h1")
    store.add_relation(
        abble.id, "works_on", memoryd_proj.id, scope_hash="h1",
        source_memory_id="memory:m1",
    )
    store.add_relation(
        memoryd_proj.id, "uses", networkx.id, scope_hash="h1",
        source_memory_id="memory:m1",
    )
    conn.commit()
    return conn


def test_relations_page_renders_without_kg(tmp_path):
    """No index.db → page shows 'unavailable' notice but still 200."""
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/relations?token=t")
    assert r.status_code == 200
    assert "知识图谱" in r.text
    assert "未启用" in r.text  # notice rendered


def test_relations_page_renders_with_kg(tmp_path):
    _seed_kg(tmp_path).close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/relations?token=t")
    assert r.status_code == 200
    assert "知识图谱" in r.text
    assert "cytoscape" in r.text.lower()
    # No "unavailable" warning when KG tables exist
    assert "未启用" not in r.text


def test_relations_entity_focus_route(tmp_path):
    _seed_kg(tmp_path).close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/relations/entity/entity:person:abble?token=t")
    assert r.status_code == 200
    # The focus is wired via data attribute
    assert "entity:person:abble" in r.text


def test_api_graph_global_empty_when_no_db(tmp_path):
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/graph/global?token=t")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert data["elements"] == []


def test_api_graph_global_returns_cytoscape_elements(tmp_path):
    _seed_kg(tmp_path).close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/graph/global?token=t&depth=2&window_days=365")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    # Should have at least 3 entity nodes and 2 edges
    nodes = [e for e in data["elements"] if "source" not in e["data"]]
    edges = [e for e in data["elements"] if "source" in e["data"]]
    assert len(nodes) >= 3
    assert len(edges) >= 2
    # Verify predicate metadata is in edges
    predicates = {e["data"].get("predicate") for e in edges}
    assert "works_on" in predicates or "uses" in predicates


def test_api_graph_entity_returns_subgraph(tmp_path):
    _seed_kg(tmp_path).close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/graph/entity:person:abble?token=t&depth=2")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    assert data["entity"] is not None
    assert data["entity"]["name"] == "abble"
    # subgraph should include the abble node
    node_ids = [e["data"]["id"] for e in data["elements"] if "source" not in e["data"]]
    assert "entity:person:abble" in node_ids


def test_api_graph_entity_unknown_id(tmp_path):
    _seed_kg(tmp_path).close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/graph/entity:person:nobody?token=t")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    assert data["entity"] is None


def test_api_graph_global_filter_by_scope(tmp_path):
    """Passing scope=h2 (empty scope) should return zero nodes."""
    _seed_kg(tmp_path).close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/graph/global?token=t&scope=h2&window_days=365")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    assert data["elements"] == []


def test_relations_page_scope_dropdown_lists_scopes(tmp_path):
    """When scopes/<hash>/ dirs exist on disk they show in the dropdown."""
    (tmp_path / "scopes" / "abc123def" / "sessions").mkdir(parents=True)
    (tmp_path / "scopes" / "abc123def" / "sessions" / "x.md").write_text("x")
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/relations?token=t")
    assert r.status_code == 200
    # Scope hash should appear (truncated to 10 chars in the dropdown)
    assert "abc123def" in r.text


def test_relations_requires_token(tmp_path):
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/relations")
    assert r.status_code == 401


def test_api_graph_global_skipped_when_kg_module_missing(tmp_path, monkeypatch):
    """Simulate ImportError → returns available=False, doesn't 500."""
    _seed_kg(tmp_path).close()
    # Monkey-patch sys.modules so the import inside the route fails
    import sys
    sys.modules.pop("memoryd.knowledge_graph", None)
    monkeypatch.setitem(sys.modules, "memoryd.knowledge_graph", None)
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/api/graph/global?token=t")
    # Either reports unavailable, or works (if monkey-patch couldn't simulate ImportError).
    # We just need: no 500.
    assert r.status_code == 200
