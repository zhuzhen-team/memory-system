"""Tests for memoryd.llm.prompts — template rendering + JSON output parsing.

The prompts module is pure rendering / Pydantic validation; no LLM calls.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from memoryd.llm import LLMMessage
from memoryd.llm.prompts import (
    CHANGE_REPORT_PROMPT,
    ENTITY_TYPES,
    EXTRACT_ENTITIES_PROMPT,
    EXTRACT_ENTITIES_SCHEMA,
    IDENTITY_MAX_CHARS,
    JUDGE_PROMPT,
    RELATION_PREDICATES,
    REWRITE_PROMPT,
    SUPERSEDE_AUTO_THRESHOLD,
    SUPERSEDE_REVIEW_THRESHOLD,
    ChangeReportOutput,
    ExtractedEntities,
    IdentityRewrite,
    SupersedeJudgment,
    classify_judgment,
    render_change_report_prompt,
    render_extract_prompt,
    render_judge_prompt,
    render_rewrite_prompt,
)


# ---------------------------------------------------------------------------
# extract_entities
# ---------------------------------------------------------------------------


def test_extract_prompt_has_chinese_system_text():
    assert "实体" in EXTRACT_ENTITIES_PROMPT
    assert "严格 JSON" in EXTRACT_ENTITIES_PROMPT
    assert "你不是在跟用户对话" in EXTRACT_ENTITIES_PROMPT


def test_extract_prompt_lists_all_seven_entity_types():
    # The 7 required types are documented in the constant tuple and prompt body.
    assert set(ENTITY_TYPES) == {
        "person",
        "organization",
        "place",
        "library",
        "tool",
        "project",
        "concept",
    }
    for t in ENTITY_TYPES:
        assert t in EXTRACT_ENTITIES_PROMPT


def test_extract_prompt_documents_all_predicates():
    assert 8 <= len(RELATION_PREDICATES) <= 12  # spec said 8-10
    for pred in RELATION_PREDICATES:
        assert pred in EXTRACT_ENTITIES_PROMPT


def test_render_extract_prompt_returns_two_messages():
    msgs = render_extract_prompt("我在使用 memory-system", scope_hint="work")
    assert len(msgs) == 2
    assert isinstance(msgs[0], LLMMessage)
    assert msgs[0].role == "system"
    assert msgs[1].role == "user"
    assert "memory-system" in msgs[1].content
    assert "work" in msgs[1].content


def test_render_extract_prompt_without_scope_hint():
    msgs = render_extract_prompt("一些文本")
    # scope 段不出现
    assert "Scope" not in msgs[1].content


def test_extract_schema_parses_valid_json():
    payload = {
        "entities": [
            {
                "name": "memory-system",
                "type": "project",
                "aliases": ["MS"],
                "confidence": 0.95,
            },
            {
                "name": "Anthropic",
                "type": "organization",
                "aliases": [],
                "confidence": 0.9,
            },
        ],
        "relations": [
            {
                "subject": "memory-system",
                "predicate": "uses",
                "object": "Anthropic",
                "evidence": "memory-system 默认走 Anthropic Claude",
                "confidence": 0.8,
            }
        ],
    }
    parsed = EXTRACT_ENTITIES_SCHEMA.model_validate(payload)
    assert len(parsed.entities) == 2
    assert parsed.entities[0].type == "project"
    assert parsed.relations[0].predicate == "uses"


def test_extract_schema_rejects_invalid_entity_type():
    bad = {
        "entities": [
            {"name": "x", "type": "not-a-type", "confidence": 0.9, "aliases": []}
        ],
        "relations": [],
    }
    with pytest.raises(ValidationError):
        EXTRACT_ENTITIES_SCHEMA.model_validate(bad)


def test_extract_schema_rejects_invalid_predicate():
    bad = {
        "entities": [
            {"name": "a", "type": "person", "confidence": 0.9, "aliases": []},
            {"name": "b", "type": "person", "confidence": 0.9, "aliases": []},
        ],
        "relations": [
            {
                "subject": "a",
                "predicate": "loves",  # not in predicate list
                "object": "b",
                "evidence": "",
                "confidence": 0.9,
            }
        ],
    }
    with pytest.raises(ValidationError):
        EXTRACT_ENTITIES_SCHEMA.model_validate(bad)


def test_extract_schema_accepts_empty_lists():
    empty = ExtractedEntities.model_validate({"entities": [], "relations": []})
    assert empty.entities == []
    assert empty.relations == []


def test_extract_schema_round_trips_through_json():
    payload = {
        "entities": [
            {"name": "Zed", "type": "tool", "aliases": [], "confidence": 0.9}
        ],
        "relations": [],
    }
    raw = json.dumps(payload)
    parsed = EXTRACT_ENTITIES_SCHEMA.model_validate_json(raw)
    assert parsed.entities[0].name == "Zed"


# ---------------------------------------------------------------------------
# judge_supersedes
# ---------------------------------------------------------------------------


def test_judge_prompt_has_chinese_role_block():
    assert "记忆" in JUDGE_PROMPT
    assert "替代" in JUDGE_PROMPT or "supersede" in JUDGE_PROMPT.lower()
    assert "你不是在跟用户对话" in JUDGE_PROMPT


def test_render_judge_prompt_includes_ids_and_text():
    msgs = render_judge_prompt(
        old_id="m-123",
        old_text="用户在腾讯工作",
        new_fact="用户加入了字节跳动",
        scope_hint="work",
    )
    assert len(msgs) == 2
    assert "m-123" in msgs[1].content
    assert "腾讯" in msgs[1].content
    assert "字节" in msgs[1].content


def test_supersede_thresholds_make_sense():
    assert 0 < SUPERSEDE_REVIEW_THRESHOLD < SUPERSEDE_AUTO_THRESHOLD < 1
    assert SUPERSEDE_AUTO_THRESHOLD == 0.85
    assert SUPERSEDE_REVIEW_THRESHOLD == 0.50


def test_supersede_judgment_parses_valid_json():
    j = SupersedeJudgment.model_validate(
        {
            "candidate_old_id": "m-1",
            "is_superseded": True,
            "confidence": 0.92,
            "reason": "新事实直接覆盖旧事实",
        }
    )
    assert j.is_superseded is True
    assert j.confidence == pytest.approx(0.92)


def test_supersede_judgment_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        SupersedeJudgment.model_validate(
            {
                "candidate_old_id": "m-1",
                "is_superseded": True,
                "confidence": 1.5,
                "reason": "x",
            }
        )


def test_classify_judgment_buckets():
    auto = SupersedeJudgment(
        candidate_old_id="x", is_superseded=True, confidence=0.9, reason="r"
    )
    review = SupersedeJudgment(
        candidate_old_id="x", is_superseded=True, confidence=0.7, reason="r"
    )
    ignore_low = SupersedeJudgment(
        candidate_old_id="x", is_superseded=True, confidence=0.3, reason="r"
    )
    ignore_false = SupersedeJudgment(
        candidate_old_id="x", is_superseded=False, confidence=0.99, reason="r"
    )
    assert classify_judgment(auto) == "auto"
    assert classify_judgment(review) == "review"
    assert classify_judgment(ignore_low) == "ignore"
    assert classify_judgment(ignore_false) == "ignore"


# ---------------------------------------------------------------------------
# rewrite_identity
# ---------------------------------------------------------------------------


def test_rewrite_prompt_announces_identity_max_chars():
    assert REWRITE_PROMPT.find(str(IDENTITY_MAX_CHARS)) != -1
    assert IDENTITY_MAX_CHARS == 800


def test_render_rewrite_prompt_carries_previous_and_facts():
    msgs = render_rewrite_prompt(
        previous_md="# 用户档案\n喜欢 Python",
        new_facts=["开始用 Rust", "切换编辑器到 Zed"],
        period_label="本周",
    )
    assert len(msgs) == 2
    assert "Python" in msgs[1].content
    assert "Rust" in msgs[1].content
    assert "Zed" in msgs[1].content
    assert "本周" in msgs[1].content


def test_render_rewrite_prompt_handles_empty_facts():
    msgs = render_rewrite_prompt(previous_md="", new_facts=[])
    assert "无新增" in msgs[1].content or "无" in msgs[1].content


def test_identity_rewrite_schema_parses_valid_json():
    payload = {
        "new_profile_md": "# 用户档案\n## 偏好\n用户喜欢 Rust",
        "diff_summary": "本周从 Python 切换到 Rust",
    }
    out = IdentityRewrite.model_validate(payload)
    assert "Rust" in out.new_profile_md
    assert out.diff_summary.startswith("本周")


# ---------------------------------------------------------------------------
# profile_change_report
# ---------------------------------------------------------------------------


def test_change_report_prompt_lists_required_sections():
    for section in ("主要变化", "新增实体", "退场实体", "supersede"):
        assert section in CHANGE_REPORT_PROMPT


def test_render_change_report_with_data():
    msgs = render_change_report_prompt(
        versions=[
            {"ts": "2026-05-01", "diff_summary": "切换到 Zed"},
            {"ts": "2026-05-15", "diff_summary": "新增 wolin 项目"},
        ],
        supersedes=[
            {
                "old": "用户在腾讯",
                "new": "用户在字节",
                "confidence": 0.92,
                "reason": "明确离职",
            }
        ],
        entity_changes=[
            {
                "name": "wolin",
                "type": "project",
                "change": "appeared",
                "reason": "新启动",
            },
            {
                "name": "VSCode",
                "type": "tool",
                "change": "disappeared",
                "reason": "已切换至 Zed",
            },
        ],
        period_label="2026 年 5 月",
    )
    assert len(msgs) == 2
    text = msgs[1].content
    assert "2026 年 5 月" in text
    assert "wolin" in text
    assert "VSCode" in text
    assert "腾讯" in text and "字节" in text


def test_render_change_report_with_empty_lists_produces_placeholders():
    msgs = render_change_report_prompt(
        versions=[],
        supersedes=[],
        entity_changes=[],
    )
    text = msgs[1].content
    # Each section should still show a 无 placeholder rather than crash.
    assert "无版本变更" in text
    assert "无 supersede" in text
    assert "无实体变化" in text


def test_change_report_output_schema_validates():
    out = ChangeReportOutput.model_validate(
        {"report_md": "# 月报\n...", "headline": "本月切换至 Zed"}
    )
    assert out.headline.startswith("本月")
