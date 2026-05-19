---
title: 教程 04 · 知识图谱
keywords: kg, 知识图谱, 实体, 关系, supersedes, evolution
---

# 教程 04 · 知识图谱：实体、关系、决策演化

**目标：** 看 memoryd 自动从记忆里抽实体、画关系图、追决策演化（supersedes 链），并用 CLI 反查。

**前置：** 库里至少有 10+ 条带实质内容的记忆（不只是 "hello world" 这种）。

## 它在干什么

每条记忆 capture 后，后台 `analyze-session` 会：

1. **抽实体**：LLM 抽（如果配了 LLM）/ jieba 中文分词兜底
   - 实体类型：`person / library / project / concept / company / file / ...`
2. **抽关系**：实体两两之间的 verb 三元组 `(subj, verb, obj)`
3. **检测 supersedes**：跟历史同 entity 的旧 preference / decision / fact 比对，新覆盖旧的
4. **算冲突**：同实体两条记忆陈述矛盾 → 进 `conflicts` 待人工
5. **更新画像**：mention_count + 1，last_mentioned_at = now

输出落 SQLite 的 `entities / relations / supersedes / conflicts` 四张表。

## 一、看你的"热门实体" Top N

```bash
memoryd kg entities --top=10
```

**预期输出：**

```
| rank | name        | type     | mention_count | last_mentioned_at      |
|------|-------------|----------|---------------|------------------------|
|    1 | Claude Code | tool     |            42 | 2026-05-19T...         |
|    2 | memoryd     | project  |            38 | 2026-05-20T...         |
|    3 | Solid       | library  |            12 | 2026-05-15T...         |
...
```

库里实体少时输出 `no entities`（教程开头的提示）。

## 二、按类型过滤

```bash
memoryd kg entities --type=person --top=5
```

只看人名。其他可选：`library / project / concept / company / file / ...`

## 三、反查"含某实体的所有记忆"

```bash
memoryd kg memories-about "Solid"
```

**预期输出：**

```
2026-05-15-solid-decision    decision    "决定迁移到 Solid 因为..."
2026-05-12-react-pain        warning     "React 渲染性能在大列表..."
2026-05-10-...
```

这是知识图谱最常用的反查路径，比 `search "Solid"` 更精准（不会被字符串包含的噪音污染）。

## 四、决策演化链（supersedes）

```bash
memoryd kg evolution "React"
```

**预期输出：**

```
2025-08-01: prefer React (initial)
  ↓ superseded by (confidence 0.87, LLM)
2026-03-10: consider Solid as alternative
  ↓ superseded by (confidence 0.91, LLM)
2026-05-15: migrate to Solid (current)
```

显示同一个 entity 上**思想/选择演化的时间轴**。每条边带：
- 时间戳
- LLM 给出的 confidence
- 自动 supersede（≥ 0.85）还是 manual approve（0.5–0.85 进 digest 待人审）

## 五、N-hop 子图

```bash
memoryd kg subgraph "Claude Code" --hops=2 --format=text
```

**预期输出：**

```
Claude Code
├── uses → MCP server
│   └── uses → memoryd
├── replaced → Cursor (supersedes 2026-02-...)
└── conflicts_with → Codex (1 conflict)
```

`--format=cytoscape` 输出 JSON 给 web dashboard 的 `/relations` 页面渲染（Cytoscape.js）。

## 六、看冲突

```bash
memoryd kg conflicts
```

**预期输出：**

```
[1] entity=React
    A (2026-01-01): "永远不会换 React"
    B (2026-05-15): "迁移到 Solid"
    状态: pending（confidence 0.62，进 digest 待审）
```

冲突包括：
- 两条 preference 矛盾
- 一条 decision 推翻另一条
- 同一 fact 两个不同陈述

人工审批：把 B 作为 supersede A 的 → `memoryd promote <conflict_id>`，或反之。

## 七、在 web dashboard 上可视化

```bash
memoryd web --port=18765
# 浏览器打开 .../relations
```

页面用 Cytoscape.js 渲染 N-hop 子图，鼠标拖拽节点、点节点跳记忆详情。

## 真实意义：从一堆笔记到一张图

普通笔记软件让你"按时间倒序看一堆 markdown"。memoryd 让 AI 看到的是**一张图**：

- "客户 A" 这个节点连着 5 条事实 + 3 条决策 + 1 个未解决冲突
- AI 在跟你聊 "客户 A" 时，自动调 `mem_kg_subgraph` 拉这一坨进上下文
- 你前年用 React 现在用 Solid，AI 知道 React 是 superseded 的，不会再推荐 React

这是 memoryd 跟 "搜索式记忆服务" 的核心差别。

## 你掌握了

- 实体/关系/supersedes/conflicts 四张表的来源
- `kg entities / memories-about / evolution / subgraph / conflicts` 五个查询
- supersedes 自动 vs 人审的阈值
- 为什么 "图" 比 "笔记列表" 强

## 下一步

[教程 05 · 画像自学习](profile-self-learning.md) —— LLM 周期性把这张图凝练成"未来的你看到的当下的你"。
