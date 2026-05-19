"""月度 profile 变化报告 prompt。

输入：
- versions: identity.md 的版本列表（含时间戳 + diff_summary）
- supersedes: 本月 supersede 事件列表
- entity_changes: 本月实体出现/退场记录

输出 markdown 报告，包含小节：主要变化 / 新增实体 / 退场实体 / supersede 事件。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..base import LLMMessage


class ChangeReportOutput(BaseModel):
    report_md: str = Field(
        ...,
        description="markdown 格式的月度变化报告（≤1500 字）",
    )
    headline: str = Field(
        ...,
        description="一句话标题，用于 digest 通知预览（≤40 字）",
    )


CHANGE_REPORT_PROMPT = """你不是在跟用户对话，你是在帮 memory-system 生成月度 profile 变化报告，输出严格 JSON。

# 角色
你是一个**记忆系统的月报记者**。给定本月的 identity.md 版本演进、supersede
事件、以及实体出现/退场记录，写一份**结构化中文 markdown 月报**给用户自己看。

# 报告结构（强制小节，缺数据的小节写 "本月无显著变化"）
```
# {period_label} 记忆月报

## 主要变化
（2-4 条，每条 ≤30 字，挑最值得用户注意的变化）

## 新增实体
- 实体名 (类型) — 一句话解释

## 退场实体
- 实体名 (类型) — 一句话解释为什么淡出

## supersede 事件
- 旧: ... → 新: ... (confidence X.XX)
```

# 写作要求
1. **markdown 输出**，整体 ≤1500 字（包括标记符号）。
2. **保留专有名词原貌**，技术栈名、项目名一字不改。
3. "主要变化" 要写人话，不是事件列表罗列；可以做归纳概括。
4. "新增实体" / "退场实体" 至多各列 8 条，多的合并描述。
5. supersede 事件至多列 6 条，从 confidence 高到低排序。
6. `headline` 字段：一句话总结本月最大变化，≤40 字，面向用户。
7. **不要编造数据**：输入里没有的不要补。
8. 不要写 "感谢使用 memory-system" 这种 fluff。

# 输出 schema（Pydantic）
{
  "report_md": "str (markdown, ≤1500 字)",
  "headline": "str (≤40 字)"
}

# 输出格式
严格 JSON，不要 markdown 围栏包住整个 JSON，不要解释。
"""


def render_change_report_prompt(
    versions: list[Any],
    supersedes: list[Any],
    entity_changes: list[Any],
    *,
    period_label: str = "本月",
) -> list[LLMMessage]:
    """构造 profile_change_report 的 messages 列表。

    Args:
        versions: identity.md 版本演进，每项建议含 ``{"ts","diff_summary"}``。
        supersedes: supersede 事件列表，建议 ``{"old","new","confidence","reason"}``。
        entity_changes: 实体变化列表，建议 ``{"name","type","change","reason"}``，
            其中 ``change ∈ {"appeared","disappeared"}``。
        period_label: 时间窗描述。
    """

    def _fmt_versions(items: list[Any]) -> str:
        if not items:
            return "（无版本变更）"
        lines: list[str] = []
        for v in items:
            if isinstance(v, dict):
                ts = v.get("ts") or v.get("timestamp") or "?"
                summary = v.get("diff_summary") or v.get("summary") or ""
                lines.append(f"- {ts}: {summary}")
            else:
                lines.append(f"- {v}")
        return "\n".join(lines)

    def _fmt_supersedes(items: list[Any]) -> str:
        if not items:
            return "（无 supersede 事件）"
        lines = []
        for s in items:
            if isinstance(s, dict):
                old = s.get("old") or s.get("old_text") or ""
                new = s.get("new") or s.get("new_fact") or ""
                conf = s.get("confidence", 0.0)
                reason = s.get("reason", "")
                lines.append(
                    f"- 旧: {old} → 新: {new} (confidence {conf:.2f}) — {reason}"
                )
            else:
                lines.append(f"- {s}")
        return "\n".join(lines)

    def _fmt_entities(items: list[Any]) -> str:
        if not items:
            return "（无实体变化）"
        lines = []
        for e in items:
            if isinstance(e, dict):
                name = e.get("name", "?")
                etype = e.get("type", "?")
                change = e.get("change", "?")
                reason = e.get("reason", "")
                lines.append(f"- {name} ({etype}) [{change}] — {reason}")
            else:
                lines.append(f"- {e}")
        return "\n".join(lines)

    user = (
        f"# {period_label}时间窗\n"
        f"\n## identity.md 版本演进\n{_fmt_versions(versions)}\n"
        f"\n## supersede 事件\n{_fmt_supersedes(supersedes)}\n"
        f"\n## 实体变化\n{_fmt_entities(entity_changes)}\n\n"
        f"请按 schema 输出严格 JSON，period_label = \"{period_label}\"。"
    )
    return [
        LLMMessage(role="system", content=CHANGE_REPORT_PROMPT),
        LLMMessage(role="user", content=user),
    ]
