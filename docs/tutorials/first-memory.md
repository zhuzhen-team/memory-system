---
title: 教程 01 · 第一次记忆
keywords: capture, list, show, search, 第一次, hello world
---

# 教程 01 · 第一次记忆：写一条 + 查回来

**目标：** 5 分钟内手动写一条记忆，再用 4 种方式查回来。理解 memoryd 的写入与召回最小闭环。

**前置：** 已按 [详细安装](../getting-started/installation.md) 装好，`memoryd --help` 能跑。

## 一、写

```bash
echo '{"session_id":"my-first","transcript_path":"","cwd":"'"$(pwd)"'"}' \
  | memoryd capture --source=manual
```

**预期输出：**

```
captured -> /Users/<you>/.local/share/memoryd/scopes/<hash>/sessions/2026-05-20-my-first.md
```

发生了什么：

1. memoryd 读了 stdin 里的 JSON
2. 算了一下当前目录的 **scope_hash**（git toplevel 或 cwd 的 SHA256 前 12 位）
3. 在 `~/.local/share/memoryd/scopes/<hash>/sessions/` 下新建了一个 markdown 文件
4. 把这条 session 写进了 SQLite 索引（`memories` 表 + `memories_fts` 全文索引）
5. 后台 fork 出一个 `analyze-session` 进程跑 DURA 评分（如果你配了 LLM）

## 二、查方式 1：按时间倒序列

```bash
memoryd list --limit=5
```

**预期输出：**

```
2026-05-20-my-first                       [session   ] db8435ffd199   2026-05-20T00:18:23
```

四列含义：

| 列 | 含义 |
|---|---|
| `2026-05-20-my-first` | slug（文件名根） |
| `[session]` | type（六种类型之一） |
| `db8435ffd199` | scope_hash 前 12 位 |
| `2026-05-20T...` | created_at |

## 三、查方式 2：看详情

```bash
memoryd show 2026-05-20-my-first
```

**预期输出：**

```yaml
---
title: 2026-05-20 会话 my-first
slug: 2026-05-20-my-first
type: session
scope_hash: db8435ffd199
source: manual
created_at: '2026-05-20T00:18:23.202094'
---

## 无 transcript（transcript unavailable）

transcript_path: ``
session_id: `my-first`
```

memoryd 在 stdin 里**没拿到 transcript** —— 因为我们传的 `transcript_path` 是空字符串。真实场景里 Claude Code 的 hook 会把 transcript 文件路径塞进来，memoryd 会朴素摘要写进 body。

## 四、查方式 3：全文搜索

```bash
memoryd search "my-first"
```

**预期输出：**

```
2026-05-20-my-first    db8435ffd199    title: 2026-05-20 会话 my-first
```

search 走 ripgrep 全文 + Milvus 向量 + 知识图谱实体三路加权混合。一条 manual session 只有一行命中，更复杂的场景下 search 才会显出威力（详见 [搜索与召回](search-and-recall.md)）。

## 五、查方式 4：直接读 markdown

```bash
ls ~/.local/share/memoryd/scopes/
# db8435ffd199

ls ~/.local/share/memoryd/scopes/db8435ffd199/sessions/
# 2026-05-20-my-first.md

cat ~/.local/share/memoryd/scopes/db8435ffd199/sessions/2026-05-20-my-first.md
```

记住 memoryd 的核心设计：**Markdown 才是 source of truth**，SQLite 只是索引、向量库只是搜索副产物。`rm -rf` 数据库不会丢数据；rebuild-index 一句就回来：

```bash
memoryd rebuild-index
```

## 六、删

```bash
memoryd delete 2026-05-20-my-first
# 提示 y/N，输入 y
```

或加 `--force` 跳过提示。删的同时 `.md` 文件被 `rm`、SQLite 行被 drop。

## 你掌握了

- 一次 capture 写入产生的所有副作用（md + SQLite + 后台分析）
- 4 种召回路径（list / show / search / 直接看 md）
- scope_hash 的位置和命名规则
- "Markdown 是 SoT、索引可重建"的设计含义

## 下一步

把 hook 挂上让 AI 自己写：[教程 02 · 自动捕获](auto-capture.md)。
