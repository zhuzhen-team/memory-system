---
title: 教程 07 · 敏感记忆
keywords: sensitive, 敏感, 加密, AES-256, keyring, grant, revoke, audit
---

# 教程 07 · 敏感记忆：标敏感 / 加密 / 授权 / 审计

**目标：** 把涉及客户名单、凭证、财务等的 scope 标 sensitive，理解加密、密钥托管、按需授权、审计链全流程。

**前置：** 库里有一个想保护的 scope（比如 `~/clients-data`）。

## 它要解决的问题

普通记忆系统的问题：一旦泄露 / 设备丢失 / 共享读取，**所有记忆都裸的**。memoryd 给你"分级保护"：

- 默认 scope：明文 markdown
- 敏感 scope：AES-256-GCM 加密 + OS keyring 密钥 + 按需授权 + 全访问审计

只有当前激活 grant 的代码 / 命令能解密。Grant 过期自动撤销。每次解密都记一条不可篡改的审计行。

## 一、把一个 scope 标 sensitive

```bash
memoryd mark-sensitive ~/clients-data
```

**预期输出：**

```
mark-sensitive ~/clients-data
  scope_hash: 7f3a91b2c8e4
  encrypted files: 12
  key stored in keyring service=memoryd-scope-7f3a91b2c8e4
  .memoryd-sensitive marker written
```

发生的事：

1. 生成一把 256-bit AES key
2. 把 key 存进 OS keyring（macOS Keychain / Linux Secret Service / Windows DPAPI）
3. 加密所有现存 `.md` → `.md.enc`（AES-256-GCM）
4. 删除原 `.md`
5. 写 `.memoryd-sensitive` 标记文件到 scope 根
6. SQLite `memories.scope_sensitive = 1`
7. 子目录自动继承（递归生效）

## 二、看现在状态

```bash
ls ~/.local/share/memoryd/scopes/7f3a91b2c8e4/sessions/
# 全是 *.md.enc，没有 *.md

cat ~/.local/share/memoryd/scopes/7f3a91b2c8e4/sessions/2026-05-15-something.md.enc
# 一堆二进制，看不懂
```

```bash
memoryd search "client A"
# 找不到 —— 因为加密的内容不在向量库 / FTS 索引里
```

加密 scope 的记忆**默认不被搜索召回**，要先 grant 才能读。

## 三、临时授权：once

```bash
memoryd grant ~/clients-data --duration once
```

**预期输出：**

```
granted ~/clients-data (scope=7f3a91b2c8e4) for 90 seconds
```

90 秒窗口内，所有读这个 scope 的命令能解密；窗口关掉自动撤销。

```bash
memoryd search "client A"     # 现在能搜到
memoryd show 2026-05-15-something    # 现在能看到明文
```

## 四、长时间授权：session

```bash
memoryd grant ~/clients-data --duration session
```

**8 小时**有效。适合"今天我要专门处理客户数据"的场景。

## 五、任务级授权：task

```bash
memoryd grant ~/clients-data --duration task --task quarterly-report
```

直到你显式 revoke：

```bash
memoryd revoke ~/clients-data --task quarterly-report
```

适合"接到一个跨天的任务，做完才撤"。

## 六、审计每一次访问

```bash
memoryd audit --scope=7f3a91b2c8e4
```

**预期输出：**

```
| ts                  | event_type | actor          | task            | prev_hash    |
|---------------------|------------|----------------|-----------------|--------------|
| 2026-05-20T11:23:45 | grant      | cli            | (once)          | 7a3b2c...    |
| 2026-05-20T11:24:01 | read       | mem_show       | (once)          | 5e1f8d...    |
| 2026-05-20T11:24:15 | read       | mem_search     | (once)          | 2b9c4a...    |
| 2026-05-20T11:25:15 | revoke     | timeout        | (once)          | 8d7e6f...    |
```

**审计链不可篡改**：每行的 `prev_hash` 链向上一行。要校验：

```bash
memoryd audit --verify
# 正常输出 chain ok at N entries
# 篡改过 exit code 非 0 + 报错
```

## 七、解密看完后清理

`once` / `session` 都会自动到期。`task` 要主动 revoke。

```bash
memoryd revoke ~/clients-data
```

revoke 之后再 `memoryd show` 同一条记忆 → 报"access denied"。

## 八、跨设备同步加密 scope

同步盘里只有 `.md.enc`（已加密）。**密钥不进同步盘** —— 它在每台机器的 OS keyring。

换新机器：

```bash
# 机器 A
memoryd export-key --scope=~/clients-data > /tmp/k.txt    # 走安全通道传给 B

# 机器 B (装完 memoryd 后)
memoryd sync import          # 拿到 .md.enc 但还看不懂
memoryd import-key --scope=~/clients-data < /tmp/k.txt
# 密钥进了 B 的 keyring；之后 B 上 grant + show 都正常
```

## 九、解除 sensitive（紧急回退）

```bash
memoryd unmark-sensitive ~/clients-data
```

**预期输出：**

```
unmark-sensitive ~/clients-data
  decrypted files: 12
  key removed from keyring
```

`.md.enc` → 明文 `.md`，密钥从 keyring 删，标记文件删，SQLite 改回 `scope_sensitive=0`。

## 十、什么时候用 passphrase 模式而不是 keyring

Linux 没装 keyring 后端 / 你不想把密钥放 OS 服务里：

```bash
memoryd set-passphrase
# 提示输 passphrase，PBKDF2 派生密钥
# 之后每次 grant 都要输 passphrase 解锁
```

更安全（密钥只在你脑子里），但每次都要敲。

## 你掌握了

- mark / unmark sensitive 全流程
- once / session / task 三档 grant
- 审计链结构和 verify
- 跨设备分发密钥
- keyring vs passphrase 取舍

## 下一步

出了问题怎么定位：[教程 08 · 故障诊断流](troubleshooting-flow.md)。
