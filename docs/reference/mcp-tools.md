---
title: MCP 工具
keywords: MCP, mem_save, mem_search, mem_review_pending, fastmcp, 22 个工具
---

# MCP 工具：22 个 `mem_*` 工具签名 + 示例

memoryd 的 MCP server 暴露 22 个 `mem_*` 工具：

- **16 个 agent-tier** —— 默认对所有客户端可见（含 3 个 promotion-review 工具，让 CC 内一句话过 pending）
- **6 个 admin-tier** —— 仅 `MEMORYD_MCP_ADMIN=1` 或 `--admin` 时注册

## Promotion-review（3 个，让 CC 在会话里直接审批 pending）

| 工具 | 签名 | 用途 |
|---|---|---|
| `mem_review_pending` | `(scope="global", limit=10, min_score=0.0, max_score=1.0, types=None)` | 列待审批 promotions，DURA avg 升序（最不确定的先），含 reasoning |
| `mem_promote` | `(promotion_ids=None, auto_high=False, threshold=0.85, scope="global")` | 批一个/多个/或全部高分（DURA≥threshold）。`scope` 参数让 auto_high 限定到具体 scope |
| `mem_reject` | `(promotion_ids=[...])` | 拒绝 promotion(s)，flips status='rejected'（不写 .md，保留 audit） |

典型 CC 内对话：

```
你: "过一遍 pending"
CC: [mem_review_pending(limit=10)] "你有 X 条灰区，最低分 #87 (avg 0.65)..."
你: "批 87 89，拒 88"
CC: [mem_promote(ids=[87,89]) + mem_reject(ids=[88])]
你: "剩下的全批高分"
CC: [mem_promote(auto_high=True, threshold=0.85)]
```

源码：

- [memoryd/src/memoryd/mcp_server.py](https://github.com/EthanQC/memory-system/blob/main/memoryd/src/memoryd/mcp_server.py) —— 入口 + tool 注册
- [memoryd/src/memoryd/mcp_tools/](https://github.com/EthanQC/memory-system/tree/main/memoryd/src/memoryd/mcp_tools) —— 处理函数

## 启动

```bash
memoryd-mcp                        # stdio
memoryd-mcp --transport http --port 8766
MEMORYD_MCP_ADMIN=1 memoryd-mcp    # 启用 admin 工具
```

## 接到 Claude Code

见 [安装](../getting-started/installation.md) 第五步：编辑 `~/.claude.json` 的 `mcpServers.memoryd`。

## 接到 Codex

`~/.codex/config.toml`：

```toml
[mcp.memoryd]
command = "/Users/abble/memory-system/memoryd/.venv/bin/memoryd-mcp"
args = []
```

## 接到 OpenClaw

OpenClaw 通常用 native plugin 而非 MCP（直接调本地 memoryd CLI / HTTP）。
但如果需要走 MCP：参考 OpenClaw 的 MCP 配置文档。

---

## Memory 工具（7 个，agent tier）

### `mem_save`

写一条 memory。`type` 是 `session / decision / preference / fact / playbook / warning`。
`scope="auto"` 从 cwd 派生（git root）。

```python
result = await mem_save(
    content="决定从 React 切到 Solid，理由是性能+体积",
    type="decision",
    scope="auto",
    tags=["frontend"],
    triggers=["solid", "react", "切换"],
    title="2026-05-12 前端切换决策",
)
# → {"memory_id": "2026-05-12-...", "path": "...", "status": "saved"}
```

### `mem_update`

按 memory_id 在原位 patch body / tags / triggers / title。

```python
await mem_update(
    memory_id="2026-05-12-...",
    content="...",       # 可选
    tags=[...],          # 可选
    triggers=[...],      # 可选
    title="...",         # 可选
)
```

### `mem_delete`

删除 memory（unlink .md + 删 SQLite 行）。

```python
await mem_delete(memory_id="2026-05-12-...")
```

### `mem_get`

取完整 memory（row + raw markdown body）。

```python
await mem_get(memory_id="2026-05-12-...")
# → {"memory_id": "...", "type": "decision", "scope_hash": "...",
#    "frontmatter": {...}, "body": "..."}
```

### `mem_search`

Hybrid 搜索（ripgrep × Milvus × 实体加权）。

```python
await mem_search(
    query="abble 切 Solid 决策",
    scope="auto",
    top_k=10,
    types=["decision","preference"],   # 可选 post-filter
    entity_ids=["entity:library:Solid"],  # 可选，加权命中
)
# → {"results": [{memory_id, score, content_preview, ...}, ...]}
```

### `mem_context`

返回 memory_id 在同 scope 时序相邻的记忆（前后各 N 条）。

```python
await mem_context(memory_id="2026-05-12-...", neighbors=3)
# → {"before": [...], "after": [...], "anchor": {...}}
```

### `mem_timeline`

scope 内按时间倒序，自动排除 soft-forgotten。

```python
await mem_timeline(
    scope="auto",
    since="30d",     # 30d / 2w / 6m / 1y
    types=["decision"],
    limit=100,
)
```

## Session 工具（4 个，agent tier）

### `mem_session_start`

开一个 session memory 占位，返回 id。每个 CC / Codex / OpenClaw 会话开始时调一次，
之后的 `session_end` / `session_summary` 都用这个 id。

```python
sid = await mem_session_start(scope="auto", source="manual", title="...")
# → {"session_id": "2026-05-19-..."}
```

### `mem_session_end`

给 session 追加 summary 并标记关闭。幂等（重复调不会重写 summary）。

```python
await mem_session_end(session_id=sid["session_id"], summary="...")
```

### `mem_session_summary`

取 session 的 body 原 markdown。

```python
await mem_session_summary(session_id=sid["session_id"])
```

### `mem_capture_passive`

直接写一条 long-term memory（fact / decision / preference / playbook / warning），
**跳过 DURA promotion gate**。给三端 mirror 用，记录观察到的用户行为。

```python
await mem_capture_passive(
    content="abble 偏好 autonomous + 中文汇总",
    source="openclaw",
    scope="auto",
    type="preference",
    tags=["meta"],
    triggers=["autonomous", "中文"],
)
```

## Judge 工具（2 个，agent tier）

### `mem_judge`

LLM 判定 `new_text` 是否 supersede 旧的 `old_memory_id`。返回 `SupersedeJudgment + band(auto/review/ignore)`。
**不自动 apply**——调用方自己决定。

```python
verdict = await mem_judge(
    new_text="现在用 Solid",
    old_memory_id="2026-04-01-...",  # 旧的 "用 React" 记忆
)
# → {"judgment": {confidence: 0.91, reason: "...", type: "supersedes"},
#    "band": "auto"}
```

### `mem_compare`

diff 两条记忆 + LLM 判冲突。

```python
result = await mem_compare(memory_id_a="...", memory_id_b="...")
# → {"a": {...}, "b": {...}, "diff_lines": [...],
#    "judgment": {...}, "band": "review"}
```

## Admin 工具（6 个，admin tier）

启用方式：`MEMORYD_MCP_ADMIN=1 memoryd-mcp` 或 `memoryd-mcp --admin`。

### `mem_stats`

聚合 counts（按 type / scope / decay_state / top entities）。

```python
await mem_stats(scope=None)  # None = 全局
# → {"total": 1234, "by_type": {...}, "by_scope": {...},
#    "by_decay": {...}, "top_entities": [...]}
```

### `mem_merge_projects`

合并 scope_b 到 scope_a。`dry_run=True`（默认）只预览。
**不可逆**——确认前请用 dry_run。

```python
preview = await mem_merge_projects("scope_a", "scope_b", dry_run=True)
# → {"would_move": N, "would_dedup": M, "samples": [...]}
await mem_merge_projects("scope_a", "scope_b", dry_run=False)
```

### `mem_current_project`

返回 cwd 对应的 scope_hash（用于"我现在在哪个 scope?"）。

```python
await mem_current_project(cwd=None)
# → {"scope_hash": "...", "scope_name": "...", "scope_root": "..."}
```

### `mem_doctor`

跨子系统健康检查：data root / index db / embeddings / LLM / KG / sync。

```python
await mem_doctor()
# → {"data_root": "ok", "index_db": "ok", "embeddings": "ok",
#    "llm": "warn: ANTHROPIC_API_KEY not set", ...}
```

### `mem_save_prompt`

把高质量 prompt 模板存到 `<data_root>/prompts/<name>.md`。给 prompt 工程做记录。

```python
await mem_save_prompt(name="dura-extract", content="...")
```

### `mem_suggest_topic_key`

LLM（或启发式兜底）给一段 text 出一个稳定 topic_key。

```python
await mem_suggest_topic_key(content="React → Solid 的切换决策")
# → {"topic_key": "frontend-framework-migration", "source": "llm"}
```

---

## 通用响应约定

所有工具返回 `dict[str, Any]`，遇到 sensitive scope 没 grant 时统一抛 `AuthorizationRequired`
（MCP 错误 code），调用方应：

1. 跳过该工具，降级响应
2. 或调 `mem_request_sensitive_read`（旧 server 提供，规划合入新 server）

## 工具列表内省

```python
# 测试时取
from memoryd.mcp_server import build_server, list_tool_names
mcp = build_server(include_admin=True)
print(await list_tool_names(mcp))
# → ['mem_save', 'mem_update', 'mem_delete', 'mem_get', 'mem_search',
#    'mem_context', 'mem_timeline', 'mem_session_start', 'mem_session_end',
#    'mem_session_summary', 'mem_capture_passive', 'mem_judge', 'mem_compare',
#    'mem_stats', 'mem_merge_projects', 'mem_current_project', 'mem_doctor',
#    'mem_save_prompt', 'mem_suggest_topic_key']
```

## 设计权衡

- **19 个工具是显式选择**。不堆 50 个；少而正交
- **admin 工具默认隐藏**。普通 agent 不应该直接 merge projects 或读 stats
- **mem_judge / mem_compare 不自动 apply**。LLM 评分仅作 evidence，actions 由调用方决定
- **mem_capture_passive 跳过 DURA**。给可信源（mirror、importer）用，**不给 agent 用**
- **sensitive scope 上所有读工具自动 gate**。不需要工具本身写授权逻辑
