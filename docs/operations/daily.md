---
title: 日常使用
keywords: 日常, list, search, show, digest, promote, TUI
---

# 日常使用：常用命令与节奏

## 查最近写的

```bash
memoryd list                            # 默认 50 条，按时间倒序
memoryd list --limit=10
memoryd list --type=decision
memoryd list --scope=<hash>
memoryd list --source=claude-code
memoryd list --json | jq
```

## 全文搜索

```bash
memoryd search "claude code hook"
memoryd search "<query>" --type=decision --limit=20
memoryd search "<query>" --json | jq
```

混合检索（ripgrep + 向量 + 实体加权）详见 [架构 · 搜索](../architecture/search.md)。

## 看某条详情

```bash
memoryd show <slug>
memoryd show <slug> --scope=<hash>     # 显式 scope，跨 scope 同名时
```

输出：

```
---
title: ...
...
---

正文 markdown ...
```

## 主动写一条记忆

```bash
echo '{"session_id":"x","transcript_path":"","cwd":"'$(pwd)'"}' \
  | memoryd capture --source=manual
```

或通过 MCP（agent 内部）：

```python
await mem_save(content="...", type="decision", scope="auto", tags=[...], triggers=[...])
```

## 每日 / 每周 digest

```bash
memoryd digest             # 文本视图
memoryd digest --json
memoryd digest --notify    # 同时弹原生通知（macOS notification / Linux libnotify / Windows BurntToast）
memoryd digest --tui       # textual 交互界面
```

三栏：

- **候选提升**（DURA ≥ 0.6 pending）
- **重复合并**（同 fingerprint）
- **TTL 到期**（即将 dim / 已 soft-forgotten）

新增 **trends 栏**：top trigger / entity 上升 / supersede 事件 / recall hot。

## TUI 审批

```bash
memoryd digest --tui
```

键盘：

- `a` Approve all pending
- `r` Reject highlighted
- `s` Skip
- `q` Quit

依赖：textual ≥ 0.40；macOS Terminal / iTerm / Windows Terminal 都兼容。

## 批准 pending promotion

```bash
memoryd digest --json | jq '.candidate_promotions[].id'  # 拿 id
memoryd promote 42
```

`promote` 真写文件：`scopes/<hash>/<type>s/promoted-<id>-<slug>.md`，含 `promoted_from` 标 source session。

## 合并重复

```bash
memoryd merge --keep <good-slug> --drop <bad-slug-1> <bad-slug-2>
```

`--keep` 保留，`--drop` 删除。被合并的条目以 audit `merge` 事件记录。

## 删除

```bash
memoryd delete <slug>          # 默认 y/N 确认
memoryd delete <slug> --force
```

**不可逆**。删除前可以先 `memoryd show <slug>` 看清。

## 启动 Web Dashboard

```bash
memoryd web --port=8088
# 把 stderr 输出的含 ?token= URL 粘到浏览器
```

详见 [Web 仪表板](../reference/web-dashboard.md)。

## 看审计链

```bash
memoryd audit                                        # 全部事件
memoryd audit --scope=<hash>
memoryd audit --since=2026-05-01T00:00:00+00:00
memoryd audit --event-type=access_denied
memoryd audit --json | jq
```

## 看画像

```bash
cat ~/.local/share/memoryd/profile/identity.md
ls  ~/.local/share/memoryd/profile/change-reports/
```

Web 端：`/identity` 路由有版本切换 + diff 视图。

## 推荐的日常节奏

| 时段 | 命令 |
|---|---|
| 每天结束前 | `memoryd digest`（看 pending 多不多） |
| 每周一上午 | `memoryd digest --tui`（批量审批） |
| 每月一次 | 看 `~/.local/share/memoryd/profile/change-reports/<上个月>.md` |
| 切设备前 | `memoryd sync export` |
| 切设备后 | `memoryd sync import` |
| 新加客户 / 新项目时 | 主动 `mem_save` 一条 fact 或 preference，让画像更准 |
| 觉得画像不对时 | `memoryd profile rewrite`（手工触发重写） |

## 常见任务速查

| 我想 | 命令 |
|---|---|
| 看最近 5 条 CC capture | `memoryd list --source=claude-code --limit=5` |
| 找上次关于 X 的决策 | `memoryd search "X" --type=decision` |
| 一次性 import 我的 CLAUDE.md | `memoryd import claude-md ~/.claude/CLAUDE.md` |
| 把所有记忆同步到 Dropbox | `memoryd sync export` |
| 跨机继续 | 另一台 `memoryd sync import` |
| 标某个项目为敏感 | `memoryd mark-sensitive <path>` |
| 让 AI 访问敏感记忆 1 次 | `memoryd grant <path> --duration once` |
| 看画像最新版 | `memoryd web` → `/identity` |
