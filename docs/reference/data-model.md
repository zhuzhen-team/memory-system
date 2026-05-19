---
title: 数据模型
keywords: frontmatter, schema, SQLite, 表, migrations
---

# 数据模型：frontmatter 字段 + SQLite 表

## frontmatter schema

源码：[memoryd/src/memoryd/schema.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/schema.py)（Pydantic `Frontmatter`）

```yaml
---
# 标识
title: 2026-05-18 会话 a1b2c3d4         # 人类可读标题
slug: 2026-05-18-a1b2c3d4-e5f6-7890     # 唯一 id（也是文件名）
type: session                            # session/decision/preference/fact/playbook/warning
scope_hash: d8e86b48589e                 # 12 位 SHA256 前缀
source: claude-code                      # claude-code/codex/codex-rollout/openclaw/openclaw-fs/manual/importer-<which>
created_at: 2026-05-18T22:30:12Z
updated_at: 2026-05-18T22:30:12Z

# 索引 hint
tags: [react, solid]
triggers: [前端切换, 性能]                # 关键词列表，用于 keyword search 索引
category: tech/frontend                  # 顶层归类（Basic Memory 对齐）

# 治理
ttl_days: 90                             # null = 永久
decay_state: alive                       # alive/dim/soft-forgotten
recall_count: 0
last_recalled_at: 2026-05-18T22:30:12Z
dura_score:
  D: 0.7
  U: 0.8
  R: 0.6
  A: 0.9

# 演化
promoted_from: 2026-05-12-xyz...         # 从哪条 session 提升来的
supersedes: [2026-04-12-abc...]          # 这条覆盖了哪些旧条目

# 关系（KG 写入）
observations:
  - entity:library:React
  - entity:library:Solid
relations:
  - mentions:entity:library:React

---
```

!!! note "加密状态不在 frontmatter"
    `encrypted` **不是** markdown frontmatter 字段。是否加密通过**文件路径**判断：明文文件叫 `*.md`，加密文件叫 `*.md.enc`（AES-256-GCM）。SQLite 索引在 `memories.scope_sensitive` 列里记录 per-memory 标记，`sensitive_scopes` 表里记录 per-scope 标记。`encrypted: true/false` 只在 [memories.json 跨设备格式](memories-json.md) 里出现，那是同步用的 schema。

### 字段含义速查

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `title` | str | 必填 | 人类可读标题 |
| `slug` | str | 必填 | 唯一 id |
| `type` | `MemoryType` | 必填 | 6 种之一 |
| `scope_hash` | str | 必填 | 12 位 |
| `source` | str | 必填 | 来源 tag |
| `created_at` | datetime | 必填 | UTC ISO-8601 |
| `updated_at` | datetime | None | 修改时刷新 |
| `triggers` | list[str] | `[]` | 关键词索引 |
| `tags` | list[str] | `[]` | Basic Memory 标签 |
| `category` | str | None | 顶层归类 |
| `observations` | list[str] | `[]` | 抽到的 entity_id 列表 |
| `relations` | list[str] | `[]` | 关系字符串列表 |
| `promoted_from` | str | None | 提升来源 session slug |
| `supersedes` | list[str] | `[]` | 覆盖的旧条目 |
| `ttl_days` | int | None | None = 永久 |
| `decay_state` | str | `"alive"` | 衰减状态 |
| `last_recalled_at` | datetime | None | 上次被召回时间 |
| `recall_count` | int | 0 | 被召回次数 |
| `dura_score` | dict[str, float] | None | DURA 4 项 |

## SQLite migrations

源码：[memoryd/src/memoryd/migrations/](https://github.com/zhuzhen-team/memory-system/tree/main/memoryd/src/memoryd/migrations)

| 文件 | 引入的表 |
|---|---|
| `001_initial_schema.sql` | `memories` · `triggers` · `promotions` |
| `002_sensitive_scope.sql` | 加 `memories.scope_sensitive` 字段（兼容旧表）|
| `003_sensitive_scopes_table.sql` | `sensitive_scopes` |
| `004_knowledge_graph.sql` | `entities` · `relations` · `supersedes_chain` |
| `005_profile_self_learning.sql` | `profile_versions` · `trigger_stats` · `profile_change_reports` |

每次 `open_index()` 自动跑未执行的 migration。

## 主索引：memories

```sql
CREATE TABLE memories (
    slug             TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    scope_hash       TEXT NOT NULL,
    title            TEXT NOT NULL,
    source           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT,
    ttl_days         INTEGER,
    decay_state      TEXT NOT NULL DEFAULT 'alive',
    last_recalled_at TEXT,
    recall_count     INTEGER NOT NULL DEFAULT 0,
    fingerprint      TEXT NOT NULL,
    body_path        TEXT NOT NULL UNIQUE
);

CREATE INDEX idx_memories_scope_type    ON memories (scope_hash, type);
CREATE INDEX idx_memories_fingerprint   ON memories (fingerprint);
CREATE INDEX idx_memories_decay         ON memories (decay_state, last_recalled_at);
CREATE INDEX idx_memories_created       ON memories (created_at);
```

`fingerprint = sha1(body[:500])`，用于跨路径去重。

## triggers 反向索引

```sql
CREATE TABLE triggers (
    slug     TEXT NOT NULL,
    trigger  TEXT NOT NULL,
    PRIMARY KEY (slug, trigger),
    FOREIGN KEY (slug) REFERENCES memories(slug) ON DELETE CASCADE
);
```

## promotions 待审批

```sql
CREATE TABLE promotions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    source_session_slug  TEXT NOT NULL,
    proposed_type        TEXT NOT NULL,
    proposed_title       TEXT NOT NULL,
    proposed_body        TEXT NOT NULL,
    proposed_triggers    TEXT NOT NULL,   -- JSON array
    dura_score           TEXT NOT NULL,   -- JSON object
    reasoning            TEXT,
    proposed_supersedes  TEXT NOT NULL DEFAULT '[]',
    scope_hash           TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/rejected/merged
    created_at           TEXT NOT NULL,
    decided_at           TEXT,
    FOREIGN KEY (source_session_slug) REFERENCES memories(slug) ON DELETE CASCADE
);
```

## sensitive_scopes

```sql
CREATE TABLE sensitive_scopes (
    scope_hash    TEXT PRIMARY KEY,
    marked_at     TEXT NOT NULL,
    key_source    TEXT NOT NULL DEFAULT 'random'    -- random / passphrase
);
```

## entities

完整定义：[migrations/004_knowledge_graph.sql](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/migrations/004_knowledge_graph.sql)

```sql
CREATE TABLE entities (
  id              TEXT PRIMARY KEY,    -- entity:person:abble
  name            TEXT NOT NULL,
  type            TEXT NOT NULL,       -- 7 类：person/organization/place/library/tool/project/concept
  aliases         TEXT,                -- JSON array
  context         TEXT,
  first_seen_at   TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  mention_count   INTEGER NOT NULL DEFAULT 1,
  scope_hash      TEXT,
  decay_state     TEXT NOT NULL DEFAULT 'fresh'
);
```

## relations

```sql
CREATE TABLE relations (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  subject_id        TEXT NOT NULL,
  subject_kind      TEXT NOT NULL,     -- entity / memory
  predicate         TEXT NOT NULL,
  object_id         TEXT NOT NULL,
  object_kind       TEXT NOT NULL,
  source_memory_id  TEXT,
  scope_hash        TEXT,
  confidence        REAL,
  created_at        TEXT NOT NULL,
  superseded_at     TEXT,
  UNIQUE(subject_id, predicate, object_id, source_memory_id)
);
```

11 种 predicate：`mentions / works_on / uses / prefers / supersedes / superseded_by /
conflicts_with / cites / runs_on / belongs_to / located_at`。

## supersedes_chain

```sql
CREATE TABLE supersedes_chain (
  newer_memory_id  TEXT NOT NULL,
  older_memory_id  TEXT NOT NULL,
  entity_id        TEXT,
  confidence       REAL NOT NULL,
  decided_at       TEXT NOT NULL,
  decided_by       TEXT NOT NULL,      -- auto / user / digest
  reason           TEXT,
  PRIMARY KEY(newer_memory_id, older_memory_id)
);
```

## profile_versions

```sql
CREATE TABLE profile_versions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    version_num          INTEGER NOT NULL UNIQUE,
    written_at           TEXT NOT NULL,
    trigger              TEXT NOT NULL,   -- weekly_cron / manual / on_event / monthly_report
    content_md           TEXT NOT NULL,
    diff_from_prev       TEXT,
    change_summary       TEXT,
    sources_count        INTEGER,
    sources_window_start TEXT,
    sources_window_end   TEXT
);
```

## trigger_stats

```sql
CREATE TABLE trigger_stats (
    trigger     TEXT NOT NULL,
    scope_hash  TEXT NOT NULL DEFAULT '_global',
    day         TEXT NOT NULL,            -- YYYY-MM-DD
    hits        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (trigger, scope_hash, day)
);
```

## profile_change_reports

```sql
CREATE TABLE profile_change_reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    period            TEXT NOT NULL UNIQUE,    -- YYYY-MM
    generated_at      TEXT NOT NULL,
    content_md        TEXT NOT NULL,
    versions_count    INTEGER,
    supersedes_count  INTEGER,
    entities_added    INTEGER,
    entities_dropped  INTEGER
);
```

## audit.jsonl（不在 SQLite 里）

`~/.local/share/memoryd/audit/audit.jsonl` 一行一事件：

```json
{
  "seq": 42,
  "ts": "2026-05-18T22:30:12Z",
  "actor": "cli",
  "event_type": "capture",
  "scope_hash": "d8e86b48589e",
  "target_id": "2026-05-18-...",
  "details": "{...}",
  "prev_hash": "sha256:abc...",
  "this_hash": "sha256:def..."
}
```

`this_hash = sha256(prev_hash || canonical_json(record_without_hash))`，篡改单行让后面所有行链断。

## grants（不在 SQLite 里）

`~/.local/share/memoryd/grants/<scope_hash>.json`：

```json
{
  "scope_hash": "d8e86b48589e",
  "granted_at": "2026-05-18T22:30:12Z",
  "duration": "session",
  "expires_at": "2026-05-19T06:30:12Z",
  "task": null
}
```

## 跨设备：memories.json

详见 [memories.json 格式](memories-json.md)。
