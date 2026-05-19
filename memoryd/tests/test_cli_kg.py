"""Plan 10 Task: `memoryd kg ...` CLI subcommands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from memoryd import cli
from memoryd.index import open_index
from memoryd.knowledge_graph import KnowledgeGraphStore


def _args(**kw: object) -> argparse.Namespace:
    return argparse.Namespace(**kw)


def _seed(data_root: Path, fn) -> None:
    """Open the index DB at data_root, run `fn(conn, store)`, close cleanly."""
    idx = open_index(data_root / "index.db")
    try:
        store = KnowledgeGraphStore(idx.conn)
        fn(idx.conn, store)
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# kg entities
# ---------------------------------------------------------------------------


def test_kg_entities_table_output(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_data_root", lambda: tmp_path)

    def _build(conn, store):
        store.upsert_entity("abble", "person", scope_hash="s1")
        # Bump abble's mention count to make it top.
        store.upsert_entity("abble", "person", scope_hash="s1")
        store.upsert_entity("memoryd", "project", scope_hash="s1")

    _seed(tmp_path, _build)

    rc = cli._cmd_kg_entities(
        _args(scope=None, type_=None, top=10, window_days=30, as_json=False)
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "abble" in out
    assert "memoryd" in out


def test_kg_entities_json_and_filters(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_data_root", lambda: tmp_path)

    def _build(conn, store):
        store.upsert_entity("abble", "person")
        store.upsert_entity("memoryd", "project")

    _seed(tmp_path, _build)

    rc = cli._cmd_kg_entities(
        _args(scope=None, type_="person", top=5, window_days=30, as_json=True)
    )
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    types = {r["type"] for r in data}
    assert types == {"person"}


# ---------------------------------------------------------------------------
# kg memories-about
# ---------------------------------------------------------------------------


def test_kg_memories_about_by_name(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_data_root", lambda: tmp_path)

    def _build(conn, store):
        ent = store.upsert_entity("abble", "person")
        # memories table is created via migrations; insert two rows.
        conn.execute(
            "INSERT INTO memories (slug, type, scope_hash, title, source, body_path, created_at, fingerprint, recall_count) "
            "VALUES ('m1', 'preference', 's1', 'm1', 'test', 'm1.md', '2026-05-01', 'fp1', 0)"
        )
        conn.execute(
            "INSERT INTO memories (slug, type, scope_hash, title, source, body_path, created_at, fingerprint, recall_count) "
            "VALUES ('m2', 'decision', 's1', 'm2', 'test', 'm2.md', '2026-05-01', 'fp2', 0)"
        )
        conn.commit()
        for slug in ("m1", "m2"):
            store.add_relation(
                subject_id=f"memory:{slug}",
                subject_kind="memory",
                predicate="mentions",
                object_id=ent.id,
                object_kind="entity",
                source_memory_id=slug,
                confidence=0.9,
            )

    _seed(tmp_path, _build)

    rc = cli._cmd_kg_memories_about(
        _args(entity="abble", types=None, as_json=True)
    )
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert set(data["slugs"]) == {"m1", "m2"}


def test_kg_memories_about_entity_not_found(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_data_root", lambda: tmp_path)
    # Open index so the DB+schema exists, but don't add entities.
    _seed(tmp_path, lambda c, s: None)

    rc = cli._cmd_kg_memories_about(
        _args(entity="nobody", types=None, as_json=False)
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "not found" in err


# ---------------------------------------------------------------------------
# kg evolution
# ---------------------------------------------------------------------------


def test_kg_evolution_returns_chain(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_data_root", lambda: tmp_path)

    def _build(conn, store):
        ent = store.upsert_entity("python", "library")
        # Older → newer
        store.add_supersede("m_new", "m_old", entity_id=ent.id, confidence=0.9)

    _seed(tmp_path, _build)

    rc = cli._cmd_kg_evolution(_args(entity="python", as_json=True))
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["chain"] == ["m_old", "m_new"]


# ---------------------------------------------------------------------------
# kg subgraph
# ---------------------------------------------------------------------------


def test_kg_subgraph_writes_file(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_data_root", lambda: tmp_path)

    def _build(conn, store):
        a = store.upsert_entity("abble", "person")
        b = store.upsert_entity("memoryd", "project")
        store.add_relation(
            subject_id=a.id,
            predicate="works_on",
            object_id=b.id,
            confidence=0.9,
        )

    _seed(tmp_path, _build)

    out_path = tmp_path / "sub.json"
    rc = cli._cmd_kg_subgraph(
        _args(entity="abble", depth=1, out=str(out_path), format="cytoscape")
    )
    assert rc == 0
    payload = json.loads(out_path.read_text())
    # cytoscape list of dicts with a "data" key
    assert isinstance(payload, list)
    assert all("data" in e for e in payload)
    ids = {e["data"].get("id") for e in payload}
    assert "entity:person:abble" in ids


# ---------------------------------------------------------------------------
# kg conflicts
# ---------------------------------------------------------------------------


def test_kg_conflicts_empty(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_data_root", lambda: tmp_path)
    _seed(tmp_path, lambda c, s: None)
    rc = cli._cmd_kg_conflicts(_args(scope=None, as_json=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out) == []


def test_kg_conflicts_detects_disagreement(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_data_root", lambda: tmp_path)

    def _build(conn, store):
        subj = store.upsert_entity("abble", "person")
        obj_a = store.upsert_entity("python", "library")
        obj_b = store.upsert_entity("rust", "library")
        # Two prefers relations from same subject to different objects, same scope.
        store.add_relation(
            subject_id=subj.id,
            predicate="prefers",
            object_id=obj_a.id,
            source_memory_id="m_a",
            confidence=0.9,
            scope_hash="s1",
        )
        store.add_relation(
            subject_id=subj.id,
            predicate="prefers",
            object_id=obj_b.id,
            source_memory_id="m_b",
            confidence=0.9,
            scope_hash="s1",
        )

    _seed(tmp_path, _build)

    rc = cli._cmd_kg_conflicts(_args(scope=None, as_json=True))
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert len(data) == 1
    pair = {data[0]["mem_a"], data[0]["mem_b"]}
    assert pair == {"m_a", "m_b"}
