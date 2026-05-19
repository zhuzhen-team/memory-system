"""supersedes.py 测试 —— LLM 必须 mock。"""
from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock

import pytest

from memoryd.knowledge_graph import (
    KnowledgeGraphStore,
    detect_supersedes_for_new_memory,
    ensure_kg_schema,
)


def _build_memories_table(conn: sqlite3.Connection) -> None:
    """supersedes 走的是 memories 表 JOIN —— 测试里建一份 minimal schema。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS memories (
          slug TEXT PRIMARY KEY,
          type TEXT NOT NULL,
          scope_hash TEXT NOT NULL,
          title TEXT NOT NULL,
          body_path TEXT,
          decay_state TEXT NOT NULL DEFAULT 'alive',
          created_at TEXT NOT NULL DEFAULT '2026-05-01'
        );
        """
    )
    conn.commit()


def _insert_memory(conn: sqlite3.Connection, slug: str, type_: str = "preference",
                   scope_hash: str = "s1", title: str = "title") -> None:
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


def _seed_entity_with_mentions_from(store: KnowledgeGraphStore,
                                    *, name: str, type_: str,
                                    memory_ids: list[str],
                                    scope_hash: str = "s1") -> str:
    """建一个 entity，并写 memory→entity 的 mentions 关系。"""
    ent = store.upsert_entity(name, type_, scope_hash=scope_hash)
    for mid in memory_ids:
        store.add_relation(
            subject_id=f"memory:{mid}",
            subject_kind="memory",
            predicate="mentions",
            object_id=ent.id,
            object_kind="entity",
            source_memory_id=mid,
            confidence=0.9,
            scope_hash=scope_hash,
        )
    return ent.id


# ---- 阈值分流 ------------------------------------------------------------


async def test_high_confidence_auto_applies(store):
    _insert_memory(store.conn, "old1", "preference", title="起床时间 7am")
    _insert_memory(store.conn, "new1", "preference", title="起床时间 9am")
    eid = _seed_entity_with_mentions_from(
        store, name="起床时间", type_="concept",
        memory_ids=["old1", "new1"],
    )

    llm = AsyncMock(return_value={
        "decision": "supersedes",
        "confidence": 0.95,
        "reason": "new time replaces 7am",
    })

    out = await detect_supersedes_for_new_memory(
        store,
        new_memory_id="new1",
        new_entity_ids=[eid],
        new_memory_text="now 9am",
        scope_hash="s1",
        llm=llm,
    )
    assert len(out.applied) == 1
    assert out.applied[0].old_memory_id == "old1"
    # supersedes_chain 已落
    rows = store.get_supersedes_for("new1")
    assert len(rows) == 1 and rows[0]["decided_by"] == "auto"
    # 旧 memory 已 decay 到 dim
    decay = store.conn.execute(
        "SELECT decay_state FROM memories WHERE slug = 'old1'"
    ).fetchone()[0]
    assert decay == "dim"


async def test_mid_confidence_lands_in_pending_review(store):
    _insert_memory(store.conn, "old2", "preference", title="t1")
    _insert_memory(store.conn, "new2", "preference", title="t2")
    eid = _seed_entity_with_mentions_from(
        store, name="x", type_="concept", memory_ids=["old2", "new2"]
    )

    llm = AsyncMock(return_value={
        "decision": "supersedes", "confidence": 0.7, "reason": "maybe",
    })
    out = await detect_supersedes_for_new_memory(
        store, "new2", [eid], scope_hash="s1", llm=llm,
    )
    assert len(out.pending) == 1
    assert out.applied == []
    rows = store.get_supersedes_for("new2")
    assert rows and rows[0]["decided_by"] == "digest"
    # 旧 memory 不动
    decay = store.conn.execute(
        "SELECT decay_state FROM memories WHERE slug = 'old2'"
    ).fetchone()[0]
    assert decay == "alive"


async def test_low_confidence_is_ignored(store):
    _insert_memory(store.conn, "old3", "preference")
    _insert_memory(store.conn, "new3", "preference")
    eid = _seed_entity_with_mentions_from(
        store, name="y", type_="concept", memory_ids=["old3", "new3"]
    )
    llm = AsyncMock(return_value={"decision": "supersedes", "confidence": 0.3})
    out = await detect_supersedes_for_new_memory(
        store, "new3", [eid], scope_hash="s1", llm=llm,
    )
    assert out.applied == []
    assert out.pending == []
    assert len(out.ignored) == 1
    # supersedes_chain 不入库
    assert store.get_supersedes_for("new3") == []


async def test_non_supersedes_decision_falls_to_ignored(store):
    _insert_memory(store.conn, "old4", "preference")
    _insert_memory(store.conn, "new4", "preference")
    eid = _seed_entity_with_mentions_from(
        store, name="z", type_="concept", memory_ids=["old4", "new4"]
    )
    llm = AsyncMock(return_value={
        "decision": "conflicts", "confidence": 0.99, "reason": "different idea",
    })
    out = await detect_supersedes_for_new_memory(
        store, "new4", [eid], scope_hash="s1", llm=llm,
    )
    # 即使高分但 decision != supersedes —— 不落 supersedes_chain
    assert out.applied == []
    assert out.pending == []
    assert len(out.ignored) == 1
    assert store.get_supersedes_for("new4") == []


# ---- 范围 / 过滤 --------------------------------------------------------


async def test_only_target_types_are_considered(store):
    """session 类型的旧 memory 不应触发 supersede。"""
    _insert_memory(store.conn, "sess_old", "session", title="some session")
    _insert_memory(store.conn, "pref_new", "preference", title="new")
    eid = _seed_entity_with_mentions_from(
        store, name="t", type_="concept", memory_ids=["sess_old", "pref_new"]
    )
    llm = AsyncMock(return_value={"decision": "supersedes", "confidence": 0.99})
    out = await detect_supersedes_for_new_memory(
        store, "pref_new", [eid], scope_hash="s1", llm=llm,
    )
    # sess_old 不在 SUPERSEDE_TYPES 内 → 不被纳入候选
    assert out.applied == []


async def test_different_scope_does_not_trigger(store):
    _insert_memory(store.conn, "old_a", "preference", scope_hash="scopeA")
    _insert_memory(store.conn, "new_b", "preference", scope_hash="scopeB")
    # entity 在两个 scope 都被 mention（关系上）
    eid = _seed_entity_with_mentions_from(
        store, name="cross", type_="concept",
        memory_ids=["old_a"], scope_hash="scopeA",
    )
    # 再加一条 new_b 在 scopeB 的 mentions
    store.add_relation(
        subject_id="memory:new_b", subject_kind="memory",
        predicate="mentions", object_id=eid, object_kind="entity",
        source_memory_id="new_b", confidence=0.9, scope_hash="scopeB",
    )
    llm = AsyncMock(return_value={"decision": "supersedes", "confidence": 0.99})
    out = await detect_supersedes_for_new_memory(
        store, "new_b", [eid], scope_hash="scopeB", llm=llm,
    )
    # scopeA 的 old_a 被 scope 过滤掉
    assert out.applied == []


async def test_no_old_memory_with_entity_means_no_candidates(store):
    _insert_memory(store.conn, "new_solo", "preference")
    eid = _seed_entity_with_mentions_from(
        store, name="solo", type_="concept",
        memory_ids=["new_solo"],  # 只有 new 自己
    )
    llm = AsyncMock(return_value={"decision": "supersedes", "confidence": 0.99})
    out = await detect_supersedes_for_new_memory(
        store, "new_solo", [eid], scope_hash="s1", llm=llm,
    )
    assert out.total == 0
    llm.assert_not_awaited()


async def test_missing_entity_skipped_silently(store):
    """传入不存在的 entity_id 不应崩溃，只是被忽略。"""
    _insert_memory(store.conn, "n", "preference")
    llm = AsyncMock(return_value={"decision": "supersedes", "confidence": 0.99})
    out = await detect_supersedes_for_new_memory(
        store, "n", ["entity:person:ghost"], scope_hash="s1", llm=llm,
    )
    assert out.total == 0


async def test_llm_unavailable_returns_zero_decisions(store):
    """无 LLM 注入 → 走 stub_judge → 全部 0.0 → ignored 也是 0（因为不入 supersedes-only 路径）。"""
    _insert_memory(store.conn, "old", "preference")
    _insert_memory(store.conn, "new", "preference")
    eid = _seed_entity_with_mentions_from(
        store, name="nolllm", type_="concept", memory_ids=["old", "new"]
    )
    # 不传 llm，确保 stub_judge 被用上（导入失败时的兜底）
    # 故意先 mock 掉 llm.prompts.judge_supersede 的导入
    import importlib
    import sys
    fake_mod = type(sys)("memoryd.llm.prompts")
    # 不放 judge_supersede 属性 → 触发 ImportError 路径
    sys.modules.setdefault("memoryd.llm", type(sys)("memoryd.llm"))
    sys.modules["memoryd.llm.prompts"] = fake_mod
    try:
        out = await detect_supersedes_for_new_memory(
            store, "new", [eid], scope_hash="s1", llm=None,
        )
    finally:
        sys.modules.pop("memoryd.llm.prompts", None)
    # stub_judge 返回 unrelated 0.0 → 进 ignored
    assert out.applied == []
    assert out.pending == []
    assert len(out.ignored) >= 1


async def test_llm_exception_is_handled_per_pair(store):
    _insert_memory(store.conn, "old5", "preference")
    _insert_memory(store.conn, "new5", "preference")
    eid = _seed_entity_with_mentions_from(
        store, name="boom", type_="concept", memory_ids=["old5", "new5"]
    )
    llm = AsyncMock(side_effect=RuntimeError("LLM down"))
    out = await detect_supersedes_for_new_memory(
        store, "new5", [eid], scope_hash="s1", llm=llm,
    )
    assert out.total == 0


async def test_dedupes_same_pair_across_entities(store):
    """同 (new, old) 对在多个共享 entity 上只判一次。"""
    _insert_memory(store.conn, "old", "preference")
    _insert_memory(store.conn, "new", "preference")
    e1 = _seed_entity_with_mentions_from(
        store, name="e1", type_="concept", memory_ids=["old", "new"]
    )
    e2 = _seed_entity_with_mentions_from(
        store, name="e2", type_="concept", memory_ids=["old", "new"]
    )
    llm = AsyncMock(return_value={"decision": "supersedes", "confidence": 0.95})
    out = await detect_supersedes_for_new_memory(
        store, "new", [e1, e2], scope_hash="s1", llm=llm,
    )
    # 即使两个 entity 都触发，pair (new, old) 只判一次
    assert llm.await_count == 1
    assert len(out.applied) == 1
