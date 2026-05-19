---
title: 加密
keywords: 加密, AES-256-GCM, keyring, passphrase, sensitive, grant, audit
---

# 加密：本机 keyring + passphrase 跨机

memoryd 的加密设计有三层：

1. **文件级加密**：标敏感的 scope 内全部 `.md` → `.md.enc`（AES-256-GCM）
2. **密钥层**：随机 32B（本机 keyring）或 passphrase 派生（PBKDF2）
3. **授权层**：访问 sensitive 内容必须有 grant，全程 audit chain

源码：

- [memoryd/src/memoryd/enc.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/enc.py) —— AES-256-GCM
- [memoryd/src/memoryd/passphrase.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/passphrase.py) —— PBKDF2 派生
- [memoryd/src/memoryd/governance/gate.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/governance/gate.py) —— 授权 gate
- [memoryd/src/memoryd/governance/audit.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/governance/audit.py) —— 审计链

## mark-sensitive

```bash
memoryd mark-sensitive ~/scopes/finance
```

子命令一气：

1. 写 `~/scopes/finance/.memoryd-sensitive` marker 文件（人类可读）
2. 生成 AES-256 key 进 OS keyring：
   - **macOS**：Keychain（service `memoryd-scope-key`, account 是 scope_hash）
   - **Linux**：Secret Service（需要 gnome-keyring 或 KeePassXC daemon 在跑）
   - **Windows**：Credential Manager / DPAPI
3. 把 `~/.local/share/memoryd/scopes/<scope_hash>/*.md` 全部 encrypt → `.md.enc`，删原 `.md`
4. SQLite `memories.scope_sensitive = 1` + `sensitive_scopes` 表插一行
5. audit `mark_sensitive` 事件

子目录自动继承——`~/scopes/finance/sub` 也算敏感。不能在敏感作用域内再开非敏感子作用域。

## unmark-sensitive

```bash
memoryd unmark-sensitive ~/scopes/finance
```

反操作：解密所有 `.md.enc` → `.md`，删 marker，删 keyring 条目。

需要先有 grant（防误删）。

## grant / revoke

```bash
memoryd grant ~/scopes/finance --duration once       # 90 秒
memoryd grant ~/scopes/finance --duration session    # 8 小时
memoryd grant ~/scopes/finance --duration task --task my-deep-work
memoryd revoke ~/scopes/finance --task my-deep-work
```

duration 三档：

- `once` —— 90 秒（默认用于"AI 临时查一次"）
- `session` —— 8 小时（默认用于"今天我都在做这个项目"）
- `task --task <name>` —— 直到 `revoke --task <name>`（默认用于"deep work 模式"）

存储：`~/.local/share/memoryd/grants/<scope_hash>.json`。**不进**同步盘。

## Agent workflow

任何 MCP / CLI / Web 工具读敏感 scope 前都先 `gate.check_or_raise(scope_hash)`。没 grant 抛 `AuthorizationRequired`，
调用方决定是 raise 还是返回友好降级。

```python
# 简化伪代码
def cmd_show(slug):
    memory = load_memory(slug)
    if memory.scope_sensitive:
        gate.check_or_raise(memory.scope_hash)  # 没 grant 抛 AUTHORIZATION_REQUIRED
    return memory
```

Agent 收到 `AuthorizationRequired` 后应当：

a) 放弃读、降级响应（"我注意到你问 finance 相关，但我没有授权访问"）
b) 调 `request_sensitive_read` 工具显式请求授权（如果存在）

设 `MEMORYD_AUTH_INTERACTIVE=1` 后 server 会经 `/dev/tty` 弹 4 选项 prompt：

```
Sensitive scope <hash> 需要授权。选：
  1) once (90s)
  2) session (8h)
  3) task <task>
  4) deny
```

仅 CLI client（CC stdio）有效；Codex.app GUI 等无 tty 场景不行。

## 加密文件结构

```
.md.enc 文件 = [12 bytes nonce][N bytes ciphertext][16 bytes auth_tag]
```

- 算法：AES-256-GCM
- 密钥：32 字节
- nonce：每次加密随机生成
- auth_tag：GCM 自带的完整性校验

## 密钥模式

### 1. random（默认）

每个 sensitive scope 一把 32B 随机 key，存 OS keyring。

- 优点：每 scope 一把独立 key，损一不损其他
- 缺点：跨机器不能解（key 在旧机器 keyring 里）

### 2. passphrase（opt-in for 跨机）

用户在所有机器跑 `memoryd set-passphrase` 输入同一短语 → PBKDF2-HMAC-SHA256(passphrase, salt=scope_hash, iters=600000) 推导 32B key。`.md.enc` 在所有机器都可解。

```bash
memoryd set-passphrase
memoryd config set sensitive.key_source passphrase
```

切换：

```bash
# 临时（CI / scripts）
export MEMORYD_MASTER_PASSPHRASE='your-12-char-or-more'

# 持久（写 keyring）
memoryd set-passphrase
```

!!! warning "passphrase 不能丢"
    忘记 passphrase = 所有 `.md.enc` 永久无法解。没有 recovery。
    建议写到 1Password / Bitwarden。

## audit 链

所有 sensitive 相关操作进 `~/.local/share/memoryd/audit/audit.jsonl`：

```bash
memoryd audit                                      # 全部
memoryd audit --scope=<hash>                       # 按 scope
memoryd audit --event-type=access_denied
memoryd audit --since=2026-05-01T00:00:00+00:00
memoryd audit --json
```

事件类型：

- `mark_sensitive` / `unmark_sensitive`
- `grant_issued` / `grant_revoked`
- `access_granted` / `access_denied`
- `decrypt` / `encrypt`

链式 hash：`this_hash = sha256(prev_hash || canonical_json(record))`。
篡改单行让后面所有行的链断掉。

校验：

```bash
memoryd audit verify     # 重算 hash 与 stored 比对
```

## Web Dashboard 上的敏感处理

- `/memories` 列表：sensitive 显 🔒 占位，不显标题
- `/memories/{slug}` 详情：sensitive 直接 403（**即使有 grant 也不放行**——Web 不解密）
- `/search`：sensitive 内容**不参与** search 索引（不会在结果里出现摘要）

要看 sensitive 内容只能走 CLI：

```bash
memoryd grant ~/scopes/finance --duration once
memoryd show <slug>
```

## 限制

- **macOS Keychain UI 没主题**。第一次访问会弹 macOS 原生对话框
- **Linux 需要 secret-service daemon**。gnome-keyring / KeePassXC 没装则 keyring 调用失败
- **Windows DPAPI 绑用户账号**。换用户账号要重新 mark-sensitive
- **passphrase 模式没有 rotation**。改 passphrase 暂走手工解密 + unmark + 重新 mark
- **Sensitive Web UI 永远 403**。设计上故意，避免 web 端泄露

## 失败模式

| 现象 | 排查 |
|---|---|
| keyring 写入失败（Linux） | 装 gnome-keyring 或 KeePassXC + 启 daemon |
| `mark-sensitive` 后看不到内容 | 这是正常的——授权才能读 |
| grant 之后还是 403 | 检查 `~/.local/share/memoryd/grants/<hash>.json` 是否存在 + 未过期 |
| passphrase 输错 | 重启 shell；`export MEMORYD_MASTER_PASSPHRASE=` 改 |
| audit verify 报错 | 看哪一行断；不要自己改 audit.jsonl |
