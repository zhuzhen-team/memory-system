"""图查询 API。

读侧——构造 networkx 图、跑 N-hop BFS、找冲突 / 演化链、转 cytoscape.js 元素。
所有函数都接 :class:`KnowledgeGraphStore`，不直接写 SQL（除少量必要 JOIN）。
"""
from __future__ import annotations

from collections import deque
from typing import Iterable

import networkx as nx

from .store import KnowledgeGraphStore


# ---- memory ↔ entity 反查 -----------------------------------------------


def memories_about_entity(
    store: KnowledgeGraphStore,
    entity_id: str,
    types: Iterable[str] | None = None,
) -> list[str]:
    """返回所有 mentions 了 ``entity_id`` 的 memory slug。

    可选 ``types`` 过滤到 memory.type ∈ types。
    """
    rows = store.conn.execute(
        """
        SELECT DISTINCT subject_id
        FROM relations
        WHERE object_id = ?
          AND predicate = 'mentions'
          AND subject_kind = 'memory'
          AND superseded_at IS NULL
        ORDER BY created_at DESC
        """,
        (entity_id,),
    ).fetchall()
    slugs = [r["subject_id"].removeprefix("memory:") for r in rows if r["subject_id"]]
    if not types:
        return slugs

    type_list = tuple(types)
    if not slugs:
        return []
    placeholders = ",".join("?" * len(slugs))
    sql = (
        f"SELECT slug FROM memories WHERE slug IN ({placeholders}) "
        f"AND type IN ({','.join('?' * len(type_list))}) "
        f"ORDER BY created_at DESC"
    )
    try:
        out = store.conn.execute(sql, [*slugs, *type_list]).fetchall()
    except Exception:
        return slugs
    return [r["slug"] for r in out]


# ---- N-hop subgraph -----------------------------------------------------


def n_hop_subgraph(
    store: KnowledgeGraphStore,
    entity_id: str,
    depth: int = 2,
    *,
    active_only: bool = True,
    include_memory_nodes: bool = False,
) -> nx.DiGraph:
    """从 ``entity_id`` 出发 BFS depth 跳，返回 networkx.DiGraph。

    - 节点属性：``name`` / ``type`` / ``mention_count`` / ``decay_state``。
    - 边属性：``predicate`` / ``confidence`` / ``source_memory_id``。
    - ``include_memory_nodes=False`` 时跳过 subject_kind='memory' 的节点
      （图谱视角通常只看 entity-entity）。
    """
    g: nx.DiGraph = nx.DiGraph()
    if depth < 0:
        return g

    visited: set[str] = set()
    frontier: deque[tuple[str, int]] = deque([(entity_id, 0)])

    def _add_entity_node(eid: str) -> None:
        if eid in g.nodes:
            return
        if eid.startswith("memory:"):
            if include_memory_nodes:
                g.add_node(eid, kind="memory")
            return
        ent = store.get_entity(eid)
        if ent is None:
            g.add_node(eid, kind="entity", name=eid, type="unknown")
            return
        g.add_node(
            eid,
            kind="entity",
            name=ent.name,
            type=ent.type,
            mention_count=ent.mention_count,
            decay_state=ent.decay_state,
        )

    _add_entity_node(entity_id)

    while frontier:
        node, d = frontier.popleft()
        if node in visited:
            continue
        visited.add(node)
        if d >= depth:
            continue

        rels = store.neighbors(node, active_only=active_only)
        for rel in rels:
            # 决定哪一端是"另一头"
            if rel.subject_id == node:
                other = rel.object_id
                src, dst = rel.subject_id, rel.object_id
            else:
                other = rel.subject_id
                src, dst = rel.subject_id, rel.object_id

            # 过滤 memory 节点（默认）
            if not include_memory_nodes and (
                src.startswith("memory:") or dst.startswith("memory:")
            ):
                continue

            _add_entity_node(src)
            _add_entity_node(dst)
            g.add_edge(
                src,
                dst,
                predicate=rel.predicate,
                confidence=rel.confidence,
                source_memory_id=rel.source_memory_id,
                relation_id=rel.id,
            )
            if other not in visited:
                frontier.append((other, d + 1))

    return g


# ---- 演化链 -------------------------------------------------------------


def evolution_chain(store: KnowledgeGraphStore, entity_id: str) -> list[str]:
    """返回与 ``entity_id`` 相关的 supersedes 链（按时间从旧到新）。

    实现思路：拉 supersedes_chain 表里 entity_id = ? 的所有 (newer, older)，
    然后用 DAG 拓扑排序得到完整链。如果中间断了（缺中间节点），仅返回连通子链。
    """
    rows = store.conn.execute(
        "SELECT newer_memory_id, older_memory_id FROM supersedes_chain "
        "WHERE entity_id = ? ORDER BY decided_at",
        (entity_id,),
    ).fetchall()
    if not rows:
        return []

    g: nx.DiGraph = nx.DiGraph()
    for r in rows:
        # older -> newer 表示"被取代 → 取代者"，便于拓扑排序得到时间顺序
        g.add_edge(r["older_memory_id"], r["newer_memory_id"])

    if not nx.is_directed_acyclic_graph(g):
        # 极少出现，但有可能 LLM 给出环——保守返回按 decided_at 顺序
        return [r["older_memory_id"] for r in rows] + [rows[-1]["newer_memory_id"]]

    try:
        ordered = list(nx.topological_sort(g))
    except nx.NetworkXUnfeasible:
        return []
    return ordered


# ---- 冲突检测 -----------------------------------------------------------


def find_conflicts(
    store: KnowledgeGraphStore,
    scope_hash: str | None = None,
) -> list[tuple[str, str]]:
    """返回所有 (mem_a, mem_b) 对：它们对同一 entity 写出"互斥"陈述。

    简化定义（plan10）：同 entity 同 subject_id 上有 ``prefers`` / ``uses`` /
    ``works_on`` 这三类 predicate 的两条 active relations，且 object 不同 →
    判作冲突候选。LLM-judge 在 supersedes.py 已处理 ``conflicts_with``，
    这里只做结构级粗筛，作 governance digest 的输入。
    """
    sql = """
    SELECT a.source_memory_id AS mem_a,
           b.source_memory_id AS mem_b,
           a.subject_id      AS subject,
           a.predicate       AS predicate,
           a.object_id       AS obj_a,
           b.object_id       AS obj_b
      FROM relations a
      JOIN relations b
        ON a.subject_id = b.subject_id
       AND a.predicate  = b.predicate
       AND a.id         < b.id
       AND a.object_id <> b.object_id
       AND a.superseded_at IS NULL
       AND b.superseded_at IS NULL
       AND a.predicate IN ('prefers', 'uses', 'works_on')
       AND a.source_memory_id IS NOT NULL
       AND b.source_memory_id IS NOT NULL
       AND a.source_memory_id <> b.source_memory_id
    """
    args: list[object] = []
    if scope_hash is not None:
        sql += " AND a.scope_hash = ? AND b.scope_hash = ?"
        args.extend([scope_hash, scope_hash])

    rows = store.conn.execute(sql, args).fetchall()
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        pair = tuple(sorted([r["mem_a"], r["mem_b"]]))
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
    return out


# ---- Cytoscape 输出 -----------------------------------------------------


def to_cytoscape_elements(graph: nx.DiGraph) -> list[dict]:
    """转 cytoscape.js 格式给 Web Dashboard 渲染。

    输出结构：

        [
          {"data": {"id": "...", "label": "...", "type": "...", ...}},
          ...
          {"data": {"source": "...", "target": "...", "predicate": "...", ...}},
          ...
        ]

    节点 / 边的字段全部走 ``data``——cytoscape 渲染约定。
    """
    elements: list[dict] = []
    for node_id, attrs in graph.nodes(data=True):
        data: dict = {"id": node_id}
        # label 优先用 name 字段，回落到 id
        data["label"] = attrs.get("name", node_id)
        for k, v in attrs.items():
            if k == "label":
                continue
            data[k] = v
        elements.append({"data": data})

    for src, dst, attrs in graph.edges(data=True):
        # cytoscape 边 data.id 可选；用 source+target+predicate 拼一个稳定 id
        predicate = attrs.get("predicate", "rel")
        edge_id = f"{src}--{predicate}->{dst}"
        data = {"id": edge_id, "source": src, "target": dst}
        for k, v in attrs.items():
            data[k] = v
        elements.append({"data": data})

    return elements


# ---- 其它便捷 -----------------------------------------------------------


def entity_neighborhood_summary(
    store: KnowledgeGraphStore, entity_id: str, depth: int = 1
) -> dict:
    """返回一个 plain dict 概括 entity 的邻居：用于 CLI / TUI 显示。

    形如：

        {
          "entity": {...},
          "neighbors": [
            {"predicate": "works_on", "other": {...}, "direction": "out"},
            ...
          ]
        }
    """
    ent = store.get_entity(entity_id)
    if ent is None:
        return {"entity": None, "neighbors": []}

    g = n_hop_subgraph(store, entity_id, depth=depth)
    neighbors: list[dict] = []
    for src, dst, attrs in g.edges(data=True):
        if src == entity_id:
            other_id = dst
            direction = "out"
        elif dst == entity_id:
            other_id = src
            direction = "in"
        else:
            continue
        other_attrs = g.nodes[other_id]
        neighbors.append(
            {
                "predicate": attrs.get("predicate"),
                "confidence": attrs.get("confidence"),
                "direction": direction,
                "other": {
                    "id": other_id,
                    "name": other_attrs.get("name"),
                    "type": other_attrs.get("type"),
                },
            }
        )
    return {
        "entity": {
            "id": ent.id,
            "name": ent.name,
            "type": ent.type,
            "mention_count": ent.mention_count,
            "decay_state": ent.decay_state,
        },
        "neighbors": neighbors,
    }


__all__ = [
    "entity_neighborhood_summary",
    "evolution_chain",
    "find_conflicts",
    "memories_about_entity",
    "n_hop_subgraph",
    "to_cytoscape_elements",
]
