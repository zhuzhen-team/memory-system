"""LLM prompt templates for memoryd's memory pipeline.

Each submodule exports:
- a Pydantic schema describing the expected JSON output
- a system + user prompt template (Chinese)
- a ``render_*`` function that produces a ``list[LLMMessage]`` ready to feed
  into ``LLMProvider.generate_json``.

All prompts emphasize: "你不是在跟用户对话，你是在帮 memory-system 抽信息，
输出严格 JSON"。
"""
from .extract_entities import (
    EXTRACT_ENTITIES_PROMPT,
    EXTRACT_ENTITIES_SCHEMA,
    ENTITY_TYPES,
    RELATION_PREDICATES,
    ExtractedEntities,
    render_extract_prompt,
)
from .judge_supersedes import (
    JUDGE_PROMPT,
    SUPERSEDE_AUTO_THRESHOLD,
    SUPERSEDE_REVIEW_THRESHOLD,
    SupersedeJudgment,
    classify_judgment,
    render_judge_prompt,
)
from .profile_change_report import (
    CHANGE_REPORT_PROMPT,
    ChangeReportOutput,
    render_change_report_prompt,
)
from .rewrite_identity import (
    REWRITE_PROMPT,
    IDENTITY_MAX_CHARS,
    IdentityRewrite,
    render_rewrite_prompt,
)

__all__ = [
    "EXTRACT_ENTITIES_PROMPT",
    "EXTRACT_ENTITIES_SCHEMA",
    "ENTITY_TYPES",
    "RELATION_PREDICATES",
    "ExtractedEntities",
    "render_extract_prompt",
    "JUDGE_PROMPT",
    "SUPERSEDE_AUTO_THRESHOLD",
    "SUPERSEDE_REVIEW_THRESHOLD",
    "SupersedeJudgment",
    "classify_judgment",
    "render_judge_prompt",
    "CHANGE_REPORT_PROMPT",
    "ChangeReportOutput",
    "render_change_report_prompt",
    "REWRITE_PROMPT",
    "IDENTITY_MAX_CHARS",
    "IdentityRewrite",
    "render_rewrite_prompt",
]
