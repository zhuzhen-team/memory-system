"""实体 + 关系抽取。

策略（plan10 锁定）：
- **LLM-first**：默认调 ``memoryd.llm.prompts.extract_entities`` —— 由 sub-agent B
  实现的 async 接口，返回结构化 ``{entities: [...], relations: [...]}``。
- **jieba 兜底**：LLM 不可用（无网络 / 无 API key / 调用异常）时退回 jieba 词性
  标注，仅产出 person / organization / place 等实体；不抽关系。
- ``jieba`` 在函数内 import，避免冷启动慢。

调用约定（sub-agent B 的契约，本模块按此 mock）：

.. code-block:: python

    from memoryd.llm.prompts import extract_entities

    result: dict = await extract_entities(
        text=memory_text,
        scope_hash=scope_hash,
        memory_id=memory_id,
    )

    # result schema:
    # {
    #   "entities": [
    #     {"name": "abble", "type": "person",
    #      "aliases": ["阿宝"], "context": "...",
    #      "confidence": 0.92},
    #     ...
    #   ],
    #   "relations": [
    #     {"subject": {"name": "abble", "type": "person"},
    #      "predicate": "works_on",
    #      "object": {"name": "memoryd", "type": "project"},
    #      "confidence": 0.85},
    #     ...
    #   ]
    # }
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .store import ALLOWED_PREDICATES, ENTITY_TYPES, make_entity_id


_log = logging.getLogger(__name__)


@dataclass
class ExtractedEntity:
    name: str
    type: str
    aliases: list[str] = field(default_factory=list)
    context: str = ""
    confidence: float = 0.5

    @property
    def id(self) -> str:
        return make_entity_id(self.type, self.name)


@dataclass
class ExtractedRelation:
    subject_name: str
    subject_type: str
    predicate: str
    object_name: str
    object_type: str
    confidence: float = 0.5

    @property
    def subject_id(self) -> str:
        return make_entity_id(self.subject_type, self.subject_name)

    @property
    def object_id(self) -> str:
        return make_entity_id(self.object_type, self.object_name)


@dataclass
class ExtractResult:
    entities: list[ExtractedEntity]
    relations: list[ExtractedRelation]
    source: str  # 'llm' / 'jieba' / 'mixed'

    def is_empty(self) -> bool:
        return not self.entities and not self.relations


# ---- jieba 兜底 ---------------------------------------------------------

# jieba POS tag -> 我们的 7 类映射
_JIEBA_POS_MAP = {
    "nr": "person",        # 人名
    "nrfg": "person",      # 古汉语人名
    "nrt": "person",       # 音译人名
    "ns": "place",         # 地名
    "nt": "organization",  # 机构
    "nz": "concept",       # 其他专名（项目 / 概念 / 工具）
}


def _jieba_fallback(text: str) -> list[ExtractedEntity]:
    """无 LLM 时的兜底实体识别。函数内 import jieba，启动不付出代价。"""
    try:
        import jieba.posseg as pseg  # 重型，延迟 import
    except ImportError:
        _log.warning("jieba not installed; entity extraction fully disabled")
        return []

    out: list[ExtractedEntity] = []
    seen: set[tuple[str, str]] = set()
    for word, flag in pseg.cut(text):
        word = word.strip()
        if not word or len(word) < 2:
            continue
        mapped = _JIEBA_POS_MAP.get(flag)
        if mapped is None:
            continue
        key = (word.lower(), mapped)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ExtractedEntity(
                name=word,
                type=mapped,
                aliases=[],
                context=text[:200],
                confidence=0.4,  # 兜底信度固定偏低
            )
        )
    return out


# ---- LLM 主路径 ---------------------------------------------------------


def _parse_llm_payload(payload: object) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
    """把 LLM 返回的 dict 解析成 dataclass 列表。容错——异常字段丢弃不抛错。"""
    if not isinstance(payload, dict):
        return [], []

    entities: list[ExtractedEntity] = []
    for raw in payload.get("entities") or []:
        if not isinstance(raw, dict):
            continue
        name = (raw.get("name") or "").strip()
        type_ = (raw.get("type") or "").strip().lower()
        if not name or type_ not in ENTITY_TYPES:
            continue
        aliases_raw = raw.get("aliases") or []
        aliases = [str(a) for a in aliases_raw if isinstance(a, (str, int, float))] if isinstance(aliases_raw, list) else []
        conf = raw.get("confidence", 0.5)
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            conf = 0.5
        entities.append(
            ExtractedEntity(
                name=name,
                type=type_,
                aliases=aliases,
                context=str(raw.get("context") or "")[:500],
                confidence=conf,
            )
        )

    relations: list[ExtractedRelation] = []
    for raw in payload.get("relations") or []:
        if not isinstance(raw, dict):
            continue
        subj = raw.get("subject") or {}
        obj = raw.get("object") or {}
        if not isinstance(subj, dict) or not isinstance(obj, dict):
            continue
        s_name = (subj.get("name") or "").strip()
        s_type = (subj.get("type") or "").strip().lower()
        o_name = (obj.get("name") or "").strip()
        o_type = (obj.get("type") or "").strip().lower()
        predicate = (raw.get("predicate") or "").strip().lower()
        if not (s_name and o_name and predicate):
            continue
        if s_type not in ENTITY_TYPES or o_type not in ENTITY_TYPES:
            continue
        # predicate 不在白名单时记录但不阻断（保留未来扩展空间）
        if predicate not in ALLOWED_PREDICATES:
            _log.debug("unknown predicate from LLM: %r", predicate)
        conf = raw.get("confidence", 0.5)
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            conf = 0.5
        relations.append(
            ExtractedRelation(
                subject_name=s_name,
                subject_type=s_type,
                predicate=predicate,
                object_name=o_name,
                object_type=o_type,
                confidence=conf,
            )
        )

    return entities, relations


async def extract_entities_and_relations(
    memory_text: str,
    memory_id: str,
    scope_hash: str,
    *,
    llm=None,
    fallback_jieba: bool = True,
) -> ExtractResult:
    """LLM 主，jieba 兜底。

    Parameters
    ----------
    memory_text:
        待抽取的 markdown 正文（已截断 / 清洗）。
    memory_id:
        触发抽取的 memory slug；只透传给 LLM，本函数不直接用。
    scope_hash:
        透传给 LLM 作为上下文。
    llm:
        可注入的 LLM caller（测试用）。签名应当是 ``async (text, memory_id, scope_hash) -> dict``。
        ``None`` 时尝试 ``from memoryd.llm.prompts import extract_entities``。
    fallback_jieba:
        LLM 失败 / 不可用时是否退回 jieba（默认 True）。

    Returns
    -------
    ExtractResult
        ``source='llm'`` 表 LLM 成功；``'jieba'`` 表兜底；``'mixed'`` 表 LLM 部分
        成功 + jieba 补齐（实际目前不会进 mixed，留作未来扩展）。
    """
    text = memory_text or ""
    if not text.strip():
        return ExtractResult(entities=[], relations=[], source="jieba")

    payload: object | None = None
    llm_error: Exception | None = None

    callable_llm = llm
    if callable_llm is None:
        try:
            # sub-agent B 实现的 prompt 模块
            from memoryd.llm.prompts import extract_entities as callable_llm  # type: ignore[attr-defined,no-redef]
        except Exception as e:  # ImportError + 任何 import 期异常
            callable_llm = None
            llm_error = e
            _log.debug("memoryd.llm.prompts.extract_entities unavailable: %s", e)

    if callable_llm is not None:
        try:
            payload = await callable_llm(
                text=text,
                memory_id=memory_id,
                scope_hash=scope_hash,
            )
        except Exception as e:
            llm_error = e
            _log.warning("LLM extract failed: %s — falling back", e)
            payload = None

    if payload is not None:
        entities, relations = _parse_llm_payload(payload)
        if entities or relations:
            return ExtractResult(entities=entities, relations=relations, source="llm")
        # payload 解析为空 → 尝试兜底

    if fallback_jieba:
        ents = _jieba_fallback(text)
        return ExtractResult(entities=ents, relations=[], source="jieba")

    # 不兜底 → 返回空（保留 llm_error 信息用于上层日志）
    if llm_error is not None:
        _log.info("entity extraction returning empty (no LLM, no fallback)")
    return ExtractResult(entities=[], relations=[], source="llm")


__all__ = [
    "ExtractResult",
    "ExtractedEntity",
    "ExtractedRelation",
    "extract_entities_and_relations",
]
