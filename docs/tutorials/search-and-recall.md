---
title: 教程 03 · 搜索与召回
keywords: search, 搜索, 全文, 向量, 混合, 实体加权
---

# 教程 03 · 搜索与召回：keyword / 向量 / 混合什么时候用哪个

**目标：** 理解 memoryd 搜索的三路混合实现，知道何时调哪种参数。

**前置：** 教程 02，库里已有 5+ 条记忆。

## 三路并发，加权融合

memoryd 的 `search` 一次调用同时跑三条路：

| 路 | 后端 | 擅长 | 短板 |
|---|---|---|---|
| **全文** | ripgrep + SQLite FTS5 | 准词命中（"memoryd"、"DURA"） | 同义词 / 自然语言查询差 |
| **向量** | Milvus Lite + bge-m3 ONNX | 语义近似（"记忆"匹配"我之前提过的事"） | 严格关键词反而打折 |
| **知识图谱** | entities 表实体名 + 关系 | 反查"含某实体的所有记忆" | 实体抽取漏报就找不到 |

三路各出 Top-K，按权重融合，去重，按最终 score 排序输出。

## 一、最朴素：关键词查

```bash
memoryd search "memoryd"
```

**预期输出：**

```
2026-05-20-my-first    db8435ffd199    title: 2026-05-20 会话 my-first
2026-05-19-...
...
```

每行：slug / scope_hash / 摘要片段。

## 二、加过滤参数

```bash
memoryd search "决策" --type=decision --limit=20
```

只在 type=decision 里搜，最多 20 条。

```bash
memoryd search "客户名单" --scope=db8435ffd199
```

只搜某个 scope。这在记忆量大时关键。

```bash
memoryd search "deployment" --json | jq '.[0]'
```

`--json` 给脚本用，附带 score 各分量、各路命中信息。

## 三、向量主导的语义搜索

memoryd 不暴露"只走向量"开关 —— 默认就是三路混合。但**当你的查询语义化**（多个词、有自然语言意图）时，向量路自动占主导：

```bash
memoryd search "上次我提到要换的那个工具"
```

如果你之前有过 "考虑迁移到 Solid 框架"、"放弃 React" 这种长期记忆，向量召回会命中，全文路命中可能为零。

## 四、知识图谱反查

直接按实体反查所有相关记忆：

```bash
memoryd kg memories-about "Solid"
```

**预期输出：**

```
2026-05-15-solid-decision    decision    "决定迁移到 Solid 因为..."
2026-05-12-react-pain        warning     "React 渲染性能在大列表..."
...
```

这条不走 search 三路融合，是直接走知识图谱。详细看 [教程 04 · 知识图谱](knowledge-graph.md)。

## 五、看排序为什么这样排

```bash
memoryd search "memoryd" --json | jq '.[0] | {slug, score, score_breakdown}'
```

**预期输出：**

```json
{
  "slug": "2026-05-20-my-first",
  "score": 1.42,
  "score_breakdown": {
    "fulltext": 0.85,
    "vector": 0.32,
    "entity": 0.25
  }
}
```

`score_breakdown` 告诉你三路各贡献了多少。这对调试 / 调权重 / 写 prompt 都有用。

## 六、看 dim / soft-forgotten 的旧记忆

默认搜索**自动隐藏 soft-forgotten**（90 + 30 天没召回的）。要全部看：

```bash
memoryd search "old query" --include-forgotten
```

详见 [核心概念](../getting-started/concepts.md) 中的 "decay（衰减状态机）" 节。

## 何时用哪个

| 场景 | 用 |
|---|---|
| 我知道关键词，要快速找回 | `memoryd search "<词>"` |
| 我只记得大概意思 | `memoryd search "<自然语言句子>"` |
| 我要按 type 或 scope 过滤 | 加 `--type` / `--scope` |
| 我要"和某人 / 某项目相关的全部" | `memoryd kg memories-about "<entity>"` |
| 我要 AI 主动用记忆 | 不用搜，让 CC / Codex / OpenClaw 自己调 MCP `mem_search_memory` |
| 我要肉眼浏览 | `memoryd web` 启 dashboard |

## 你掌握了

- 三路并发的混合搜索
- 通过过滤参数缩小范围
- 知识图谱反查 vs 普通搜索的边界
- 调 `--json` 看 score 拆解

## 下一步

[教程 04 · 知识图谱](knowledge-graph.md) —— 实体、关系、supersedes 决策演化。
