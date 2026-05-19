---
title: 首次运行
keywords: capture, search, list, digest, 入门
---

# 首次运行：跑一遍写读循环

装完之后，建议手动跑一遍最小闭环，确认 capture → search → digest 全通。

## 一、手动写一条记忆

```bash
echo '{"session_id":"demo-1","transcript_path":"","cwd":"'"$(pwd)"'"}' \
  | memoryd capture --source=manual
```

输出例：

```
captured -> /Users/<you>/.local/share/memoryd/scopes/<hash>/sessions/2026-05-19-demo-1.md
```

## 二、看刚才写的

```bash
memoryd list --limit=5
```

应该列出至少一条 session memory，含 slug、type、scope、created_at。

```bash
memoryd show <刚才那条 slug>
```

会输出 frontmatter + body 原文。

## 三、搜索

```bash
memoryd search "demo"
```

输出命中条目 + score。--json 给脚本用：

```bash
memoryd search "demo" --json | jq
```

## 四、看 digest（治理面板）

```bash
memoryd digest
```

输出三栏：

- **候选提升**：DURA ≥ 0.6 的待审批 promotion
- **重复合并**：fingerprint 相同的条目对
- **TTL 到期**：进 dim / soft-forgotten 的提醒

刚装完应该都是空的。等几个 Claude Code 会话结束后再看，第一栏会有内容。

## 五、TUI 审批（可选）

```bash
memoryd digest --tui
```

启动 textual 交互界面，键盘：

- `a` Approve all pending
- `r` Reject highlighted
- `s` Skip
- `q` Quit

## 六、启动 Web Dashboard 浏览

```bash
memoryd web --port=8765
```

stderr 出一行：

```
memoryd web on http://127.0.0.1:8765/?token=<256-bit-token>
```

把整个 URL（含 `?token=`）复制到浏览器；token 不复制无法访问。

页面：

- `/` 仪表板首页（最近记忆 + 各页面跳转）
- `/memories` 列表
- `/memories/{slug}` 详情
- `/search?q=` 全文搜索（HTMX 局部刷新）
- `/audit` 审计日志
- `/digest` 待审 promotion 列表
- `/relations` 知识图谱（Cytoscape.js）
- `/trends` 趋势页
- `/identity` 用户画像

## 七、跑一轮 Claude Code 看自动 capture

如果你已经按 [安装](installation.md) 第三步挂了 hook，去 Claude Code 跑一段对话然后退出。

```bash
tail ~/.local/share/memoryd/logs/cc-session-end.log
memoryd list --recent=5
```

应该能看到一条 `source=claude-code` 的新 session。

如果 LLM 配好了，几秒后会异步触发 DURA 分析。再看：

```bash
memoryd digest
```

可能就有候选了。

## 下一步

- 知道核心概念：[核心概念](concepts.md)
- 配跨设备同步：[同步配置](../operations/sync-setup.md)
- 把另外两端也挂上：[Codex 集成](../integrations/codex.md) / [OpenClaw 集成](../integrations/openclaw.md)
