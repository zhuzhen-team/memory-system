"""自动 supersedes 检测。

场景：一条新的 preference / decision / fact 写入后，找同 entity 的旧
 preference / decision / fact，让 LLM 判断"新的是否取代旧的"。

阈值（与 plan10 锁定一致）：
- ``confidence >= 0.85``  自动应用：写 supersedes_chain + 把旧 memory 标 'dim'
- ``0.5 <= confidence < 0.85``  写一条 *pending* digest（暂存 supersedes_chain，
  decided_by='digest'）等用户审；不动旧 memory
- ``confidence < 0.5``  忽略，不入库

LLM 接口契约：``async (new_text, old_text, entity_name) -> {decision, confidence, reason}``
decision ∈ {"supersedes", "extends", "conflicts", "unrelated"}。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .store import KnowledgeGraphStore


_log = logging.getLogger(__name__)


# entity 触发 supersede 的 memory type；session 不触发（演化太频繁会失真）
SUPERSEDE_TYPES = ("preference", "decision", "fact")


@dataclass
class SupersedeCandidate:
    new_memory_id: str
    old_memory_id: str
    entity_id: str
    confidence: float
    reason: str
    decision: str = "supersedes"  # supersedes / extends / conflicts / unrelated


@dataclass
class SupersedesResult:
    applied: list[SupersedeCandidate] = field(default_factory=list)     # ≥ 0.85
    pending: list[SupersedeCandidate] = field(default_factory=list)     # 0.5–0.85
    ignored: list[SupersedeCandidate] = field(default_factory=list)     # < 0.5

    @property
    def total(self) -> int:
        return len(self.applied) + len(self.pending) + len(self.ignored)


# ---- 默认 judge（LLM 不可用时用一个保守 stub） ---------------------------


async def _stub_judge(new_text: str, old_text: str, entity_name: str) -> dict:
    """无 LLM 时的 stub：永远返回 unrelated / 0.0，等价于不触发任何 supersede。

    这样保证调用方在 offline 模式下不会误下结论，安全降级。
    """
    return {"decision": "unrelated", "confidence": 0.0, "reason": "no-llm"}


def _query_old_memories_with_entity(
    store: KnowledgeGraphStore,
    entity_id: str,
    *,
    exclude_memory_id: str,
    scope_hash: str | None,
    types: tuple[str, ...] = SUPERSEDE_TYPES,
) -> list[str]:
    """找所有"提到 entity_id"且 type ∈ types 的旧 memory slug。

    通过 relations 表的 (subject='memory:slug', predicate='mentions',
    object=entity_id) 反查 —— 把图层和 memories 表 join。
    """
    # 先拿 mentions 关系里所有 memory ids
    rows = store.conn.execute(
        """
        SELECT DISTINCT subject_id
        FROM relations
        WHERE object_id = ?
          AND predicate = 'mentions'
          AND subject_kind = 'memory'
          AND superseded_at IS NULL
        """,
        (entity_id,),
    ).fetchall()
    memory_ids = [r["subject_id"].removeprefix("memory:") for r in rows]
    memory_ids = [m for m in memory_ids if m and m != exclude_memory_id]
    if not memory_ids:
        return []

    # 用 memories 表过滤 type / scope
    placeholders = ",".join("?" * len(memory_ids))
    sql = (
        f"SELECT slug FROM memories WHERE slug IN ({placeholders}) "
        f"AND type IN ({','.join('?' * len(types))})"
    )
    args: list[object] = [*memory_ids, *types]
    if scope_hash is not None:
        sql += " AND scope_hash = ?"
        args.append(scope_hash)
    try:
        out = store.conn.execute(sql, args).fetchall()
    except Exception as e:
        _log.debug("memories table not available for supersede lookup: %s", e)
        return []
    return [r["slug"] for r in out]


def _load_memory_body(store: KnowledgeGraphStore, memory_id: str) -> str:
    """从 memories 表读 title + body_path（按需读文件）。

    fail-soft：找不到时返回空串，让 LLM 在没旧文本时返回 unrelated。
    """
    row = store.conn.execute(
        "SELECT title, body_path FROM memories WHERE slug = ?", (memory_id,)
    ).fetchone()
    if row is None:
        return ""
    # 这里不去真读 .md 文件——store 不知道 data_root；上层若需要更精确判断
    # 可以传入自定义 judge。返回 title 作为低保真签名足够触发"同 entity 旧记忆"判断。
    return row["title"] or ""


async def detect_supersedes_for_new_memory(
    store: KnowledgeGraphStore,
    new_memory_id: str,
    new_entity_ids: list[str],
    *,
    new_memory_text: str = "",
    scope_hash: str | None = None,
    llm=None,
    auto_threshold: float = 0.85,
    review_threshold: float = 0.5,
) -> SupersedesResult:
    """检测新 memory 是否 supersede 同 entity 的旧 memory。

    Parameters
    ----------
    store:
        :class:`KnowledgeGraphStore`。
    new_memory_id:
        刚写入的 memory slug。
    new_entity_ids:
        新 memory 抽出的实体 id 列表（来自 extract.py）。
    new_memory_text:
        新 memory 正文（喂给 LLM）。
    scope_hash:
        限定旧 memory 同 scope。
    llm:
        可注入的 async judge，签名 ``async (new, old, entity_name) -> dict``。
        ``None`` 时尝试 ``from memoryd.llm.prompts import judge_supersede``。
    auto_threshold / review_threshold:
        分流阈值（默认 0.85 / 0.5）。

    Returns
    -------
    SupersedesResult
        applied 已自动入库 + 旧 memory decay；pending 仅入库等审批；ignored 仅日志。
    """
    judge = llm
    if judge is None:
        try:
            from memoryd.llm.prompts import judge_supersede as judge  # type: ignore[attr-defined,no-redef]
        except Exception:
            judge = _stub_judge

    out = SupersedesResult()
    seen_pairs: set[tuple[str, str]] = set()

    for entity_id in new_entity_ids:
        ent = store.get_entity(entity_id)
        if ent is None:
            continue
        old_ids = _query_old_memories_with_entity(
            store,
            entity_id,
            exclude_memory_id=new_memory_id,
            scope_hash=scope_hash,
        )
        for old_id in old_ids:
            pair = (new_memory_id, old_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            old_signature = _load_memory_body(store, old_id)
            try:
                verdict = await judge(new_memory_text, old_signature, ent.name)
            except Exception as e:
                _log.warning("supersede judge failed for %s vs %s: %s",
                             new_memory_id, old_id, e)
                continue

            if not isinstance(verdict, dict):
                continue
            decision = str(verdict.get("decision") or "").strip().lower()
            try:
                conf = float(verdict.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            reason = str(verdict.get("reason") or "")[:500]

            cand = SupersedeCandidate(
                new_memory_id=new_memory_id,
                old_memory_id=old_id,
                entity_id=entity_id,
                confidence=max(0.0, min(1.0, conf)),
                reason=reason,
                decision=decision or "unrelated",
            )

            # 仅 decision == 'supersedes' 才走 supersede 流；其他状态走 conflicts 等
            # 但本函数主要管 supersedes，其他暂只记日志。
            if decision != "supersedes":
                out.ignored.append(cand)
                continue

            if cand.confidence >= auto_threshold:
                store.add_supersede(
                    newer_memory_id=new_memory_id,
                    older_memory_id=old_id,
                    entity_id=entity_id,
                    confidence=cand.confidence,
                    reason=reason,
                    decided_by="auto",
                )
                # 同步给旧 memory 打 dim（best-effort，没有 memories 表也不报错）
                try:
                    store.conn.execute(
                        "UPDATE memories SET decay_state = 'dim' WHERE slug = ?",
                        (old_id,),
                    )
                    store.conn.commit()
                except Exception:
                    pass
                out.applied.append(cand)
            elif cand.confidence >= review_threshold:
                store.add_supersede(
                    newer_memory_id=new_memory_id,
                    older_memory_id=old_id,
                    entity_id=entity_id,
                    confidence=cand.confidence,
                    reason=reason,
                    decided_by="digest",
                )
                out.pending.append(cand)
            else:
                out.ignored.append(cand)

    return out


__all__ = [
    "SUPERSEDE_TYPES",
    "SupersedeCandidate",
    "SupersedesResult",
    "detect_supersedes_for_new_memory",
]
