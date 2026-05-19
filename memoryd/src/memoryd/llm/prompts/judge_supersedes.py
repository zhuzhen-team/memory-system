"""判断新 fact 是否替代旧 fact 的 prompt。

借鉴 engram 的 mem_judge 思路：
- 输入：candidate_old (旧 memory) + new_fact (新抽取的 fact)
- 输出：is_superseded + confidence (0-1) + reason

阈值约定（由 governance 层使用，不在 prompt 里硬编码）：
- confidence ≥ SUPERSEDE_AUTO_THRESHOLD (0.85)：自动触发 supersede
- SUPERSEDE_REVIEW_THRESHOLD (0.50) ≤ confidence < 0.85：进 digest 等用户审批
- confidence < SUPERSEDE_REVIEW_THRESHOLD：丢弃，不替代
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..base import LLMMessage

SUPERSEDE_AUTO_THRESHOLD = 0.85
SUPERSEDE_REVIEW_THRESHOLD = 0.50

JudgmentBand = Literal["auto", "review", "ignore"]


class SupersedeJudgment(BaseModel):
    candidate_old_id: str = Field(..., description="被评估的旧 memory id")
    is_superseded: bool = Field(
        ...,
        description="新 fact 是否使旧 memory 失效或被替换",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="判定置信度（0-1）",
    )
    reason: str = Field(..., description="一句话说明为什么，引用证据短句")


def classify_judgment(j: SupersedeJudgment) -> JudgmentBand:
    """根据 confidence 把判断分桶：auto / review / ignore。"""
    if not j.is_superseded:
        return "ignore"
    if j.confidence >= SUPERSEDE_AUTO_THRESHOLD:
        return "auto"
    if j.confidence >= SUPERSEDE_REVIEW_THRESHOLD:
        return "review"
    return "ignore"


JUDGE_PROMPT = """你不是在跟用户对话，你是在帮 memory-system 判定记忆替代关系，输出严格 JSON。

# 角色
你是一个记忆生命周期裁判。给定一条**旧 memory** 和一条**新 fact**，判断新
fact 是否"取代"旧 memory —— 即旧 memory 不再准确、被新信息覆盖、或描述的状态
已经改变。

# 判定原则
1. **同一实体**的状态变化通常构成 supersede：
   - 旧："用户在腾讯工作" + 新："用户离开腾讯，加入字节" → is_superseded=true
   - 旧："用户最喜欢的编辑器是 VSCode" + 新："用户切换到了 Zed" → true
2. **互补信息**不是 supersede：
   - 旧："用户有一只猫" + 新："用户还有一只狗" → is_superseded=false
3. **同义改写**（说法不同但事实一致）也**不是** supersede（应由去重处理）：
   - 旧："用户在北京" + 新："用户住在 Beijing" → false (confidence 应较低)
4. **临时状态 vs 持久事实**：临时性新事实不应推翻持久事实。
   - 旧："用户的工作语言是 Python" + 新："今天在用 Rust 写一个小脚本" → false
5. **置信度 confidence**：
   - 0.85+ : 几乎确信 supersede（强冲突，措辞明确）
   - 0.50–0.85 : 有 supersede 嫌疑但需人审（含糊措辞）
   - <0.50 : 大概率不替代（同义或无关）

# 输出 schema（Pydantic）
{
  "candidate_old_id": "str",   // 原样回填输入里的 old_id
  "is_superseded": true|false,
  "confidence": 0.0-1.0,
  "reason": "一句话理由（≤80 字），引用关键证据"
}

# 输出格式
严格 JSON，不要 markdown 围栏，不要解释，不要补字段。
"""


def render_judge_prompt(
    *,
    old_id: str,
    old_text: str,
    new_fact: str,
    scope_hint: str = "",
) -> list[LLMMessage]:
    """构造 judge_supersedes 的 messages 列表。"""
    user_parts: list[str] = []
    if scope_hint:
        user_parts.append(f"# Scope\n{scope_hint}")
    user_parts.append(f"# 旧 memory (id = {old_id})\n{old_text}")
    user_parts.append(f"# 新 fact\n{new_fact}")
    user_parts.append(
        "请回填 candidate_old_id = " + old_id + " 并输出严格 JSON。"
    )
    return [
        LLMMessage(role="system", content=JUDGE_PROMPT),
        LLMMessage(role="user", content="\n\n".join(user_parts)),
    ]
