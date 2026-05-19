---
title: 教程 02 · 自动捕获
keywords: SessionEnd hook, 自动 capture, Claude Code, transcript
---

# 教程 02 · 自动捕获：让 AI 自己往里写

**目标：** 挂上 Claude Code SessionEnd hook，每次 CC 会话结束自动 capture，理解整个事件流。

**前置：** 教程 01 跑过。

## 一、挂 hook

```bash
memoryd setup install-cc-hook
```

**预期输出：**

```
wired CC SessionEnd hook in /Users/<you>/.claude/settings.json
```

这条命令对 `~/.claude/settings.json` 做的事：

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"/Users/<you>/memory-system/plugins/claude-code/session-end.py\""
          }
        ]
      }
    ]
  }
}
```

`session-end.py` 干的事：

1. 从环境变量读 CC 传来的 SessionEnd payload
2. 拼成 JSON 写 stdin
3. 调 `memoryd capture --source=claude-code`
4. 顺手把 transcript 文件路径塞 payload —— 这样 memoryd 能读到完整对话

## 二、跑一段 CC 会话

打开新的 Claude Code 终端，随便问点什么，然后退出（Ctrl+D 或 `/exit`）。

## 三、看 hook 日志

```bash
tail -20 ~/.local/share/memoryd/logs/cc-session-end.log
```

**预期输出：** 几行日志，最后一行类似：

```
2026-05-20 12:34:56 captured -> /Users/<you>/.local/share/memoryd/scopes/<hash>/sessions/2026-05-20-<session_id>.md
```

如果没日志或日志报错，看 [故障诊断流](troubleshooting-flow.md) 的"场景 1：CC hook 不触发"节。

## 四、看刚刚自动写的

```bash
memoryd list --limit=5
```

应该看到最顶上一条 `source=claude-code` 的 session。

```bash
memoryd show <slug>
```

如果是真实的 CC 会话，body 会有"## 摘要（朴素截断，v1.0-α）"段落，里面是对话前 2000 字。

## 五、DURA 评分（如果配了 LLM）

`capture` 会异步 spawn 一个 `analyze-session` 进程跑 LLM 评分。等 5–10 秒：

```bash
memoryd digest
```

**预期输出：**

```
=== memoryd weekly digest @ 2026-05-20T... ===

候选提升 promotions (1 待审):
  - <id> dura=0.72 score={D:0.8, U:0.7, R:0.8, A:0.6}
    "我决定用 memoryd 作为主力记忆系统"
    type=decision -> 待 approve

重复合并 duplicates (0 对):
...
```

DURA = Decision / Useful / Recallable / Accurate 四准则。均分 ≥ 0.6 自动进 `promotions` 表 pending 审批。

不配 LLM 这一栏永远空。

## 六、批准 promotion

```bash
memoryd promote <promotion_id>
```

会把工作记忆"升级"成长期记忆（type 从 `session` 变成 `decision/preference/fact/...`），写一份新的 `.md` 到对应目录，旧 session 不动（历史可追溯）。

## 完整数据流

```
CC 会话退出
  ↓
SessionEnd 事件触发 ~/.claude/settings.json 的 hook
  ↓
plugins/claude-code/session-end.py 读 payload + transcript 路径
  ↓
memoryd capture --source=claude-code
  ↓
落 sessions/<id>.md + 写 SQLite + 写向量
  ↓
异步 fork: memoryd analyze-session <slug>
  ↓
  ├─ DURA 评分（LLM）→ promotions 表
  ├─ 实体抽取（LLM 或 jieba）→ entities 表
  ├─ 关系抽取 → relations 表
  └─ supersedes 检测（confidence ≥ 0.85 自动；0.5–0.85 进 digest）
  ↓
你下次 memoryd digest 看到待审清单
```

## 同样的事在 Codex / OpenClaw

- Codex：`memoryd setup swap-codex-notify --to wrapper`（详见 [Codex 集成](../integrations/codex.md)）
- OpenClaw：[OpenClaw 集成](../integrations/openclaw.md) 单独走 plugin 路径

## 你掌握了

- CC SessionEnd hook 完整事件流
- DURA 评分异步触发与审批
- 工作记忆与长期记忆的边界
- 三端各自的 capture 入口

## 下一步

把数据攒起来后看怎么用：[教程 03 · 搜索与召回](search-and-recall.md)。
