---
title: 卸载
keywords: 卸载, 删除, uninstall, 备份
---

# 卸载：干净退场

## 卸载前一定先备份

```bash
# 一份单文件备份（含审计链）
memoryd sync export --out=~/memoryd-final-backup.json --include-audit-chain

# 或镜像整个目录
cp -a ~/.local/share/memoryd ~/memoryd-final-backup/
```

## 一、拆 hook / launchd / cron / plugin

```bash
# CC SessionEnd hook
#   手工编辑 ~/.claude/settings.json 删除 SessionEnd 那一段
#   或：用 jq 删除
jq 'del(.hooks.SessionEnd)' ~/.claude/settings.json > ~/.claude/settings.json.tmp \
  && mv ~/.claude/settings.json.tmp ~/.claude/settings.json

# Codex notify wrapper：切回 original（拿回原 notify_cmd）
memoryd setup swap-codex-notify --to original

# launchd / systemd / Task Scheduler
memoryd setup uninstall-cron --all
memoryd setup uninstall-launchd-mirror     # macOS

# OpenClaw plugin
openclaw plugins remove memoryd-openclaw    # （依 OpenClaw CLI 实际命令）

# CC sub-agent 模板
rm ~/.claude/agents/memory-searcher.md

# memoryd MCP server 配置
jq 'del(.mcpServers.memoryd)' ~/.claude.json > ~/.claude.json.tmp \
  && mv ~/.claude.json.tmp ~/.claude.json
```

## 二、删数据

!!! danger "不可逆"
    下面这条删除你所有 memoryd 数据。请先确认上一步备份成功！

```bash
rm -rf ~/.local/share/memoryd
```

含：

- 所有 markdown
- SQLite 索引
- audit 链
- grants
- keyring 元数据
- 日志
- Milvus Lite 数据

## 三、删配置

```bash
rm -rf ~/.config/memoryd
```

## 四、删模型缓存

```bash
rm -rf ~/.cache/memoryd
```

里面是 bge-m3 ONNX 模型权重（约 500MB）。

## 五、删 venv + 仓库

```bash
rm -rf ~/memory-system
```

## 六、清 OS keyring（手工）

sensitive scope 的密钥还在 OS keyring 里——`rm -rf` 不会删它。

### macOS

打开 Keychain Access.app，搜索 `memoryd-scope-key`，全部删除。

或命令行：

```bash
security delete-generic-password -s memoryd-scope-key
```

每个 scope 一条，重复执行直到全删。

### Linux（gnome-keyring / KeePassXC）

```bash
secret-tool clear service memoryd-scope-key
```

或用 seahorse GUI 找 `memoryd-scope-key` 删除。

### Windows

打开 Credential Manager → Generic Credentials → 找 `memoryd-scope-key:*` 删除。

## 验证卸载干净

```bash
# 没有遗留进程
ps aux | grep -E 'memoryd|mirror' | grep -v grep
# 应该空

# 没有 launchd 任务（macOS）
launchctl list | grep memoryd
# 应该空

# 没有 cron / systemd timer
crontab -l 2>/dev/null | grep memoryd
systemctl --user list-timers | grep memoryd

# 没有 ~/.local/share/memoryd
ls ~/.local/share/memoryd 2>&1
# "No such file or directory"

# 没有 ~/.config/memoryd
ls ~/.config/memoryd 2>&1
# "No such file or directory"
```

## 重新安装

如果只是想"重置"而不是真的离开：

```bash
# 删数据保留代码
rm -rf ~/.local/share/memoryd ~/.config/memoryd
# 重新跑
memoryd setup auto-install
```

下次 capture 时会自动重建 index.db + migrations。

## 部分卸载（保留代码 + 数据，只关守护）

```bash
# 只关 launchd / cron
memoryd setup uninstall-cron --all
memoryd setup uninstall-launchd-mirror

# memoryd CLI 仍可手动用：
memoryd list
memoryd search ...
memoryd digest
```

适合"想暂停自动 capture 但保留以前累积的数据"。
