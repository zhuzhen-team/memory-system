"""query.py 图查询 API 测试。"""
from __future__ import annotations

import sqlite3

import networkx as nx
import pytest

from memoryd.knowledge_graph import (
    KnowledgeGraphStore,
    ensure_kg_schema,
    entity_neighborhood_summary,
    evolution_chain,
    find_conflicts,
    memories_about_entity,
    n_hop_subgraph,
    to_cytoscape_elements,
)


def _build_memories_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS memories (
          slug TEXT PRIMARY KEY,
          type TEXT NOT NULL,
          scope_hash TEXT NOT NULL,
          title TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT '2026-05-01'
        );
        """
    )
    conn.commit()


def _add_memory(conn: sqlite3.Connection, slug: str, type_: str = "preference",
                scope_hash: str = "s1", title: str = "t") -> None:
    conn.execute(
        "INSERT INTO memories (slug, type, scope_hash, title) VALUES (?, ?, ?, ?)",
        (slug, type_, scope_hash, title),
    )
    conn.commit()


@pytest.fixture()
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_kg_schema(conn)
    _build_memories_table(conn)
    return KnowledgeGraphStore(conn)


# ---- memories_about_entity ----------------------------------------------


def test_memories_about_entity_returns_mentions(store):
    ent = store.upsert_entity("abble", "person")
    for slug in ("m1", "m2", "m3"):
        _add_memory(store.conn, slug)
        store.add_relation(
            subject_id=f"memory:{slug}", subject_kind="memory",
            predicate="mentions", object_id=ent.id, object_kind="entity",
            source_memory_id=slug, confidence=0.9,
        )
    out = memories_about_entity(store, ent.id)
    assert set(out) == {"m1", "m2", "m3"}


def test_memories_about_entity_filters_by_type(store):
    ent = store.upsert_entity("abble", "person")
    _add_memory(store.conn, "p1", type_="preference")
    _add_memory(store.conn, "d1", type_="decision")
    for slug in ("p1", "d1"):
        store.add_relation(
            subject_id=f"memory:{slug}", subject_kind="memory",
            predicate="mentions", object_id=ent.id, object_kind="entity",
            source_memory_id=slug, confidence=0.9,
        )
    out = memories_about_entity(store, ent.id, types=["decision"])
    assert out == ["d1"]


def test_memories_about_entity_empty_when_no_mentions(store):
    assert memories_about_entity(store, "entity:person:nope") == []


# ---- n_hop_subgraph -----------------------------------------------------


def _link(store: KnowledgeGraphStore, a: str, p: str, b: str,
          memory_id: str = "m1", scope: str | None = None) -> None:
    store.add_relation(
        subject_id=a, predicate=p, object_id=b,
        source_memory_id=memory_id, confidence=0.9, scope_hash=scope,
    )


def test_n_hop_subgraph_depth_zero_returns_only_seed(store):
    a = store.upsert_entity("a", "person")
    g = n_hop_subgraph(store, a.id, depth=0)
    assert list(g.nodes) == [a.id]
    assert list(g.edges) == []


def test_n_hop_subgraph_depth_one(store):
    a = store.upsert_entity("a", "person")
    b = store.upsert_entity("b", "project")
    c = store.upsert_entity("c", "tool")
    _link(store, a.id, "works_on", b.id)
    _link(store, b.id, "uses", c.id)

    g = n_hop_subgraph(store, a.id, depth=1)
    assert set(g.nodes) == {a.id, b.id}
    assert (a.id, b.id) in g.edges


def test_n_hop_subgraph_depth_two(store):
    a = store.upsert_entity("a", "person")
    b = store.upsert_entity("b", "project")
    c = store.upsert_entity("c", "tool")
    _link(store, a.id, "works_on", b.id)
    _link(store, b.id, "uses", c.id)

    g = n_hop_subgraph(store, a.id, depth=2)
    assert set(g.nodes) == {a.id, b.id, c.id}
    assert g.has_edge(a.id, b.id)
    assert g.has_edge(b.id, c.id)


def test_n_hop_subgraph_skips_memory_nodes_by_default(store):
    a = store.upsert_entity("a", "person")
    store.add_relation(
        subject_id="memory:m1", subject_kind="memory",
        predicate="mentions", object_id=a.id, object_kind="entity",
        source_memory_id="m1", confidence=0.9,
    )
    g = n_hop_subgraph(store, a.id, depth=2)
    assert "memory:m1" not in g.nodes


def test_n_hop_subgraph_includes_memory_nodes_when_asked(store):
    a = store.upsert_entity("a", "person")
    store.add_relation(
        subject_id="memory:m1", subject_kind="memory",
        predicate="mentions", object_id=a.id, object_kind="entity",
        source_memory_id="m1", confidence=0.9,
    )
    g = n_hop_subgraph(store, a.id, depth=2, include_memory_nodes=True)
    assert "memory:m1" in g.nodes


def test_n_hop_subgraph_node_attrs_have_entity_metadata(store):
    a = store.upsert_entity("abble", "person")
    g = n_hop_subgraph(store, a.id, depth=0)
    attrs = g.nodes[a.id]
    assert attrs["name"] == "abble"
    assert attrs["type"] == "person"
    assert attrs["mention_count"] == 1


def test_n_hop_subgraph_handles_cycles(store):
    a = store.upsert_entity("a", "person")
    b = store.upsert_entity("b", "person")
    _link(store, a.id, "mentions", b.id, memory_id="m1")
    _link(store, b.id, "mentions", a.id, memory_id="m2")
    g = n_hop_subgraph(store, a.id, depth=5)
    assert set(g.nodes) == {a.id, b.id}


# ---- evolution_chain ----------------------------------------------------


def test_evolution_chain_returns_topo_order(store):
    # 链：m0 -> m1 -> m2 -> m3
    eid = "entity:concept:wake_time"
    store.upsert_entity("wake_time", "concept")
    store.add_supersede("m1", "m0", entity_id=eid, confidence=0.9)
    store.add_supersede("m2", "m1", entity_id=eid, confidence=0.9)
    store.add_supersede("m3", "m2", entity_id=eid, confidence=0.9)
    chain = evolution_chain(store, eid)
    assert chain == ["m0", "m1", "m2", "m3"]


def test_evolution_chain_empty_when_no_supersedes(store):
    assert evolution_chain(store, "entity:person:nope") == []


def test_evolution_chain_only_returns_supersedes_for_entity(store):
    store.upsert_entity("a", "concept")
    store.upsert_entity("b", "concept")
    store.add_supersede("m1", "m0", entity_id="entity:concept:a", confidence=0.9)
    store.add_supersede("n1", "n0", entity_id="entity:concept:b", confidence=0.9)
    assert evolution_chain(store, "entity:concept:a") == ["m0", "m1"]
    assert evolution_chain(store, "entity:concept:b") == ["n0", "n1"]


# ---- find_conflicts -----------------------------------------------------


def test_find_conflicts_detects_same_subject_different_object_for_prefers(store):
    a = store.upsert_entity("abble", "person")
    morning = store.upsert_entity("morning", "concept")
    night = store.upsert_entity("night", "concept")
    _add_memory(store.conn, "m1")
    _add_memory(store.conn, "m2")
    _link(store, a.id, "prefers", morning.id, memory_id="m1", scope="s1")
    _link(store, a.id, "prefers", night.id, memory_id="m2", scope="s1")
    conflicts = find_conflicts(store, scope_hash="s1")
    assert ("m1", "m2") in conflicts or ("m2", "m1") in conflicts


def test_find_conflicts_ignores_inactive_relations(store):
    a = store.upsert_entity("a", "person")
    x = store.upsert_entity("x", "concept")
    y = store.upsert_entity("y", "concept")
    _add_memory(store.conn, "m1")
    _add_memory(store.conn, "m2")
    rid1 = store.add_relation(
        a.id, "prefers", x.id, source_memory_id="m1", confidence=0.9, scope_hash="s1"
    )
    store.add_relation(
        a.id, "prefers", y.id, source_memory_id="m2", confidence=0.9, scope_hash="s1"
    )
    store.mark_relation_superseded(rid1)
    assert find_conflicts(store, scope_hash="s1") == []


def test_find_conflicts_excludes_other_predicates(store):
    """mentions / cites 等不计入 conflict 判定。"""
    a = store.upsert_entity("a", "person")
    x = store.upsert_entity("x", "concept")
    y = store.upsert_entity("y", "concept")
    _link(store, a.id, "mentions", x.id, memory_id="m1", scope="s1")
    _link(store, a.id, "mentions", y.id, memory_id="m2", scope="s1")
    assert find_conflicts(store, scope_hash="s1") == []


def test_find_conflicts_dedupes_pairs(store):
    """同一对 memory 不重复返回。"""
    a = store.upsert_entity("a", "person")
    b = store.upsert_entity("b", "concept")
    c = store.upsert_entity("c", "concept")
    _link(store, a.id, "uses", b.id, memory_id="m1", scope="s1")
    _link(store, a.id, "uses", c.id, memory_id="m2", scope="s1")
    # 再加一条同 subject + 不同 object 的 prefers 关系，触发 m1/m2 第二种冲突
    _link(store, a.id, "prefers", b.id, memory_id="m1", scope="s1")
    _link(store, a.id, "prefers", c.id, memory_id="m2", scope="s1")
    conflicts = find_conflicts(store, scope_hash="s1")
    # m1/m2 仅一对（去重）
    assert len(conflicts) == 1


# ---- to_cytoscape_elements ----------------------------------------------


def test_to_cytoscape_elements_emits_nodes_and_edges(store):
    a = store.upsert_entity("abble", "person")
    b = store.upsert_entity("memoryd", "project")
    _link(store, a.id, "works_on", b.id)
    g = n_hop_subgraph(store, a.id, depth=1)
    elements = to_cytoscape_elements(g)

    nodes = [e for e in elements if "source" not in e["data"]]
    edges = [e for e in elements if "source" in e["data"]]
    assert len(nodes) == 2
    assert len(edges) == 1
    node_ids = {n["data"]["id"] for n in nodes}
    assert node_ids == {a.id, b.id}
    edge = edges[0]
    assert edge["data"]["source"] == a.id
    assert edge["data"]["target"] == b.id
    assert edge["data"]["predicate"] == "works_on"
    # cytoscape 边 id 是稳定 source--pred->target
    assert "works_on" in edge["data"]["id"]


def test_to_cytoscape_elements_on_empty_graph():
    g = nx.DiGraph()
    assert to_cytoscape_elements(g) == []


# ---- entity_neighborhood_summary ---------------------------------------


def test_entity_neighborhood_summary_basic(store):
    a = store.upsert_entity("a", "person")
    b = store.upsert_entity("b", "project")
    c = store.upsert_entity("c", "tool")
    _link(store, a.id, "works_on", b.id)
    _link(store, c.id, "mentions", a.id)
    summary = entity_neighborhood_summary(store, a.id, depth=1)
    assert summary["entity"]["name"] == "a"
    directions = {n["direction"]: n["predicate"] for n in summary["neighbors"]}
    assert directions.get("out") == "works_on"
    assert directions.get("in") == "mentions"


def test_entity_neighborhood_summary_unknown_entity(store):
    out = entity_neighborhood_summary(store, "entity:person:ghost")
    assert out == {"entity": None, "neighbors": []}
