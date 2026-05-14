---
title: 敏感作用域（Plan 4）设计
date: 2026-05-14
status: 已批准（light brainstorming 完成；terminal prompt + JSONL append-only）
related:
  - docs/superpowers/specs/2026-05-09-personal-usage-and-boundary-spec.md
  - docs/superpowers/specs/2026-05-14-long-term-memory-governance-design.md
role: 设计文档——Plan 4 实施 plan 与 SDD 都引用本文档
---

# Plan 4：敏感作用域设计

## 0. 这份文档是什么

spec §3 / §4.5 / §4.6 要求：用户能把某个目录标为敏感（如 `~/scopes/finance`）；标后该 scope 内所有记忆自动加密；任何 MCP 工具（含 Plan 3 的 7 个）读敏感 scope 前必须出"授权对话框"；3 种授权粒度（仅本次 / 本会话 / 本任务）；所有访问留可查不可篡改的审计日志。**Plan 4 把这一整套上线**。

Plan 4 不动 spec；不改 Plan 1-3 已有功能；只在 capture / search / governance 通路加 sensitive 检测层 + 加密 + 授权 + 审计。

## 1. 上游与硬约束（不要破）

| 已交付 | 状态 |
|---|---|
| 5 个 source tag + Plan 2.5 capture 通路 | merged `b140b35` |
| Plan 3 SQLite index + 6 类型 + 7 MCP 工具 + DURA + decay | merged `4dac127` |
| MCP 工具预算：当前 7/12 used；本 plan 加 1 → 8/12 | spec §3 |
| **MCP 工具数 ≤ 12** | spec §3 |
| **不接管三端原生记忆机制** / **保留原生启动方式** | spec §6 / §8 |
| **审计日志可查不可篡改（追加只写）** | spec §4.5 #20 |

## 2. 总体架构

```
┌────────────────────────────────────────────────────────────────────┐
│ Scope 元数据                                                        │
│   ~/.local/share/memoryd/scopes/<scope_hash>/.memoryd-sensitive    │
│     → 单纯存在性标记；内容含 scope_root 绝对路径（人类可读）        │
│   SQLite memories.scope_sensitive 列（Plan 4 加 column；Plan 3     │
│     migration 002 加）                                              │
└────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────┐
│ 加密层（macOS Keychain 起手；Plan 5 加 Win DPAPI + Linux Secret    │
│   Service）                                                         │
│   - 每个 sensitive scope_hash → Keychain item                       │
│     account=<scope_hash>, service=`memoryd-scope-key`               │
│   - 32-byte AES-256-GCM key                                         │
│   - .md 文件落盘时被 `enc.encrypt_file(scope_hash, plaintext)` 包成 │
│     `<slug>.md.enc`（base64-of nonce+ciphertext+tag）               │
│   - load_session 检测 .md.enc 后透明解密                            │
│   - frontmatter 还是 plain（schema/scope/type/triggers）+ 加密 body │
│     注：v1 简化——整个 .md 文件加密（含 frontmatter）；查询走 SQLite │
│     index 已有的明文字段                                            │
└────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────┐
│ 授权 / 审计层（gate.py）                                            │
│   - 每个 MCP 工具调用前，server.py 检查 scope_hash 是否 sensitive   │
│   - 若是，查 `~/.local/share/memoryd/grants/<scope_hash>.json`：    │
│       { "duration": "once|session|task",                            │
│         "expires_at": ISO,                                          │
│         "session_id": <pid 或 client-given>,                        │
│         "task_id": <user 给出的任务名 / null> }                     │
│   - 如有有效 grant → 放行 + 在 audit.jsonl 记 access_granted        │
│   - 没有 grant：                                                    │
│       a. 若 env `MEMORYD_AUTH_INTERACTIVE=1`：server 打开 /dev/tty │
│          写 prompt → 读用户输入 → 自动写 grant token                │
│       b. 否则：抛 ToolError "AUTHORIZATION_REQUIRED: <scope>"，提示  │
│          用户在另一个 terminal 跑 `memoryd grant <scope_path>`       │
│   - 审计 audit/audit.jsonl 一行一事件 append-only                   │
└────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────┐
│ 新增 MCP 工具（8/12 used after Plan 4）                              │
│   1-7. Plan 1+3 既有                                                │
│   8. request_sensitive_read(scope_path, query, duration?)            │
│      智能体显式请求授权——若用户当前 grant 已覆盖则 no-op；否则      │
│      返回 "authorization required" + 提示                            │
│ 预算剩余 4 工具，留 Plan 6-8 余地                                   │
└────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────┐
│ CLI 新增                                                            │
│   memoryd mark-sensitive <scope_path>      标 scope 敏感 + 生成密钥 │
│   memoryd unmark-sensitive <scope_path>    去标 + 删密钥 + 解密文件 │
│   memoryd grant <scope_path> --duration <X> [--task NAME]           │
│   memoryd revoke <scope_path>              立即撤销当前 grant       │
│   memoryd audit [--scope=<X>] [--since=<ISO>]                       │
│     列出 audit.jsonl 中匹配的事件，默认输出表格；--json 给脚本用    │
└────────────────────────────────────────────────────────────────────┘
```

## 3. 子目录继承（spec §3）

`memory mark-sensitive ~/scopes/finance` → `~/scopes/finance/.memoryd-sensitive` 文件存在。

scope 解析（`memoryd.scope.resolve_scope_root`）已经按 `.git` 父目录找 root。Plan 4 加一步：**先**找 `.memoryd-sensitive`（向上遍历），若命中则 scope_root 改成它所在目录，type=sensitive；**再**用 `.git` 兜底。子目录因此自动继承敏感属性。

子目录不能"覆盖"父目录的 sensitive 标——禁止在 `~/scopes/finance/sub` 标非敏感（spec §3 显式要求）。`mark-sensitive` 命令检查父目录链；`unmark-sensitive` 也只允许在已标的根操作。

## 4. 加密方案

### v1 简化版（Plan 4 macOS 实现）

- Keychain 通过 `subprocess.run(["security", ...])` 或 `keyring` PyPI 包（PyPI `keyring>=24` 是 macOS Keychain 的官方 binding，已稳定）
- 每 sensitive scope 一个 32-byte 随机 key，存 Keychain item：
  - `service`: `memoryd-scope-key`
  - `account`: `<scope_hash>`
  - `password`: base64-encoded 32 bytes
- 文件加密：AES-256-GCM via `cryptography` PyPI 包（已是 `anthropic` 间接依赖）
  - nonce: 12 bytes 随机
  - associated_data: scope_hash（防止 ciphertext 被 swap 到其他 scope）
  - 输出文件：`<scope>/<type>/<slug>.md.enc` 替代 `<slug>.md`，内容是 base64(nonce || ciphertext || tag)

### `enc.py` 接口

```python
def get_or_create_scope_key(scope_hash: str) -> bytes: ...  # 拿/创 32 字节 key
def encrypt_file(scope_hash: str, plaintext: bytes) -> bytes: ...
def decrypt_file(scope_hash: str, ciphertext: bytes) -> bytes: ...
def delete_scope_key(scope_hash: str) -> None: ...
```

### storage.py 改造

- `save_memory`：若 scope 敏感，写 `.md.enc` 而不是 `.md`；SQLite `body_path` 记录 `.md.enc`
- `load_session`：path 结尾 `.md.enc` → 先解密；index_memory 依然能拿到 frontmatter 明文（小数据，先解密再 parse）

### SQLite index 列

SQLite index 的 `title` / `scope_hash` / `type` / `triggers` 表都是明文（不加密索引）。这是 spec §3 数据观允许的：用户标了"敏感"是为了内容隐私，不是 metadata 隐私。triggers / title 是用户主动写的明文，本来就预期会出现在搜索结果。如果用户想完全隐藏 title，**unmark-sensitive 不会**自动覆盖这条；他们需要手动改 title。

### scope_sensitive 列

Plan 4 migration `002_sensitive_scope.sql`：

```sql
ALTER TABLE memories ADD COLUMN scope_sensitive INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_memories_sensitive ON memories (scope_sensitive);
```

`mark-sensitive` 时一次性把 scope 下所有 row 的 scope_sensitive 改 1；新写入的 row 由 save_memory 检查 `.memoryd-sensitive` 文件存在性后设置。

## 5. 授权 grant 机制

### grant token 文件

`~/.local/share/memoryd/grants/<scope_hash>.json`：

```json
{
  "scope_hash": "abc123",
  "scope_root": "/Users/abble/scopes/finance",
  "duration": "session",
  "expires_at": "2026-05-14T18:00:00+00:00",
  "issued_at": "2026-05-14T17:00:00+00:00",
  "issued_by": "memoryd grant --duration session",
  "task_id": null
}
```

- `once`：expires_at = issued_at + 90 seconds（够智能体跑一次工具）
- `session`：expires_at = issued_at + 8 hours（默认工作日长度）
- `task`：expires_at = `9999-12-31T23:59:59+00:00`，并设 task_id；用户跑 `memoryd revoke <scope> --task NAME` 才失效

### server.py 检查流程

新增 `governance/gate.py`：

```python
class AuthorizationRequired(Exception):
    """Raised by gate when a sensitive scope access lacks a valid grant."""

def is_scope_sensitive(scope_hash: str) -> bool: ...
def check_or_raise(scope_hash: str, action: str) -> None:
    """Read grants file; raise AuthorizationRequired if no valid grant."""
def interactive_prompt(scope_root: str) -> str | None:
    """If /dev/tty available and MEMORYD_AUTH_INTERACTIVE=1, prompt user;
    return chosen duration or None on decline."""
def write_grant(scope_hash, scope_root, duration, task_id=None) -> None: ...
def write_audit(event: dict) -> None: ...
```

server.py 在 7 个工具 + 新 `request_sensitive_read` 的执行体首行（在拿 `_data_root()` 之前）调 `gate.check_or_raise(scope_hash, tool_name)`。`AuthorizationRequired` 会被 FastMCP 转成 tool error 给 client。

### `request_sensitive_read` MCP 工具（第 8 个）

```python
@mcp.tool()
def request_sensitive_read(scope_path: str, query: str, duration: str = "once") -> dict:
    """Tell user the agent wants to read a sensitive scope. The user must
    grant via `memoryd grant` in another terminal (or accept interactive
    prompt if MEMORYD_AUTH_INTERACTIVE=1). Returns {granted: bool, ...}.
    """
```

- 智能体调它后：要么 raise AuthorizationRequired（同其他工具）；要么 interactive prompt 收到 OK → 自动 write_grant → 返回 `{granted: true}`
- 智能体收到 `{granted: false}` → 放弃读这条记忆，降级响应（spec §4.5 #19 "用户拒绝时智能体降级为'无敏感上下文'继续工作，不阻塞主任务"）

## 6. 审计日志

`~/.local/share/memoryd/audit/audit.jsonl`，append-only：

每行 JSON：

```json
{"ts": "2026-05-14T17:00:01+00:00",
 "scope_hash": "abc123",
 "scope_root": "/Users/abble/scopes/finance",
 "event_type": "access_granted|access_denied|grant_issued|grant_revoked|sensitive_marked|sensitive_unmarked",
 "tool": "search_memory|get_memory|request_sensitive_read|...|null",
 "duration": "once|session|task|null",
 "client_pid": 12345,
 "client_argv0": "/Applications/Claude.app/.../claude",
 "result": "ok|denied|error",
 "reason": "<optional>"}
```

`memoryd audit --scope=X --since=YYYY-MM-DD` grep+jq friendly 输出表格；`--json` 直接 cat 文件（带 since 过滤）。

文件权限 `0600`；spec 没要求 SHA chain 但加一道：每行末尾追加 `prev_hash` 字段（前一行的 sha256），新行的 hash 是当前内容（除 prev_hash 外）的 sha256。这是 spec §4.5 #20 "可查不可篡改"的低成本实现（伪造一行就要重算后续所有行）。

## 7. CLI 接口（5 个新子命令）

```
memoryd mark-sensitive <scope_path>
  - 检查 scope_path 是 .git 根或显式 scope（resolve_scope_root）
  - 检查父目录无 .memoryd-sensitive（避免双重标）
  - 调 enc.get_or_create_scope_key 生成密钥进 Keychain
  - 创 <scope_path>/.memoryd-sensitive 文件（人类可读：scope_root: <path>）
  - 加密所有 <scope_hash> 下的 .md → .md.enc（删原 .md）
  - SQLite scope_sensitive=1
  - audit: sensitive_marked

memoryd unmark-sensitive <scope_path>
  - 解密所有 .md.enc → .md
  - 删 Keychain item
  - 删 .memoryd-sensitive 文件
  - SQLite scope_sensitive=0
  - audit: sensitive_unmarked

memoryd grant <scope_path> --duration once|session|task [--task NAME]
  - 检查 scope sensitive
  - 写 grants/<hash>.json
  - audit: grant_issued

memoryd revoke <scope_path> [--task NAME]
  - 删 grants/<hash>.json（或 task 过滤）
  - audit: grant_revoked

memoryd audit [--scope=X] [--since=ISO] [--event-type=Y] [--json]
  - tail/grep audit.jsonl
  - 默认表格（ts / scope / event / tool / result）
```

## 8. 不在 Plan 4 内（边界）

| 不做 | 推迟到 |
|---|---|
| Windows DPAPI / Linux Secret Service | Plan 5 |
| sensitive scope 在多电脑间同步密钥 | Plan 6 |
| audit Web Dashboard | Plan 7 |
| 内容关键词敏感识别（密码 / 身份证扫描） | 永远不做（spec §6 明确） |
| 整文件 metadata-level encryption（隐藏 title / trigger） | v2 |

## 9. 风险与回退

| 风险 | 触发 | 回退 |
|---|---|---|
| Keychain 拒绝访问 | 用户没解锁 keychain | 提示用户解锁 + 重试；不丢数据 |
| `.md.enc` 解密失败 | 密钥被外部删 / 文件损坏 | load_session raise DecryptionFailed；用户 `memoryd recover-key <scope>`（v2） |
| `/dev/tty` 无 controlling terminal | GUI 启动的 client | interactive prompt 跳过；走 AuthorizationRequired 路径 |
| audit.jsonl 被用户手改 | prev_hash 链断 | `memoryd audit verify` 检测后报警；不阻止读 |
| migration 失败 | SQLite 旧版本 | open_index 会按序跑 001+002；失败 raise 启动错误 |

## 10. 完成判据

1. ✅ pytest 全绿（117 + 新增 25+ ≈ 145）
2. ✅ `mark-sensitive` → `.memoryd-sensitive` 文件 + Keychain item + 文件加密 + SQLite 列更新；逆操作 `unmark-sensitive` 完整还原
3. ✅ 标后 search_memory 在没 grant 时 raise AuthorizationRequired；有 grant 时返回明文
4. ✅ `request_sensitive_read` 工具可用
5. ✅ MCP 工具总数 8/12
6. ✅ audit.jsonl 含完整事件流；prev_hash 链可校验
7. ✅ Plan 1-3 已有功能无回归

## 11. 变更记录

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-05-14 | 初版 | Plan 3 完成；上敏感作用域 |
