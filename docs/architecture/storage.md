---
title: 存储层
keywords: Markdown, SQLite, 加密, 文件布局, frontmatter
---

# 存储层：Markdown 为根，SQLite 作索引

memoryd 的存储设计有三条规则：

1. **Markdown 是 source of truth**。SQLite 只是索引，可随时从 markdown 重建。
2. **SQLite 不入同步盘**。WAL 文件不可跨机器搬动；任何同步只动 markdown。
3. **加密是文件级别**。`.md.enc` 是 AES-256-GCM 密文，frontmatter 仍然写在文件里（在密文之内）。

## 文件布局

```
~/.local/share/memoryd/
├── scopes/
│   └── <scope_hash>/                # SHA256(git_root_or_cwd) 前 12 位
│       ├── .scope-name              # 人类可读的 scope 名
│       ├── .memoryd-sensitive       # 存在则该 scope 视为敏感
│       ├── sessions/                # 工作记忆，每次 capture 一份
│       │   └── 2026-05-18-<short_id>.md
│       ├── decisions/               # 长期：决策（默认 ttl=∞）
│       ├── facts/                   # 长期：事实
│       ├── preferences/             # 长期：偏好
│       ├── playbooks/               # 长期：操作手册
│       ├── warnings/                # 长期：警告
│       └── forgotten/               # decay → 物理归档
├── _unscoped/                       # 反推不到 scope 的兜底
│   └── sessions/...
├── _conflicts/                      # 跨机 sync 冲突时的本地备份
├── profile/                         # 全局画像
│   ├── identity.md                  # 最新一版 LLM 重写
│   ├── identity.md.history/         # 历次快照（YYYY-WW.md）
│   ├── change-reports/              # 月度变化报告
│   │   └── 2026-04.md
│   └── trends.md                    # 最新 trigger 频次
├── prompts/                         # mem_save_prompt 写入的高质量 prompt 模板
├── index.db                         # SQLite 索引（不入同步盘）
├── index.db-wal / .db-shm           # SQLite WAL（不入同步盘）
├── audit/                           # 审计目录
│   └── audit.jsonl                  # JSONL，SHA256 prev_hash 链
├── grants/                          # 敏感作用域授权状态
├── logs/                            # 各通路日志
└── last_import_at                   # auto-import throttle marker
```

源码：[memoryd/src/memoryd/storage.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/storage.py)

## 单文件结构（Markdown + frontmatter）

```yaml
---
title: 2026-05-18 会话 a1b2c3d4
slug: 2026-05-18-a1b2c3d4-e5f6-7890
type: session
scope_hash: d8e86b48589e
source: claude-code
created_at: 2026-05-18T22:30:12Z
updated_at: 2026-05-18T22:30:12Z

# 索引 hint
tags: [react, solid]
triggers: [前端切换, 性能, 体积]
category: tech/frontend

# 治理字段
ttl_days: 90
decay_state: alive
recall_count: 0
last_recalled_at: null
dura_score:
  D: 0.7
  U: 0.8
  R: 0.6
  A: 0.9

# 演化字段
supersedes: []
promoted_from: null

# Basic Memory 对齐
observations:
  - entity:library:React
  - entity:library:Solid
relations:
  - mentions:entity:library:React
  - mentions:entity:library:Solid
  - cites:memory:2026-04-12-abc12345
---

## 摘要

abble 决定从 React 切到 Solid，理由是性能 + 包体积...

## 工具调用

- npm install solid-js
- ...
```

完整字段定义见 [参考 · 数据模型](../reference/data-model.md)。

## SQLite 索引

完整 schema 见 [migrations/](https://github.com/zhuzhen-team/memory-system/tree/main/memoryd/src/memoryd/migrations)。
核心表：

| 表 | 来源 migration | 作用 |
|---|---|---|
| `memories` | 001 | 主索引：slug / type / scope_hash / decay_state / ttl_days / fingerprint / body_path |
| `triggers` | 001 | 反向索引：(slug, trigger) |
| `promotions` | 001 | DURA pending → approved 队列 |
| `sensitive_scopes` | 003 | 哪些 scope 被 mark-sensitive |
| `entities` | 004 | 知识图谱：7 类 entity |
| `relations` | 004 | 知识图谱：11 种 predicate |
| `supersedes_chain` | 004 | 决策演化追踪 |
| `profile_versions` | 005 | identity.md 历次快照 |
| `trigger_stats` | 005 | trigger 命中频次（按天） |
| `profile_change_reports` | 005 | 月度变化报告 |

启用了 `PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;`。

源码：[memoryd/src/memoryd/index.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/index.py)

### 重建索引

任何时候都可以：

```bash
memoryd rebuild-index
```

会清空 SQLite 然后扫一遍所有 `.md` / `.md.enc`（不解密，按 frontmatter 重建），输出 `indexed=N errors=M`。
单文件 `.md` 编辑后想立即生效也用它。

源码：[memoryd/src/memoryd/index.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/index.py)（`rebuild_index` 函数）

## Atomic write

`storage.save_session` 走 "tmp + rename" 模式，避免半写状态。任何 markdown 写入都是
原子的；同步盘可以放心镜像。

## 加密文件（.md.enc）

敏感 scope 下文件以 `.md.enc` 落盘，结构：

```
[12 bytes nonce][N bytes ciphertext][16 bytes auth_tag]
```

- 算法：AES-256-GCM
- 密钥：32 字节，存 OS keyring（macOS Keychain / Linux Secret Service / Windows DPAPI）
- service name：`memoryd-scope-key`，account：scope_hash

跨机器场景请走 passphrase 模式（PBKDF2-HMAC-SHA256，iter=600000）：

```bash
memoryd set-passphrase
```

详见 [加密](../operations/encryption.md)。

## 跨平台数据根

环境变量优先：

```bash
export MEMORYD_DATA_ROOT=/path/to/your/memoryd
```

不设则默认 `~/.local/share/memoryd/`，跨三大平台都一致（Linux XDG 标准；macOS 复用同一路径；Windows 走
`%LOCALAPPDATA%`，由 `platforms/windows.py` 调整）。
