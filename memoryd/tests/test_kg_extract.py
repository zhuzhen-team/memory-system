"""extract.py 测试 —— LLM 必须 mock，jieba 兜底有条件触发。"""
from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock

import pytest

from memoryd.knowledge_graph import (
    ExtractResult,
    KnowledgeGraphStore,
    ensure_kg_schema,
    extract_entities_and_relations,
    ingest_extract_result,
    normalize_predicate,
)
from memoryd.knowledge_graph.extract import (
    ExtractedEntity,
    ExtractedRelation,
    _parse_llm_payload,
)


@pytest.fixture()
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_kg_schema(conn)
    return KnowledgeGraphStore(conn)


# ---- LLM 主路径 ---------------------------------------------------------


async def test_extract_with_mocked_llm_returns_entities_and_relations():
    fake_payload = {
        "entities": [
            {"name": "abble", "type": "person", "aliases": ["阿宝"],
             "context": "项目 owner", "confidence": 0.9},
            {"name": "memoryd", "type": "project", "confidence": 0.85},
        ],
        "relations": [
            {
                "subject": {"name": "abble", "type": "person"},
                "predicate": "works_on",
                "object": {"name": "memoryd", "type": "project"},
                "confidence": 0.88,
            }
        ],
    }
    fake_llm = AsyncMock(return_value=fake_payload)

    result = await extract_entities_and_relations(
        "abble 在开发 memoryd",
        memory_id="m1",
        scope_hash="s1",
        llm=fake_llm,
        fallback_jieba=False,
    )
    assert result.source == "llm"
    assert {e.name for e in result.entities} == {"abble", "memoryd"}
    assert len(result.relations) == 1
    rel = result.relations[0]
    assert rel.predicate == "works_on"
    assert rel.subject_id == "entity:person:abble"
    assert rel.object_id == "entity:project:memoryd"

    fake_llm.assert_awaited_once()
    kwargs = fake_llm.await_args.kwargs
    assert kwargs["memory_id"] == "m1"
    assert kwargs["scope_hash"] == "s1"


async def test_extract_skips_invalid_entities_in_payload():
    fake_payload = {
        "entities": [
            {"name": "ok", "type": "person", "confidence": 0.9},
            {"name": "", "type": "person", "confidence": 0.5},        # 无 name
            {"name": "alien", "type": "alien", "confidence": 0.9},    # 非法 type
            "not-a-dict",
        ],
        "relations": [],
    }
    fake_llm = AsyncMock(return_value=fake_payload)
    result = await extract_entities_and_relations(
        "x", "m1", "s1", llm=fake_llm, fallback_jieba=False
    )
    assert [e.name for e in result.entities] == ["ok"]


async def test_extract_clamps_confidence_into_unit_range():
    fake_payload = {
        "entities": [
            {"name": "a", "type": "person", "confidence": 3.5},
            {"name": "b", "type": "person", "confidence": -1.0},
            {"name": "c", "type": "person", "confidence": "abc"},
        ],
        "relations": [],
    }
    result = await extract_entities_and_relations(
        "x", "m1", "s1", llm=AsyncMock(return_value=fake_payload),
        fallback_jieba=False,
    )
    confs = sorted(e.confidence for e in result.entities)
    assert confs == [0.0, 0.5, 1.0]


async def test_extract_skips_invalid_relations():
    fake_payload = {
        "entities": [{"name": "a", "type": "person", "confidence": 0.9}],
        "relations": [
            # subject 类型非法
            {"subject": {"name": "a", "type": "alien"}, "predicate": "uses",
             "object": {"name": "b", "type": "tool"}, "confidence": 0.8},
            # 缺 predicate
            {"subject": {"name": "a", "type": "person"}, "predicate": "",
             "object": {"name": "b", "type": "tool"}, "confidence": 0.8},
        ],
    }
    result = await extract_entities_and_relations(
        "x", "m1", "s1", llm=AsyncMock(return_value=fake_payload),
        fallback_jieba=False,
    )
    assert result.relations == []


# ---- LLM 失败 / 不可用 → jieba 兜底 -------------------------------------


async def test_extract_falls_back_to_jieba_when_llm_raises():
    failing_llm = AsyncMock(side_effect=RuntimeError("network down"))
    result = await extract_entities_and_relations(
        "李雷在北京大学跟韩梅梅讨论问题",
        memory_id="m1",
        scope_hash="s1",
        llm=failing_llm,
        fallback_jieba=True,
    )
    assert result.source == "jieba"
    # jieba 应当至少识别出一个 person / place / organization 中的某个
    types = {e.type for e in result.entities}
    assert types & {"person", "place", "organization"}


async def test_extract_no_fallback_returns_empty_when_llm_fails():
    failing_llm = AsyncMock(side_effect=RuntimeError("boom"))
    result = await extract_entities_and_relations(
        "李雷在北京", "m1", "s1", llm=failing_llm, fallback_jieba=False
    )
    assert result.entities == []
    assert result.relations == []


async def test_extract_empty_text_returns_empty():
    result = await extract_entities_and_relations(
        "", "m1", "s1", llm=AsyncMock(return_value={"entities": [], "relations": []})
    )
    assert result.is_empty()


async def test_extract_when_llm_returns_empty_falls_back_to_jieba():
    """LLM 返回 {entities: [], relations: []} 时也会触发 jieba 兜底。"""
    empty_llm = AsyncMock(return_value={"entities": [], "relations": []})
    result = await extract_entities_and_relations(
        "李雷和韩梅梅",
        "m1", "s1",
        llm=empty_llm,
        fallback_jieba=True,
    )
    assert result.source == "jieba"


# ---- _parse_llm_payload 边界 --------------------------------------------


def test_parse_llm_payload_handles_non_dict():
    assert _parse_llm_payload("just a string") == ([], [])
    assert _parse_llm_payload(None) == ([], [])


def test_parse_llm_payload_handles_missing_keys():
    ents, rels = _parse_llm_payload({})
    assert ents == [] and rels == []


# ---- normalize_predicate ------------------------------------------------


def test_normalize_predicate_passes_canonical_through():
    assert normalize_predicate("works_on") == ("works_on", False)
    assert normalize_predicate("uses") == ("uses", False)


def test_normalize_predicate_handles_aliases():
    assert normalize_predicate("Used_By") == ("uses", True)
    assert normalize_predicate("Cited By") == ("cites", True)
    assert normalize_predicate("part_of") == ("belongs_to", False)


def test_normalize_predicate_returns_unknown_unchanged():
    canonical, reverse = normalize_predicate("admires")
    assert canonical == "admires"
    assert reverse is False


def test_normalize_predicate_empty():
    assert normalize_predicate("") == ("", False)


# ---- ingest_extract_result（落库整合） ----------------------------------


async def test_ingest_writes_entities_and_relations(store: KnowledgeGraphStore):
    fake_payload = {
        "entities": [
            {"name": "abble", "type": "person", "confidence": 0.9},
            {"name": "memoryd", "type": "project", "confidence": 0.85},
        ],
        "relations": [
            {"subject": {"name": "abble", "type": "person"},
             "predicate": "works_on",
             "object": {"name": "memoryd", "type": "project"},
             "confidence": 0.88},
        ],
    }
    result = await extract_entities_and_relations(
        "abble works on memoryd",
        memory_id="m1",
        scope_hash="s1",
        llm=AsyncMock(return_value=fake_payload),
    )
    stats = ingest_extract_result(
        store, result, source_memory_id="m1", scope_hash="s1"
    )
    assert stats.entities_added == 2
    assert stats.relations_added >= 1
    # 检查 entity 写入
    assert store.get_entity("entity:person:abble") is not None
    assert store.get_entity("entity:project:memoryd") is not None
    # 检查 works_on 关系
    works = store.get_relations(predicate="works_on")
    assert len(works) == 1
    # 检查 memory → entity 的 mentions 关系
    mentions = store.get_relations(predicate="mentions")
    # 两个 entity 各一条 mentions
    assert len(mentions) == 2


def test_ingest_normalises_reversed_predicate(store: KnowledgeGraphStore):
    """LLM 给 used_by 应当被反转成 uses(b, a)。"""
    result = ExtractResult(
        entities=[
            ExtractedEntity(name="a", type="person", confidence=0.9),
            ExtractedEntity(name="b", type="tool", confidence=0.9),
        ],
        relations=[
            ExtractedRelation(
                subject_name="a", subject_type="person",
                predicate="used_by",
                object_name="b", object_type="tool",
                confidence=0.85,
            ),
        ],
        source="llm",
    )
    ingest_extract_result(store, result, source_memory_id="m1", scope_hash=None)
    rels = store.get_relations(predicate="uses")
    assert len(rels) == 1
    # 反转后：subject = b (tool), object = a (person)
    assert rels[0].subject_id == "entity:tool:b"
    assert rels[0].object_id == "entity:person:a"


def test_ingest_drops_below_min_confidence(store: KnowledgeGraphStore):
    result = ExtractResult(
        entities=[ExtractedEntity(name="a", type="person", confidence=0.9)],
        relations=[
            ExtractedRelation(
                subject_name="a", subject_type="person",
                predicate="works_on",
                object_name="b", object_type="project",
                confidence=0.3,
            ),
        ],
        source="llm",
    )
    stats = ingest_extract_result(
        store, result, source_memory_id="m1", scope_hash=None,
        min_relation_confidence=0.5,
    )
    assert stats.relations_skipped >= 1
    assert store.get_relations(predicate="works_on") == []


def test_ingest_drops_unknown_predicates_when_requested(store: KnowledgeGraphStore):
    result = ExtractResult(
        entities=[ExtractedEntity(name="a", type="person", confidence=0.9)],
        relations=[
            ExtractedRelation(
                subject_name="a", subject_type="person",
                predicate="admires",
                object_name="b", object_type="person",
                confidence=0.9,
            ),
        ],
        source="llm",
    )
    stats = ingest_extract_result(
        store, result, source_memory_id="m1", scope_hash=None,
        drop_unknown_predicates=True,
    )
    assert stats.relations_skipped >= 1
    assert store.get_relations(predicate="admires") == []
