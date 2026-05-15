---
title: 多电脑同步（Plan 6）设计
date: 2026-05-15
status: 已批准（light brainstorming：裸 .md 镜像 + passphrase-derived 密钥 opt-in）
related:
  - docs/superpowers/specs/2026-05-09-personal-usage-and-boundary-spec.md
  - docs/superpowers/specs/2026-05-14-sensitive-scopes-design.md
  - docs/superpowers/specs/2026-05-15-plan5-cross-platform-design.md
role: 设计文档——Plan 6 实施 plan 与 SDD 都引用本文档
---

# Plan 6：多电脑同步 设计

## 0. 这份文档是什么

spec §4.7 #26 要求 SessionEnd 自动 export `memories.json` 到用户配置同步目录，SessionStart 自动 import；SQLite **不进**同步盘（避免 WAL 锁损坏）；§4.5 #17 要求敏感密钥本地——但 §5 场景 E 又说"敏感作用域需要在新机器单独标记并迁移密钥"。Plan 6 落地这一整套同步层；spec §4.5 #17 已 amend：保留 default per-scope random key 模式 + 加 opt-in passphrase-derived 模式。

Plan 6 不改 Plan 1-5 现有功能；只加 sync 层 + 加密层第二种 mode；不接管同步盘软件本身（用户自己装坚果云 / iCloud / Dropbox）。

## 1. 上游与硬约束

| 已交付 | 状态 |
|---|---|
| Plan 5 跨平台 + install-cron + auto-install | merged `79b533c` |
| Plan 4 macOS Keychain + scope_meta + audit | merged `8ce76aa` |
| Plan 3 SQLite index + fingerprint 字段 + 6 类型 | merged `4dac127` |
| scope.py: scope_hash 路径派生 | Plan 1 |

| 硬约束 | 来源 |
|---|---|
| MCP 工具数 ≤ 12（当前 8 used） | spec §3。本 plan **不加新 MCP 工具**（sync 走 CLI） |
| Markdown 是 source of truth；SQLite 只索引 | spec §3 |
| SQLite index.db **不**进同步盘 | spec §4.7 #26 |
| Audit log / grants / Keychain 密钥 **不**进同步盘 | Plan 4 安全边界 |
| 不双向同步 CLAUDE.md / AGENTS.md / auto-memory | spec §6 |
| 全本地——同步盘软件由用户安装；memoryd 不联网 | spec §3 |

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│ Local data root（既有）                                                │
│   ~/.local/share/memoryd/                                            │
│     scopes/<hash>/*.md  .md.enc        ← 进同步盘                      │
│     scopes/<hash>/.memoryd-sensitive    ← 进同步盘（标记是否敏感）    │
│     index.db                            ← 不进                        │
│     audit/audit.jsonl                   ← 不进                        │
│     grants/<hash>.json                  ← 不进                        │
│     logs/                               ← 不进                        │
└──────────────────────────────────────────────────────────────────────┘
                              ↓ export
┌──────────────────────────────────────────────────────────────────────┐
│ Sync dir（用户配置；典型 ~/Library/CloudStorage/Dropbox/memoryd/）     │
│   scopes/<hash>/*.md  .md.enc                                        │
│   scopes/<hash>/.memoryd-sensitive                                   │
│   _conflicts/<slug>-<fp8>.md（冲突时落）                              │
│   .memoryd-sync-state.json（fingerprint manifest，用于增量）          │
└──────────────────────────────────────────────────────────────────────┘
                              ↑ import
┌──────────────────────────────────────────────────────────────────────┐
│ 第二台机器：从同步盘 import → 本地 scopes/<hash>/ 同结构                │
│   rebuild-index 自动跑一次（SQLite 在本地不进同步盘）                  │
│   敏感 .md.enc 需要 passphrase 才能解（passphrase mode）             │
│   或者用户在本机重 mark-sensitive 用新 random key（不能跨机解原密文）│
└──────────────────────────────────────────────────────────────────────┘
```

## 3. config.toml [sync] / [sensitive] section

```toml
[sync]
enabled = false                          # 默认关；用户主动开
dir = "~/Library/CloudStorage/Dropbox/memoryd"  # 同步盘 mirror dir
auto_export_on_session_end = false       # 默认关；开后 CC hook 会 fork export
auto_import_on_session_start = false     # 默认关；开后 capture 前先 import 一次

[sensitive]
key_source = "random"                    # random（Plan 4 default）| passphrase（Plan 6 opt-in）
kdf_iters = 600000                       # passphrase 模式 PBKDF2 iterations
```

`memoryd config set sync.enabled true` 改这些。`~` 自动 expanduser。

## 4. sync export / import / status CLI

### `memoryd sync export`

```bash
memoryd sync export                       # 增量 export 所有 scope
memoryd sync export --scope=<hash>        # 仅指定 scope
memoryd sync export --dry-run             # 列出会动什么；不动文件
```

实现（`memoryd/src/memoryd/sync.py`）：

1. 读 `[sync] dir`；resolve `~` → 绝对路径。
2. 校验 dir 存在 + 可写。
3. 列举本地 `scopes/*/sessions|decisions|preferences|facts|playbooks|warnings|forgotten/*.{md,md.enc}` 加 `.memoryd-sensitive` marker。
4. 对每个文件比对 fingerprint：
   - 本地 fingerprint 存 SQLite `memories.fingerprint`（Plan 3 既有）。
   - sync dir 的 `.memoryd-sync-state.json`：`{ "<scope>/<type>/<slug>": "<fingerprint>" }`
   - 本地 != sync state → 复制本地 → sync dir（覆盖）
   - 本地 == sync state → 跳过
   - 仅 sync state 有、本地无 → **不删 sync** dir 副本（让 import 决定）
5. 更新 `.memoryd-sync-state.json` 写回。

### `memoryd sync import`

```bash
memoryd sync import                       # 增量 import 所有
memoryd sync import --scope=<hash>
memoryd sync import --dry-run
```

实现：

1. 列举 sync dir 下文件。
2. 对每个文件：
   - 本地不存在 → 复制 sync → local；SQLite index_memory。
   - 本地存在 + fingerprint 相同 → no-op。
   - 本地存在 + fingerprint 不同 + 同 slug → **冲突**：
     - 本地版改名为 `_conflicts/<slug>-<local_fp8>.md`；
     - sync 版 import 到 `<slug>.md`。
     - audit 一行 sync_conflict。
   - 本地存在 + fingerprint 相同 + 不同 slug（罕见，因为 fingerprint 就是 slug 的派生之一）→ alias，跳过。
3. 后置 rebuild-index 一次（保证 SQLite 与 .md 一致）。

### `memoryd sync status`

```
memoryd sync status

sync dir:      /Users/abble/Library/CloudStorage/Dropbox/memoryd
enabled:       true
auto_export:   false   auto_import: false
last_export:   2026-05-15T09:00:01+00:00
last_import:   2026-05-14T22:30:00+00:00

per-scope:
  d8e86b48589e  local 12 / sync 12  (✓ in sync)
  a3f2b91c0e44  local  3 / sync  5  (! sync ahead)

_conflicts: 0
```

`--json` 输出 dict 给脚本。

## 5. SessionEnd / SessionStart auto-sync

启用条件：`[sync] enabled=true` + 对应 auto_* 开关。

- **SessionEnd**：`scripts/cc-session-end-hook.py` / `.ps1` / 现有 `.sh` 在末尾 fork `memoryd sync export --quiet`（background，不阻塞 hook 退出）。
- **SessionStart**：CC 没有 SessionStart hook（spec 不接管）；走 SessionStart 触发的 hook 当然没法接，所以 import 走两条路径：
  1. `cc-session-end-hook.py` 之前的 capture 命令前自动跑 `sync import`
  2. 或 `memoryd capture` 子命令头部检查"sync.auto_import_on_session_start && (now - last_import > 5min)"自动 fork import

简化方案：在 `memoryd capture` 主入口加 pre-step "若 sync.auto_import 开且距上次 import > 5min → fork import"。`5min` 防止 CC 短时间多次 capture 时同步刷爆。

## 6. Passphrase-derived 密钥（opt-in）

### 启用流程

```bash
# 在所有机器上跑同一个：
memoryd config set sensitive.key_source passphrase
memoryd set-passphrase    # 交互式 prompt；不显回显；写本机 OS keyring
                          # service: "memoryd-master-passphrase", account: "default"
```

### enc.py 改造

新接口（已有 `get_or_create_scope_key` 保持向后兼容）：

```python
def get_or_create_scope_key(scope_hash: str) -> bytes:
    """Plan 4: per-scope random 32B key in OS keyring (default).
    Plan 6 passphrase mode: PBKDF2-HMAC-SHA256(passphrase, salt=scope_hash, iters=cfg.kdf_iters).
    """
    cfg = load_config()
    if cfg.sensitive.key_source == "passphrase":
        passphrase = _get_master_passphrase()  # OS keyring
        if not passphrase:
            raise EncError("master passphrase unset; run `memoryd set-passphrase`")
        return _derive_key(passphrase, scope_hash.encode(), cfg.sensitive.kdf_iters)
    # default: Plan 4 random-key path（原 implementation 拆到内部 _get_random_scope_key）
    return _get_random_scope_key(scope_hash)


def _derive_key(passphrase: bytes, salt: bytes, iters: int) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iters)
    return kdf.derive(passphrase)
```

### scope_hash 跨机器问题

scope_hash = sha1(resolved_path)[:12]，依赖路径。跨机器路径不同 → scope_hash 不同 → 同一逻辑项目的敏感记忆在新机器算作"另一个 scope"。Plan 6 v1 接受这个限制：

- **解决方案 v1**：用户用同样 home dir 布局（如 macOS / Linux 都用 `~/projects/wolin`，Win 用 `C:\Users\<u>\projects\wolin`）；
- **不解决的部分**：跨平台路径差异（C:\ vs /home）；v2 评估 "scope_root 从 git remote URL 派生" 替代路径。

文档 + execution-log 明确告知用户。

### set-passphrase CLI

```python
@subcommand
def cmd_set_passphrase(args):
    import getpass
    p1 = getpass.getpass("Master passphrase: ")
    p2 = getpass.getpass("Confirm: ")
    if p1 != p2:
        print("mismatch", file=sys.stderr); return 1
    if len(p1) < 12:
        print("at least 12 chars please", file=sys.stderr); return 1
    keyring.set_password("memoryd-master-passphrase", "default", p1)
    print("master passphrase stored locally", file=sys.stderr)
    return 0
```

env 覆盖：`MEMORYD_MASTER_PASSPHRASE` 环境变量优先（用于 CI / 自动化测试）。

## 7. 风险与回退

| 风险 | 触发 | 回退 |
|---|---|---|
| 同步盘 conflict 风暴（多机同时改） | 罕见，用户多机并发改同记忆 | 落 _conflicts/，audit log 一行 sync_conflict；用户 digest 中可看到 |
| sync dir 不可写 | 同步盘软件未运行 | sync export 报错并退；不丢数据（本地仍有） |
| passphrase 忘了 | 用户改 passphrase | 旧 .md.enc 永久无法解（无法找回）；新数据走新 passphrase；用户须接受此 trade-off |
| passphrase 短/弱 | 用户随便设 | `set-passphrase` 强制 ≥ 12 字符；不强制 entropy，用户自负 |
| SQLite index 进同步盘（用户误操作） | 自己 cp index.db 到 sync dir | sync 子命令明确跳过 index.db；文档警告 |
| 跨机器路径差异 | Mac /Users/x vs Linux /home/x | 接受。文档明确：用户保持路径一致或手动 move-scope（v2 解决） |
| passphrase 在 OS keyring 被泄漏 | 攻击者拿到本机 keyring | 同 Plan 4 风险面；passphrase 本地 keyring 等同于 scope key 本地 keychain |

## 8. 文件结构

### 新建

```
memoryd/src/memoryd/
  sync.py                 # export / import / status / dry-run / conflict logic
  passphrase.py           # set/unset/get master passphrase（OS keyring 包装）
memoryd/tests/
  test_sync_export.py
  test_sync_import.py
  test_sync_conflicts.py
  test_sync_status.py
  test_passphrase.py
  test_enc_passphrase.py
```

### 修改

```
memoryd/src/memoryd/config.py           # 加 SyncConfig + SensitiveConfig
memoryd/src/memoryd/enc.py              # key_source 分发：random vs passphrase-derived
memoryd/src/memoryd/cli.py              # sync export/import/status + set-passphrase 子命令
memoryd/src/memoryd/setup.py            # auto-install 集成 sync hook（如开启）
scripts/cc-session-end-hook.py          # 加 sync export fork 末尾
scripts/cc-session-end-hook.ps1         # 同上
memoryd/src/memoryd/__init__.py 或 capture 入口  # auto_import 触发
memoryd/README.md                       # Plan 6 章节
```

## 9. 不在 Plan 6 内（边界）

| 不做 | 推迟到 |
|---|---|
| Web Dashboard | Plan 7 |
| 旧记忆导入 | Plan 8 |
| 自动跨平台路径解析（scope_hash 从 git remote 派生） | v2 |
| keychain bundle export/import（备选方案 C） | v2，如果用户反馈 passphrase 模式不够用 |
| 真实 SMTP server 嵌入 | v2 |
| 同步盘冲突自动 3-way merge | v2 |

## 10. 完成判据

1. ✅ pytest 全绿（199 + 新增 ~30 ≈ 230 passed）
2. ✅ `sync export` → sync dir 镜像本地 scopes/*；index.db / audit / grants / logs 不出现
3. ✅ `sync import` → 干净的新机能从 sync dir 拉回所有 .md；自动 rebuild-index 后 search_memory 找回历史记忆
4. ✅ Conflict 场景：本地 + sync 同 slug 不同 fingerprint → 本地版进 `_conflicts/`，sync 版上位
5. ✅ `sync status --json` 给完整 dict（per-scope 计数 + last_export/import）
6. ✅ Passphrase 模式：在 mock keyring 下 set-passphrase → mark-sensitive → 写出的 .md.enc 用另一进程同样 passphrase + 同 scope_hash 能解
7. ✅ `MEMORYD_MASTER_PASSPHRASE` env 优先于 OS keyring（CI 友好）
8. ✅ Plan 1-5 测试无回归
9. ✅ MCP 工具数 8 / 12（不增）
10. ✅ README 加 Plan 6 章节；execution-log 写 sync e2e 真机步骤

## 11. 变更记录

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-05-15 | 初版 | Plan 5 完成；上多电脑同步 |
