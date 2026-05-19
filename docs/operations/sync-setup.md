---
title: 同步配置
keywords: 同步, Dropbox, iCloud, Syncthing, memories.json, 跨设备
---

# 同步配置：路径 A 增量 + 路径 B 单文件

memoryd 支持两条独立的同步路径，按场景选。

| 路径 | 适合 |
|---|---|
| **A 增量 markdown** | 日常多设备同步（Dropbox / iCloud / Syncthing / git） |
| **B memories.json** | 一次性迁移、备份、跨工具导入导出 |

两条可以并存。

详细架构见 [架构 · 跨设备同步](../architecture/sync.md)。

## 路径 A：增量 markdown

### 配置

```bash
memoryd config set sync.enabled true
memoryd config set sync.dir ~/Dropbox/memoryd
memoryd config set sync.auto_export_on_session_end true    # 可选：每次 capture 后 fork 同步
memoryd config set sync.auto_import_on_session_start true  # 可选：每次 capture 前 fork 拉取
```

或直接编辑 `~/.config/memoryd/config.toml`：

```toml
[sync]
enabled = true
dir = "~/Dropbox/memoryd"
auto_export_on_session_end = true
auto_import_on_session_start = true
```

### 日常命令

```bash
memoryd sync export                # 推
memoryd sync export --scope=<hash> # 单 scope
memoryd sync export --dry-run      # 预览

memoryd sync import                # 拉，自动 rebuild-index
memoryd sync status                # 查看 per-scope 计数 + _conflicts
memoryd sync status --json
```

`--auto` 子选项给 hook 用：仅在 config 允许时执行。

### 同步内容

| 同步 | 不同步 |
|---|---|
| `scopes/**/*.md` | `index.db` 及 WAL（不可移植） |
| `scopes/**/*.md.enc` | `audit/`（审计链不能 merge） |
| `scopes/**/.scope-name` | `grants/`（授权只在本机） |
| `scopes/**/.memoryd-sensitive` | `keyring/`（密钥不出本机） |
| | `logs/` / `probe/`（本机调试） |

### 冲突解决

`memoryd sync import` 检测到 local 和 sync dir 同 slug 但 fingerprint 不同时：

- 本地版备份到 `~/.local/share/memoryd/scopes/_conflicts/<slug>-<fp8>.md`
- sync 版上位（覆盖本地）
- 冲突进 `digest` 复盘等待用户裁决

如果想 prefer-local，先 `--dry-run` 看清再决定。

## 路径 B：memories.json 单文件

```bash
# 导出
memoryd sync export --out=~/memories-$(date +%F).json
memoryd sync export --out=x.json --scope=<hash> --include-audit-chain
memoryd sync export --out=x.json --include-embeddings     # 大文件

# 导入
memoryd sync import --from=memories.json --conflict=merge
memoryd sync import --from=x.json --conflict=prefer-local
memoryd sync import --from=x.json --conflict=prefer-remote
memoryd sync import --from=x.json --dry-run

# diff（不写入）
memoryd sync diff-with-remote --from=memories.json
```

格式详见 [memories.json 格式](../reference/memories-json.md)。

向后兼容 `mcp-memory-service` v5.0.1：v5 工具可直接 load memoryd 的 export。

## 跨设备工作流

### 场景 1：MacBook ↔ Linux 工作站

1. MacBook：
   ```bash
   memoryd config set sync.dir ~/Library/CloudStorage/Dropbox/memoryd
   memoryd sync export
   ```
2. Linux：
   ```bash
   memoryd config set sync.dir ~/Dropbox/memoryd
   memoryd sync import
   ```

平时打开 Dropbox client 就够，两边 capture 都会 fork 同步。

### 场景 2：临时换机 / 迁移

1. 旧机：
   ```bash
   memoryd sync export --out=~/Desktop/memoryd-snapshot.json --include-audit-chain
   ```
2. 用 scp / U 盘 / iCloud Drive 把文件搬到新机
3. 新机：
   ```bash
   memoryd sync import --from=~/Desktop/memoryd-snapshot.json
   memoryd rebuild-index
   ```

### 场景 3：备份

```bash
# cron @daily
memoryd sync export --out=~/backups/memoryd-$(date +%F).json
```

加密 + redact 敏感 scope：用 passphrase 模式（见 [加密](encryption.md)）。

## sensitive scope 跨设备

随机密钥模式（默认）下，sensitive scope 的 `.md.enc` 在新机器**无法解密**——密钥在旧机器的 keyring 里。

要跨机器解密 sensitive scope，启用 **passphrase 模式**：

```bash
# 每台机器都跑（输入同一 passphrase）
memoryd set-passphrase
memoryd config set sensitive.key_source passphrase
```

PBKDF2-HMAC-SHA256（iter=600000）从 passphrase 派生 32B key，无需把 keyring 跨机搬。

!!! warning "passphrase 不能丢"
    忘记 passphrase = 所有 `.md.enc` 永久无法解。没有 recovery。
    建议把 passphrase 写在密码管理器（1Password / Bitwarden）。

## 跨平台 scope_hash caveat

scope_hash 派生自 resolved 路径。`/Users/abble/foo` 和 `/home/abble/foo` 算不同 scope_hash → 同一逻辑项目在两台不同 OS 上会算两个 scope。

解决方案（任选）：

1. **保持机器间 home dir 布局一致**：Linux 上 `sudo ln -s /home/abble /Users/abble`
2. **接受两个 scope**：每边各自管，靠 entity 共享做"软共享"
3. **手动 move-scope**（v1.1 工具规划）：把旧 scope 内容挪到新 scope_hash 目录

## 不同步盘场景

如果没有 Dropbox / iCloud：

- **Syncthing**：开源 P2P 同步，跑在两台机器，把 `~/Dropbox/memoryd` 替换成 `~/Sync/memoryd`
- **git**：每天 `git add . && git commit -m "memoryd snapshot" && git push`，另一端 `git pull`。但 `.md.enc` 是二进制，不适合 diff
- **手工 scp**：跑 `memoryd sync export --out=x.json` + scp

## 失败模式

| 现象 | 排查 |
|---|---|
| `sync import` 没拉到东西 | `memoryd sync status` 看 sync_dir 是否有文件 |
| 冲突很多 | 用 `--dry-run` 预览；检查两端 git ignore 是否一致 |
| `rebuild-index` 报错 | 看 stderr 错误；多半是 frontmatter 损坏 |
| sensitive 跨机不能读 | 检查 `key_source` 是否 passphrase + 是否 `set-passphrase` 过 |
| 同步盘体积膨胀 | 用 `memoryd sync export --include-embeddings=false`（默认） |
