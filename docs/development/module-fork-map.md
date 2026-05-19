---
title: 模块来源
keywords: 来源, fork, vendor, mem0, claude-mem, memsearch, engram, license
---

# 模块来源：哪些子模块借鉴自哪些上游

memoryd 的核心理念之一是"不重复造轮子，但只取最契合的部分"。
我们 vendor 了 5 个外部仓作为研究对象 + 必要时按文件 fork 进 memoryd。
本表列每个 fork 进 memoryd 的部分 + 上游 license。

`vendor/` 子目录本身**不参与运行时**，仅作研究 / 比对副本。

## 总览

| memoryd 模块 | 上游 | License | fork 类型 |
|---|---|---|---|
| `search/scoring.py` | [mem0ai/mem0](https://github.com/mem0ai/mem0) | Apache-2.0 | 思路 + 部分函数（BM25 归一化 + entity boost 权重） |
| `llm/` 抽象 | claude-mem | MIT | 设计模式（Protocol + factory） |
| `plugins/openclaw/src/{tools,hooks}/` | memsearch openclaw plugin template | MIT | 三工具 + 两 hook 模板 |
| `plugins/claude-code/hooks/session-start.sh`（规划） | memsearch | MIT | session-start 注入思路 |
| `mcp_server.py` 19 工具命名 | [engram](https://github.com/.../engram) | MIT | tool name schema |
| 其余 | memoryd 原创 | MIT | — |

## 详细

### 1. `memoryd/search/scoring.py` ← mem0

源码：[memoryd/src/memoryd/search/scoring.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/search/scoring.py)

借鉴自 mem0 的 `scoring.py`：

- BM25 归一化函数
- entity boost 权重设计
- lemmatize 接口

我们的修改：

- 适配 Milvus Lite 的距离计算
- 不引入 NLTK / spaCy 依赖
- entity_ids 接口对齐 memoryd 的 `entity:type:slug` 格式

mem0 license（Apache-2.0）允许 fork + 改写；在文件头标注 "adapted from mem0"。

### 2. `memoryd/llm/` ← claude-mem

借鉴 claude-mem 的 LLM provider 抽象设计：

- Protocol 定义（async + generate_json）
- factory 模式
- 多 provider 切换（不绑死 Anthropic）

我们的扩展：

- 新增 Ollama provider（本地推理）
- prompt 模板单独成 module
- 不内嵌 mem0 / engram 的特殊 prompt（用我们自己的中文 prompt）

### 3. `plugins/openclaw/` ← memsearch

memsearch 上游的 OpenClaw plugin 模板提供了：

- 三个 native 工具（memory_search / memory_get / memory_transcript）
- 两个 lifecycle hook（before_agent_start / agent_end）
- definePluginEntry + registerAgentEventSubscription 用法

我们在此基础上：

- 改 memoryd_client（spawn 本地 memoryd CLI / HTTP 都支持）
- 加 fire-and-forget 错误处理（不阻塞 agent）
- 不接管 OpenClaw 原生 memory-core

### 4. `mcp_server.py` 工具命名 ← engram

engram 的 19 个 `mem_*` 工具命名清晰 + 责任划分合理：

- 7 个 memory 工具（save / update / delete / get / search / context / timeline）
- 4 个 session 工具
- 2 个 judge 工具
- 6 个 admin 工具

我们用 Python 重写（engram 是 TS）+ 接 memoryd 后端 + 用 fastmcp。

### 5. memoryd 原创

绝大部分模块是 memoryd 原创：

- **scope 派生算法**（git root → SHA256 前 12 位）
- **DURA 4 准则**评分体系
- **decay 状态机**（alive → dim → soft-forgotten → forgotten）
- **audit chain**（SHA256 prev_hash 链）
- **sensitive scope** + grant 体系
- **passphrase 跨机派生**
- **memories.json v1 schema**（兼容 mcp-memory-service v5）
- **knowledge_graph 三表设计**（entities / relations / supersedes_chain）
- **profile 自学习**（weekly identity / 月度报告 / trends）

## License 一览

memoryd 自身代码：**MIT**。

| 来源 | License | 兼容 MIT？ |
|---|---|---|
| mem0 | Apache-2.0 | 是（需保留 NOTICE） |
| claude-mem | MIT | 是 |
| memsearch | MIT | 是 |
| engram | MIT | 是 |

`vendor/` 子目录保留各上游原 license 文件。

## 不 fork 的决定

| 模块 | 上游有 | 我们不抄的原因 |
|---|---|---|
| spaCy NER | mem0 用 `en_core_web_sm` | 50MB 依赖；对中文用户无收益。我们走 LLM-first + jieba 兜底 |
| Cypher 图查询 | engram 用 cypher | 个人量级不需要；三表 SQLite + N-hop BFS 够了 |
| 多 user / RBAC | mem0 / engram 都有 | 个人单用户场景，不引入 |
| 云后端 | 几乎所有都有 | 本地优先是设计选择 |

## 增加新 fork

如果你想 fork 一段上游代码进 memoryd：

1. 把上游仓 vendor 到 `vendor/<name>/`
2. 在 memoryd 对应位置写新文件，**文件头加 fork 注释 + 上游 path + license**
3. 在本表追加一行
4. PR 描述说明 fork 范围 + 修改清单
