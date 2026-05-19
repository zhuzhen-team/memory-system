"""Knowledge graph 模块 —— 自动学习用户画像的核心。

三表（SQLite）：

- ``entities`` — 7 类：person / organization / place / library / tool / project / concept
- ``relations`` — 11 种 predicate：mentions / works_on / uses / prefers /
  supersedes / superseded_by / conflicts_with / cites / runs_on / belongs_to / located_at
- ``supersedes_chain`` — 时间维度的"新取代旧"演化链

调用流程典型路径：

.. code-block:: python

    # 1) 在新 memory 写入后：
    result = await extract_entities_and_relations(text, slug, scope_hash)
    stats = ingest_extract_result(store, result, source_memory_id=slug, scope_hash=scope_hash)
    # 2) 检测同 entity 时间窗 supersede：
    sres = await detect_supersedes_for_new_memory(
        store, slug, [e.id for e in result.entities], scope_hash=scope_hash,
        new_memory_text=text,
    )
    # 3) Web Dashboard / CLI 查图：
    g = n_hop_subgraph(store, entity_id, depth=2)
    elements = to_cytoscape_elements(g)
"""
from __future__ import annotations

from .extract import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractResult,
    extract_entities_and_relations,
)
from .migrations import ensure_kg_schema, open_kg_db
from .query import (
    entity_neighborhood_summary,
    evolution_chain,
    find_conflicts,
    memories_about_entity,
    n_hop_subgraph,
    to_cytoscape_elements,
)
from .relations import (
    IngestStats,
    ingest_extract_result,
    normalize_predicate,
)
from .store import (
    ALLOWED_PREDICATES,
    ENTITY_TYPES,
    Entity,
    KnowledgeGraphStore,
    Relation,
    make_entity_id,
)
from .supersedes import (
    SUPERSEDE_TYPES,
    SupersedeCandidate,
    SupersedesResult,
    detect_supersedes_for_new_memory,
)


__all__ = [
    "ALLOWED_PREDICATES",
    "ENTITY_TYPES",
    "Entity",
    "ExtractResult",
    "ExtractedEntity",
    "ExtractedRelation",
    "IngestStats",
    "KnowledgeGraphStore",
    "Relation",
    "SUPERSEDE_TYPES",
    "SupersedeCandidate",
    "SupersedesResult",
    "detect_supersedes_for_new_memory",
    "ensure_kg_schema",
    "entity_neighborhood_summary",
    "evolution_chain",
    "extract_entities_and_relations",
    "find_conflicts",
    "ingest_extract_result",
    "make_entity_id",
    "memories_about_entity",
    "n_hop_subgraph",
    "normalize_predicate",
    "open_kg_db",
    "to_cytoscape_elements",
]
