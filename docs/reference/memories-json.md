---
title: memories.json 格式
keywords: memories.json, mcp-memory-service, v5, 跨设备, schema
---

# memories.json：跨设备 / 跨工具单文件格式

`memoryd sync export` 的输出是一份自描述 JSON 文件，向后兼容 `mcp-memory-service` v5.0.1 导出格式，
向前扩展了 memoryd 的全量状态。

源码：

- 主逻辑：[memoryd/src/memoryd/sync/memories_json.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/sync/memories_json.py)
- 数据类：[memoryd/src/memoryd/sync/schema.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/sync/schema.py)
- 冲突合并：[memoryd/src/memoryd/sync/conflict.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/sync/conflict.py)
- v5 参考样例：[docs/reference/legacy-memories-json-sample.json](https://github.com/zhuzhen-team/memory-system/blob/main/docs/reference/legacy-memories-json-sample.json)

## 顶层结构

```json
{
  "export_metadata": { ... },
  "memories":         [ ... ],
  "entities":         [ ... ],
  "relations":        [ ... ],
  "supersedes_chain": [ ... ],
  "identity_snapshot": "...",
  "audit_chain":      [ ... ],
  "large_file_manifest": [ ... ],
  "encryption":       { ... }
}
```

只有 `export_metadata` + `memories` 是必填的；其他字段空则省略。

## `export_metadata`

```json
{
  "source_machine": "abble-mbp",
  "export_timestamp": "2026-05-18T22:30:12Z",
  "total_memories": 1234,
  "database_path": "~/.local/share/memoryd/index.db",
  "platform": "Darwin",
  "python_version": "3.12.0",
  "exporter_version": "memoryd-1",
  "schema_compat": ["mcp-memory-v5", "memoryd-1"],
  "include_embeddings": false,
  "include_audit_chain": true,
  "audit_chain_head": "sha256:abc..."
}
```

`exporter_version = "memoryd-1"` 标识 memoryd v1 的格式；旧 `mcp-memory-v5` 字段也声明在 `schema_compat`。

## `memories[]`

每条记忆同时含 **v5 兼容字段** + **memoryd 扩展字段**：

```json
{
  // v5 兼容（mcp-memory-service v5.0.1 工具能直接消费）
  "content": "决定从 React 切到 Solid...",
  "content_hash": "sha256:abc...",
  "tags": ["frontend"],
  "created_at": 1776595134.28,        // epoch seconds，与 v5 一致
  "updated_at": 1776595134.28,
  "memory_type": "decision",          // 对应 memoryd type 字段
  "metadata": {},                     // v5 自由 metadata bucket
  "export_source": "abble-mbp",

  // memoryd 扩展
  "id": "2026-05-12-a1b2c3d4",        // memoryd slug
  "scope": "d8e86b48589e",            // scope_hash
  "source": "claude-code",
  "frontmatter": { /* 完整 frontmatter */ },
  "entities": ["entity:library:Solid", "entity:library:React"],
  "relations": [
    {"predicate": "supersedes",
     "subject_id": "memory:2026-05-12-...",
     "object_id":  "memory:2026-04-01-..."}
  ],
  "supersedes": ["2026-04-01-..."],
  "decay_state": "alive",

  // 可选：超过 chunk_size_mb 时 body 落到 chunks 目录
  "large_file_pointer": {
    "ref": "abc...",
    "size_bytes": 12345678
  },

  // 可选：sensitive scope 内的密文
  "cipher_blob": "base64..."           // 与顶层 encryption 块配合
}
```

向后兼容意味着 v5 工具读到这份 JSON 时，只看 `content / tags / created_at / memory_type` 等字段，
完整 frontmatter / 关系 / 演化链都被忽略（不报错）。

## `entities[]`

```json
{
  "id": "entity:library:Solid",
  "name": "Solid",
  "type": "library",
  "aliases": ["solid.js", "solid-js"],
  "first_seen_at": "2026-05-01T00:00:00Z",
  "last_seen_at":  "2026-05-18T22:30:12Z",
  "mention_count": 17,
  "scope_hash": "d8e86b48589e"
}
```

7 种 type 见 [知识图谱](../architecture/knowledge-graph.md)。

## `relations[]`

```json
{
  "subject_id": "entity:person:abble",
  "subject_kind": "entity",
  "predicate": "prefers",
  "object_id": "entity:library:Solid",
  "object_kind": "entity",
  "source_memory_id": "2026-05-12-...",
  "scope_hash": "d8e86b48589e",
  "confidence": 0.92,
  "created_at": "2026-05-12T10:30:00Z",
  "superseded_at": null
}
```

11 种 predicate 见 [知识图谱](../architecture/knowledge-graph.md)。

## `supersedes_chain[]`

```json
{
  "newer_memory_id": "2026-05-12-...",
  "older_memory_id": "2026-04-01-...",
  "entity_id": "entity:library:React",
  "confidence": 0.91,
  "decided_at": "2026-05-12T10:30:00Z",
  "decided_by": "auto",
  "reason": "..."
}
```

## `identity_snapshot`

最新一版 identity.md 的全文：

```json
{
  "identity_snapshot": "---\ngenerated_at: 2026-05-18T03:00:00Z\n...\n# abble · 个人画像 ..."
}
```

import 时新设备会用这份作 baseline；下一次 weekly cron 重新跑会基于这个 + 本地新增的记忆生成新版。

## `audit_chain[]`

`include_audit_chain=true` 时附带：

```json
{
  "seq": 42,
  "ts": "2026-05-18T22:30:12Z",
  "actor": "cli",
  "event_type": "capture",
  "scope_hash": "d8e86b48589e",
  "target_id": "2026-05-12-...",
  "details": "{...}",
  "prev_hash": "sha256:abc...",
  "this_hash": "sha256:def..."
}
```

import 端验证 hash chain（必须连续），然后按 ts 顺序合并。

## `large_file_manifest`

体积超 `chunk_size_mb`（默认 5 MiB）的 memory body 落到 `<out>.chunks/<sha>.bin`：

```json
[
  {"ref": "abc...", "size_bytes": 12345678, "memory_id": "2026-...-...", "filename": "abc...bin"}
]
```

主 JSON 只保留 pointer。这样主文件 diff 不卡。

## `encryption`

如果 sensitive scope 用 passphrase 模式跨设备同步，导出会写：

```json
{
  "kdf": "pbkdf2-hmac-sha256",
  "kdf_iters": 600000,
  "salt": "base64:..."
}
```

`memories[].cipher_blob` 是用 derived key + AES-256-GCM 加密的 body 密文。
import 端必须输入同 passphrase 才能解。

## CLI 入口

```bash
# export
memoryd sync export --out=memories.json
memoryd sync export --out=x.json --scope=<hash> --include-audit-chain
memoryd sync export --out=x.json --include-embeddings   # 含向量（体积大）

# import
memoryd sync import --from=memories.json --conflict=merge
memoryd sync import --from=memories.json --conflict=prefer-local
memoryd sync import --from=memories.json --conflict=prefer-remote
memoryd sync import --from=memories.json --dry-run

# diff
memoryd sync diff-with-remote --from=memories.json
```

## 冲突合并策略

`merge_memory_fields()` 在 [memoryd/src/memoryd/sync/conflict.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/sync/conflict.py) 实现字段级合并：

- 同字段不同值 → 取 ts 较新的
- 不同字段 → 字段级 merge
- `relations` / `supersedes` / `tags` / `triggers` 是 list → 取并集
- 标量冲突无法消解 → 写 `_conflicts/` 等用户裁决

## v5 兼容验证

```bash
# 用 mcp-memory-service v5 工具读 memoryd export
mcp-memory-service load memories.json  # （假设 v5 工具命令）
# 应该正常解析 memories[].content / tags / created_at / memory_type
```

memoryd 自己不依赖 v5；写兼容只是为了让用户能跨工具迁移。
