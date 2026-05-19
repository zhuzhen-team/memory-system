"""实体抽取 prompt — 从一段记忆文本中抽取实体与关系。

设计要点：
- 7 类实体：person / organization / place / library / tool / project / concept
- 9 种关系 predicate：works_at / lives_in / uses / built / depends_on /
  located_in / studies / collaborates_with / authored / part_of
- 输出严格 JSON，由 ExtractedEntities (Pydantic) 校验
- prompt 整体中文，避免英文偏置；模型必须保留专有名词原貌
- 参考 mem0 ADDITIVE_EXTRACTION_PROMPT 的"contextually rich"原则，但本项目
  更关心 *实体/关系 schema*，不要求重写为 narrative memory
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..base import LLMMessage

ENTITY_TYPES = (
    "person",
    "organization",
    "place",
    "library",
    "tool",
    "project",
    "concept",
)

RELATION_PREDICATES = (
    "works_at",
    "lives_in",
    "uses",
    "built",
    "depends_on",
    "located_in",
    "studies",
    "collaborates_with",
    "authored",
    "part_of",
)

EntityType = Literal[
    "person", "organization", "place", "library", "tool", "project", "concept"
]
RelationPredicate = Literal[
    "works_at",
    "lives_in",
    "uses",
    "built",
    "depends_on",
    "located_in",
    "studies",
    "collaborates_with",
    "authored",
    "part_of",
]


class Entity(BaseModel):
    name: str = Field(..., description="实体的规范名称，保留原始大小写和专有名词形态")
    type: EntityType = Field(..., description="实体类型，必须取 ENTITY_TYPES 之一")
    aliases: list[str] = Field(default_factory=list, description="同义/别名/缩写")
    confidence: float = Field(
        0.8,
        ge=0.0,
        le=1.0,
        description="抽取置信度（0-1），低于 0.5 应当不输出而非硬给值",
    )


class Relation(BaseModel):
    subject: str = Field(..., description="主语实体名（必须出现在 entities 列表）")
    predicate: RelationPredicate = Field(
        ..., description="关系谓词，必须取 RELATION_PREDICATES 之一"
    )
    object: str = Field(..., description="宾语实体名（必须出现在 entities 列表）")
    evidence: str = Field("", description="原文中支持该关系的短句（≤80 字）")
    confidence: float = Field(0.7, ge=0.0, le=1.0)


class ExtractedEntities(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)


EXTRACT_ENTITIES_SCHEMA = ExtractedEntities


EXTRACT_ENTITIES_PROMPT = """你不是在跟用户对话，你是在帮 memory-system 抽信息，输出严格 JSON。

# 角色
你是一个本地记忆系统的实体抽取器。给定一段来自用户笔记/对话/会话总结的文本，
你需要识别其中的**实体**与**关系**，并以严格 JSON 输出。

# 实体类型（7 类，必须取其中之一）
- person       —— 人物（用户本人、同事、家人、作者、维护者……）
- organization —— 公司、学校、团队、开源组织
- place        —— 城市、国家、地点、办公室
- library      —— 编程库 / 框架（如 React、Pydantic、jieba）
- tool         —— 工具 / 软件 / 平台（如 VSCode、Claude Code、Notion）
- project      —— 项目名（如 memory-system、wolin-clients-gathering）
- concept      —— 抽象概念 / 方法论 / 技术名词（如 RAG、零信任、长期记忆）

# 关系谓词（10 种，必须取其中之一）
- works_at, lives_in, uses, built, depends_on, located_in,
  studies, collaborates_with, authored, part_of

# 规则
1. **保留专有名词原貌**：不要把 "claude-haiku-4-5" 简化成 "claude"；不要把
   "memory-system" 翻译成 "记忆系统"。书名/项目名/库名一字不改。
2. **置信度自评**：confidence < 0.5 的实体/关系**不要输出**，宁缺毋滥。
3. **关系两端必须出现在 entities 列表**：不要凭空发明实体。
4. **evidence 字段**：从原文摘 ≤80 字的支持短句，不要改写。
5. **scope_hint**（如果给出）只用于消歧，不要把它作为新实体抽出来。
6. **输出严格 JSON**：不要 markdown 围栏，不要解释文字，不要键名翻译。
   顶层结构：{"entities": [...], "relations": [...]}。
7. 如果文本里没有任何可信实体，输出 `{"entities": [], "relations": []}`。

# 输出 schema（Pydantic）
{
  "entities": [
    {
      "name": "str",
      "type": "person|organization|place|library|tool|project|concept",
      "aliases": ["str", ...],
      "confidence": 0.0-1.0
    }
  ],
  "relations": [
    {
      "subject": "str",  // 必须等于某 entity.name
      "predicate": "works_at|lives_in|uses|built|depends_on|located_in|studies|collaborates_with|authored|part_of",
      "object": "str",   // 必须等于某 entity.name
      "evidence": "str (≤80 字)",
      "confidence": 0.0-1.0
    }
  ]
}
"""


def render_extract_prompt(
    memory_text: str, scope_hint: str = ""
) -> list[LLMMessage]:
    """构造一条 extract_entities 的 messages 列表。

    Args:
        memory_text: 待抽取的原文（一段记忆 / 对话片段 / session summary）。
        scope_hint: 可选 scope 提示（如 "work/memory-system"），用于消歧。
    """
    user_parts: list[str] = []
    if scope_hint:
        user_parts.append(f"# Scope 提示\n{scope_hint}")
    user_parts.append("# 待抽取文本\n" + memory_text)
    user_parts.append(
        "请按 EXTRACT_ENTITIES_PROMPT 的 schema 输出严格 JSON。"
    )
    return [
        LLMMessage(role="system", content=EXTRACT_ENTITIES_PROMPT),
        LLMMessage(role="user", content="\n\n".join(user_parts)),
    ]
