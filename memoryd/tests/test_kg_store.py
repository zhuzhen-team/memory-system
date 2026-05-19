"""knowledge_graph.store DAO tests."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from memoryd.knowledge_graph import (
    ENTITY_TYPES,
    KnowledgeGraphStore,
    ensure_kg_schema,
    make_entity_id,
)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    """In-memory SQLite 句柄，已初始化三表。"""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_kg_schema(c)
    return c


@pytest.fixture()
def store(conn: sqlite3.Connection) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(conn)


# ---- entity 基本 CRUD ---------------------------------------------------


def test_make_entity_id_normalises_name():
    assert make_entity_id("person", "Abble") == "entity:person:abble"
    assert make_entity_id("project", "memory system") == "entity:project:memory_system"


def test_make_entity_id_rejects_unknown_type():
    with pytest.raises(ValueError):
        make_entity_id("alien", "x")


def test_upsert_entity_inserts_new_row(store: KnowledgeGraphStore):
    ent = store.upsert_entity("abble", "person", aliases=["阿宝"], scope_hash="scope1")
    assert ent.id == "entity:person:abble"
    assert ent.name == "abble"
    assert ent.type == "person"
    assert ent.aliases == ["阿宝"]
    assert ent.mention_count == 1
    assert ent.scope_hash == "scope1"
    assert ent.decay_state == "fresh"


def test_upsert_entity_rejects_unknown_type(store: KnowledgeGraphStore):
    with pytest.raises(ValueError):
        store.upsert_entity("x", "alien")


def test_upsert_entity_second_call_increments_mention_and_merges_aliases(
    store: KnowledgeGraphStore,
):
    first = store.upsert_entity("abble", "person", aliases=["阿宝"], scope_hash="scope1")
    second = store.upsert_entity(
        "abble", "person", aliases=["王同学"], scope_hash="scope2", context="new ctx"
    )
    assert second.id == first.id
    assert second.mention_count == 2
    assert second.aliases == ["阿宝", "王同学"]  # union 保序
    assert second.context == "new ctx"
    # 首次的 scope_hash 不被覆盖
    assert second.scope_hash == "scope1"


def test_upsert_entity_different_types_same_name_are_distinct(store):
    p = store.upsert_entity("memoryd", "project")
    t = store.upsert_entity("memoryd", "tool")
    assert p.id != t.id
    assert p.type == "project"
    assert t.type == "tool"


def test_get_entity_returns_none_when_missing(store):
    assert store.get_entity("entity:person:nope") is None


def test_find_entities_by_name_fuzzy(store):
    store.upsert_entity("abble", "person", aliases=["阿宝"])
    store.upsert_entity("alice", "person")
    res = store.find_entities_by_name("abb")
    assert len(res) == 1 and res[0].name == "abble"
    # alias 命中
    res2 = store.find_entities_by_name("阿宝")
    assert len(res2) == 1 and res2[0].name == "abble"


def test_find_entities_by_name_exact(store):
    store.upsert_entity("abble", "person")
    store.upsert_entity("alice", "person")
    res = store.find_entities_by_name("abble", fuzzy=False)
    assert len(res) == 1
    assert store.find_entities_by_name("abb", fuzzy=False) == []


def test_top_entities_orders_by_mention_count_within_window(store):
    # 3 个实体，提到次数不同
    store.upsert_entity("a", "person")
    store.upsert_entity("a", "person")
    store.upsert_entity("a", "person")
    store.upsert_entity("b", "person")
    store.upsert_entity("b", "person")
    store.upsert_entity("c", "person")
    top = store.top_entities(window_days=30, top_k=10)
    assert [e.name for e in top[:3]] == ["a", "b", "c"]


def test_top_entities_window_excludes_old_entries(store):
    store.upsert_entity("old", "person")
    # 直接改 last_seen_at 到 60 天前
    cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    store.conn.execute(
        "UPDATE entities SET last_seen_at = ?, first_seen_at = ? WHERE id = ?",
        (cutoff, cutoff, make_entity_id("person", "old")),
    )
    store.conn.commit()
    store.upsert_entity("new", "person")
    top = store.top_entities(window_days=30)
    names = [e.name for e in top]
    assert "old" not in names
    assert "new" in names


def test_top_entities_filters_by_scope(store):
    store.upsert_entity("a", "person", scope_hash="s1")
    store.upsert_entity("b", "person", scope_hash="s2")
    out = store.top_entities(scope_hash="s1")
    assert [e.name for e in out] == ["a"]


def test_list_entities_filters_by_type(store):
    store.upsert_entity("a", "person")
    store.upsert_entity("memoryd", "project")
    persons = store.list_entities(type="person")
    projects = store.list_entities(type="project")
    assert len(persons) == 1 and persons[0].name == "a"
    assert len(projects) == 1 and projects[0].name == "memoryd"


def test_update_decay_state(store):
    e = store.upsert_entity("a", "person")
    store.update_decay_state(e.id, "dim")
    after = store.get_entity(e.id)
    assert after.decay_state == "dim"


# ---- relations -----------------------------------------------------------


def test_add_relation_inserts_row(store):
    a = store.upsert_entity("abble", "person")
    b = store.upsert_entity("memoryd", "project")
    rid = store.add_relation(
        a.id, "works_on", b.id,
        source_memory_id="sess1", confidence=0.9, scope_hash="s1",
    )
    assert rid > 0
    rels = store.get_relations(subject_id=a.id)
    assert len(rels) == 1
    assert rels[0].predicate == "works_on"
    assert rels[0].confidence == 0.9


def test_add_relation_is_idempotent_on_unique_quadruple(store):
    a = store.upsert_entity("a", "person")
    b = store.upsert_entity("b", "project")
    rid1 = store.add_relation(a.id, "works_on", b.id, source_memory_id="m1", confidence=0.9)
    rid2 = store.add_relation(a.id, "works_on", b.id, source_memory_id="m1", confidence=0.7)
    assert rid1 == rid2
    rels = store.get_relations(subject_id=a.id)
    assert len(rels) == 1
    # 旧 confidence 保留
    assert rels[0].confidence == 0.9


def test_add_relation_different_source_memory_creates_new_row(store):
    a = store.upsert_entity("a", "person")
    b = store.upsert_entity("b", "project")
    r1 = store.add_relation(a.id, "works_on", b.id, source_memory_id="m1", confidence=0.9)
    r2 = store.add_relation(a.id, "works_on", b.id, source_memory_id="m2", confidence=0.9)
    assert r1 != r2
    assert len(store.get_relations(subject_id=a.id)) == 2


def test_get_relations_filters(store):
    a = store.upsert_entity("a", "person")
    b = store.upsert_entity("b", "project")
    c = store.upsert_entity("c", "project")
    store.add_relation(a.id, "works_on", b.id, source_memory_id="m1", confidence=0.9)
    store.add_relation(a.id, "uses", c.id, source_memory_id="m2", confidence=0.7)
    works = store.get_relations(subject_id=a.id, predicate="works_on")
    assert len(works) == 1 and works[0].object_id == b.id


def test_mark_relation_superseded_excludes_from_active_query(store):
    a = store.upsert_entity("a", "person")
    b = store.upsert_entity("b", "project")
    rid = store.add_relation(a.id, "works_on", b.id, source_memory_id="m1", confidence=0.9)
    store.mark_relation_superseded(rid)
    assert store.get_relations(subject_id=a.id, active_only=True) == []
    assert len(store.get_relations(subject_id=a.id, active_only=False)) == 1


def test_neighbors_returns_both_directions(store):
    a = store.upsert_entity("a", "person")
    b = store.upsert_entity("b", "project")
    store.add_relation(a.id, "works_on", b.id, source_memory_id="m1", confidence=0.9)
    assert len(store.neighbors(a.id)) == 1
    assert len(store.neighbors(b.id)) == 1


# ---- supersedes_chain ---------------------------------------------------


def test_add_supersede_writes_row(store):
    store.add_supersede(
        "memNew", "memOld",
        entity_id="entity:person:abble",
        confidence=0.92,
        reason="user said no more 7am",
    )
    rows = store.get_supersedes_for("memNew")
    assert len(rows) == 1
    assert rows[0]["older_memory_id"] == "memOld"
    assert rows[0]["confidence"] == 0.92
    assert rows[0]["decided_by"] == "auto"


def test_add_supersede_rejects_self_loop(store):
    with pytest.raises(ValueError):
        store.add_supersede("m1", "m1", confidence=0.9)


def test_add_supersede_is_upsert_on_pk(store):
    store.add_supersede("n", "o", confidence=0.8, reason="r1", decided_by="auto")
    store.add_supersede("n", "o", confidence=0.95, reason="r2", decided_by="user")
    rows = store.get_supersedes_for("n")
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.95
    assert rows[0]["decided_by"] == "user"
    assert rows[0]["reason"] == "r2"


def test_get_superseded_by(store):
    store.add_supersede("n", "o", confidence=0.9)
    assert store.get_superseded_by("o")[0]["newer_memory_id"] == "n"
    assert store.get_superseded_by("nope") == []


# ---- schema 自检 --------------------------------------------------------


def test_schema_has_all_three_tables(conn):
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "entities" in tables
    assert "relations" in tables
    assert "supersedes_chain" in tables


def test_ensure_kg_schema_is_idempotent(conn):
    # 二次调用应当无副作用
    ensure_kg_schema(conn)
    ensure_kg_schema(conn)
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"entities", "relations", "supersedes_chain"}.issubset(tables)


def test_entity_type_check_enforced(conn):
    # CHECK 约束拒绝非法类型
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO entities (id, name, type, first_seen_at, last_seen_at) "
            "VALUES ('entity:alien:x','x','alien','2026-01-01','2026-01-01')"
        )


def test_entity_types_cover_seven_categories():
    assert set(ENTITY_TYPES) == {
        "person", "organization", "place", "library", "tool", "project", "concept"
    }
