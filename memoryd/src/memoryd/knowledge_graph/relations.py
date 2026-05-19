"""把 ``extract.py`` 的输出落到 SQLite。

职责：
1. **批量 upsert entities** — 调用 ``store.upsert_entity``。
2. **normalize relations** — predicate 大小写规整 / 别名换主 id / 去重。
3. **写 relations 表** — 每条关系附带 ``source_memory_id`` + ``confidence``。

normalize 的核心是把 LLM 偶尔产生的同义谓词归一：
- ``"used_by"`` 反转方向 → ``"uses"``
- ``"superseded by"`` 空格归一 → ``"superseded_by"``
- 未知 predicate 保留原文，让上层决定丢弃 / 接收。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .extract import ExtractResult, ExtractedRelation
from .store import (
    ALLOWED_PREDICATES,
    KnowledgeGraphStore,
)


_log = logging.getLogger(__name__)


# ---- predicate 归一化 ----------------------------------------------------

# 把 LLM 容易输出的"同义 / 反向"谓词归一到白名单
_PREDICATE_ALIASES: dict[str, tuple[str, bool]] = {
    # 值 = (canonical, reverse_direction)
    "mention": ("mentions", False),
    "mentioned": ("mentions", False),
    "mentioned_by": ("mentions", True),
    "work_on": ("works_on", False),
    "working_on": ("works_on", False),
    "owns": ("works_on", False),
    "use": ("uses", False),
    "used": ("uses", False),
    "used_by": ("uses", True),
    "prefer": ("prefers", False),
    "preferred": ("prefers", False),
    "supersede": ("supersedes", False),
    "supersedes_by": ("superseded_by", False),
    "superseded by": ("superseded_by", False),
    "supersedes by": ("superseded_by", False),
    "conflict": ("conflicts_with", False),
    "conflict_with": ("conflicts_with", False),
    "conflicts": ("conflicts_with", False),
    "cite": ("cites", False),
    "cited": ("cites", False),
    "cited_by": ("cites", True),
    "runs": ("runs_on", False),
    "run_on": ("runs_on", False),
    "deployed_on": ("runs_on", False),
    "belongs": ("belongs_to", False),
    "belong_to": ("belongs_to", False),
    "part_of": ("belongs_to", False),
    "located": ("located_at", False),
    "at": ("located_at", False),
}


def normalize_predicate(raw: str) -> tuple[str, bool]:
    """规整 predicate 字符串。

    Returns
    -------
    (canonical, reverse)
        ``canonical`` 是规整后的谓词；``reverse=True`` 时调用方需把
        subject / object 互换。
    """
    if not raw:
        return ("", False)
    norm = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if norm in ALLOWED_PREDICATES:
        return (norm, False)
    if norm in _PREDICATE_ALIASES:
        return _PREDICATE_ALIASES[norm]
    # 未识别 → 原样返回，由上层决定接收 / 丢弃
    return (norm, False)


@dataclass
class IngestStats:
    entities_added: int = 0
    entities_updated: int = 0
    relations_added: int = 0
    relations_skipped: int = 0


def ingest_extract_result(
    store: KnowledgeGraphStore,
    result: ExtractResult,
    *,
    source_memory_id: str,
    scope_hash: str | None,
    min_relation_confidence: float = 0.0,
    drop_unknown_predicates: bool = False,
) -> IngestStats:
    """把抽取结果写库。**幂等**：同 (subject, predicate, object, memory) 不重复入。

    Parameters
    ----------
    store:
        :class:`KnowledgeGraphStore` 实例。
    result:
        ``extract_entities_and_relations`` 返回值。
    source_memory_id:
        触发此抽取的 memory slug，存入 relations.source_memory_id。
    scope_hash:
        相同 scope_hash 写到 relations.scope_hash（便于 by-scope 查询）。
    min_relation_confidence:
        低于此阈值的关系跳过（默认 0.0 即全收）。
    drop_unknown_predicates:
        ``True`` 时把不在白名单的 predicate 直接丢；``False`` 时保留。
    """
    stats = IngestStats()

    # ---- entities：upsert 所有抽出的实体 -----------------------------------
    for ent in result.entities:
        before = store.get_entity(ent.id)
        store.upsert_entity(
            name=ent.name,
            type=ent.type,
            aliases=ent.aliases,
            scope_hash=scope_hash,
            context=ent.context,
        )
        if before is None:
            stats.entities_added += 1
        else:
            stats.entities_updated += 1

    # ---- relations：normalize + 写表 -------------------------------------
    for rel in result.relations:
        if rel.confidence < min_relation_confidence:
            stats.relations_skipped += 1
            continue

        canonical, reverse = normalize_predicate(rel.predicate)
        if not canonical:
            stats.relations_skipped += 1
            continue
        if drop_unknown_predicates and canonical not in ALLOWED_PREDICATES:
            stats.relations_skipped += 1
            continue

        # 反向 predicate → 互换 subject/object
        if reverse:
            subj_id = rel.object_id
            obj_id = rel.subject_id
            subj_name, subj_type = rel.object_name, rel.object_type
            obj_name, obj_type = rel.subject_name, rel.subject_type
        else:
            subj_id = rel.subject_id
            obj_id = rel.object_id
            subj_name, subj_type = rel.subject_name, rel.subject_type
            obj_name, obj_type = rel.object_name, rel.object_type

        # 确保两端 entity 存在（即使没在 entities 列表里也补写）
        if store.get_entity(subj_id) is None:
            store.upsert_entity(
                name=subj_name, type=subj_type, scope_hash=scope_hash
            )
        if store.get_entity(obj_id) is None:
            store.upsert_entity(
                name=obj_name, type=obj_type, scope_hash=scope_hash
            )

        rid = store.add_relation(
            subject_id=subj_id,
            predicate=canonical,
            object_id=obj_id,
            source_memory_id=source_memory_id,
            confidence=rel.confidence,
            scope_hash=scope_hash,
        )
        if rid:
            stats.relations_added += 1
        else:
            stats.relations_skipped += 1

    # 额外：自动给每个 entity 写一条 mentions 关系（memory → entity），便于
    # "这条 memory 提到了哪些实体"反查。memory 端用 memory:slug 作为 id，
    # subject_kind='memory'；不去重 confidence。
    for ent in result.entities:
        store.add_relation(
            subject_id=f"memory:{source_memory_id}",
            subject_kind="memory",
            predicate="mentions",
            object_id=ent.id,
            object_kind="entity",
            source_memory_id=source_memory_id,
            confidence=ent.confidence,
            scope_hash=scope_hash,
        )

    return stats


__all__ = [
    "IngestStats",
    "ingest_extract_result",
    "normalize_predicate",
]
