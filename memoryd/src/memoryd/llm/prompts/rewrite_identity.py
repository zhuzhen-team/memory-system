"""每周重写 identity.md 的 prompt。

输入：上一版 identity.md + 本周新增/变化的 facts（list）。
输出：new_profile_md (≤ IDENTITY_MAX_CHARS 字) + diff_summary (1-2 句)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..base import LLMMessage

IDENTITY_MAX_CHARS = 800


class IdentityRewrite(BaseModel):
    new_profile_md: str = Field(
        ...,
        description=f"重写后的 identity.md，markdown 格式，≤ {IDENTITY_MAX_CHARS} 字",
    )
    diff_summary: str = Field(
        ...,
        description="一两句话总结本周变化（≤120 字）",
    )


REWRITE_PROMPT = f"""你不是在跟用户对话，你是在帮 memory-system 重写用户 profile，输出严格 JSON。

# 角色
你是一个**长期记忆策展人**。每周一次，你拿到（a）上一版 identity.md 和（b）
本周新增/变更的 facts 列表，需要重写一份**精炼、最新、不超过 {IDENTITY_MAX_CHARS} 字**
的 identity.md。

# 写作要求
1. 输出 **markdown**，结构清晰，建议小节：
   - `# 用户档案`
   - `## 身份 / 角色`
   - `## 技术栈与偏好`
   - `## 当前项目`
   - `## 工作方式与沟通偏好`
   （没有信息的小节直接省略，不要写 "暂无"。）
2. 总长度 ≤ {IDENTITY_MAX_CHARS} 中文字符（包含 markdown 标记）。
3. **保留专有名词原貌**：项目名、技术栈名、公司名一字不改。
4. **新 fact 与旧 profile 冲突时优先采用新 fact**；保留没有被新 fact 否定的
   旧条目。
5. **不要编造**：facts 里没有的事实绝对不要补。
6. 第一人称视角写"用户偏好 X"或第三人称"用户使用 X"均可，**全文保持一致**。
7. `diff_summary` 字段：1-2 句话总结**本周相较上版**最关键的变化（≤120 字），
   面向用户自己读，写人话不要写 changelog。

# 输出 schema（Pydantic）
{{
  "new_profile_md": "str (markdown, ≤ {IDENTITY_MAX_CHARS} 字)",
  "diff_summary": "str (≤120 字)"
}}

# 输出格式
严格 JSON，不要 markdown 围栏包住整个 JSON，不要解释。
"""


def render_rewrite_prompt(
    *,
    previous_md: str,
    new_facts: list[str],
    period_label: str = "本周",
) -> list[LLMMessage]:
    """构造 rewrite_identity 的 messages 列表。

    Args:
        previous_md: 上一版 identity.md 文本。
        new_facts: 本周新增/变更的 fact 字符串列表。
        period_label: 时间窗描述，默认 "本周"，可传 "近一个月" 等。
    """
    facts_block = (
        "\n".join(f"- {f}" for f in new_facts)
        if new_facts
        else "（无新增 facts）"
    )
    user = (
        f"# 上一版 identity.md\n```\n{previous_md or '（空）'}\n```\n\n"
        f"# {period_label}新增/变更 facts\n{facts_block}\n\n"
        f"请按 schema 重写并输出严格 JSON。new_profile_md 不得超过 {IDENTITY_MAX_CHARS} 字。"
    )
    return [
        LLMMessage(role="system", content=REWRITE_PROMPT),
        LLMMessage(role="user", content=user),
    ]
