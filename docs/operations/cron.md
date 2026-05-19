---
title: 定时任务
keywords: cron, launchd, systemd, Task Scheduler, decay, digest, weekly, monthly
---

# 定时任务：decay / digest / weekly identity / 月度报告

memoryd 用 cron（或平台等价物）跑四类周期任务：

| 任务 | 频率 | 命令 |
|---|---|---|
| `decay-sweep` | 每日 03:00 | 走衰减状态机 |
| `digest --notify` | 每周一 09:00 | 周复盘 + 通知 |
| weekly identity rewrite | 每周日 02:00 | LLM 重写 identity.md（需 LLM 配好） |
| monthly change report | 每月 1 日 04:00 | 月度画像变化报告（需 LLM 配好） |

源码：[memoryd/src/memoryd/setup_cron.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/setup_cron.py) ·
[memoryd/src/memoryd/platforms/](https://github.com/zhuzhen-team/memory-system/tree/main/memoryd/src/memoryd/platforms)

## 一键安装（跨平台）

```bash
memoryd setup auto-install
```

会根据当前 OS 调对应 platforms backend：

| OS | 后端 |
|---|---|
| macOS | launchd LaunchAgent plist |
| Linux | systemd user timer |
| Windows | Task Scheduler |

## 单独装

```bash
memoryd setup install-cron --decay      # 只装 decay-sweep
memoryd setup install-cron --digest     # 只装 weekly digest
memoryd setup install-cron --all        # 一次性两个
```

## 反操作

```bash
memoryd setup uninstall-cron --all
memoryd setup uninstall-launchd-mirror   # macOS launchd 守护
```

## macOS 详细

### LaunchAgents 路径

```
~/Library/LaunchAgents/com.memoryd.<task>.plist
```

例如：

- `com.memoryd.decay.plist`
- `com.memoryd.digest.plist`
- `com.memoryd.mirror.plist`（Codex / OpenClaw FS-watch 守护，不是 cron）

### 手工诊断

```bash
launchctl list | grep memoryd
launchctl print gui/$(id -u)/com.memoryd.mirror

# 手动跑一遍
/Users/abble/memory-system/memoryd/.venv/bin/memoryd decay-sweep
/Users/abble/memory-system/memoryd/.venv/bin/memoryd digest --notify
```

## Linux 详细

systemd user timer：

```
~/.config/systemd/user/memoryd-decay.{service,timer}
~/.config/systemd/user/memoryd-digest.{service,timer}
```

诊断：

```bash
systemctl --user list-timers
systemctl --user status memoryd-decay.timer
journalctl --user -u memoryd-decay.service --no-pager -n 50
```

老 systemd 注意：可能需 `loginctl enable-linger <user>` 让 user timer 在登录前跑。

## Windows 详细

Task Scheduler：

- `\memoryd\decay` 每日 03:00
- `\memoryd\digest` 每周一 09:00

诊断：

```powershell
schtasks /query /tn "\memoryd\decay" /v /fo list
schtasks /run /tn "\memoryd\decay"
```

## SMTP 邮件 digest（可选）

`~/.config/memoryd/config.toml`：

```toml
[notify.smtp]
enabled = true
host = "smtp.gmail.com"
port = 587
use_tls = true
from = "you@gmail.com"
to = "you@gmail.com"
username = "you@gmail.com"
password_env = "MEMORYD_SMTP_PW"
```

```bash
export MEMORYD_SMTP_PW=<app-password>
memoryd digest --notify
```

`--notify` 同时发原生通知 + 邮件。

## 调整频率

直接编辑 plist / service / scheduled task 文件，或：

```bash
memoryd setup uninstall-cron --all
# 修改 ~/.config/memoryd/config.toml 里 [cron] 段（如有）后
memoryd setup install-cron --all
```

## 跨任务依赖

- `weekly identity rewrite` **要求** LLM 配好（`ANTHROPIC_API_KEY` 或 OpenAI / Ollama）。没配会跳过 + audit warn
- `monthly change report` 同上
- `decay-sweep` 不依赖 LLM，纯 SQL 状态机
- `digest` 不依赖 LLM；只是统计 + 排版

## 失败模式

| 现象 | 排查 |
|---|---|
| 任务没跑 | macOS 看 `launchctl list`；Linux 看 `systemctl --user status` |
| 任务跑了但没效果 | 看 `~/.local/share/memoryd/logs/` 下对应日志 |
| identity rewrite 跳过 | 多半 LLM 配缺；`memoryd config show` 看 llm 段 |
| digest 通知没弹 | `memoryd digest --notify` 手工跑看错误；macOS 看通知中心是否被禁 |
| cron 时区错 | 系统时区设错；`timedatectl` 看 |

## 卸载

```bash
memoryd setup uninstall-cron --all
memoryd setup uninstall-launchd-mirror     # macOS
```

确保 platform backend 残留也清掉：

```bash
# macOS
ls ~/Library/LaunchAgents/com.memoryd.*

# Linux
ls ~/.config/systemd/user/memoryd-*

# Windows
schtasks /query /fo list | findstr memoryd
```
