---
title: 5 分钟快速开始
keywords: quickstart, 快速开始, 5 分钟, hello world, 上手
---

# 5 分钟快速开始

memoryd 是个**完全在你本机跑的 AI 记忆服务**。装上之后，无论你用 Claude Code、Codex 还是 OpenClaw，三端共享同一份记忆 + 同一份会自动学习的用户画像。

下面 5 分钟跑通**写一条记忆 → 搜回来 → 在浏览器看到**。

## 装

确认你机器有 Python ≥ 3.11、[uv](https://github.com/astral-sh/uv)、`ripgrep`、`git`。一键跑：

```bash
git clone https://github.com/zhuzhen-team/memory-system ~/memory-system
cd ~/memory-system/memoryd
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

完事大约 30 秒。验证：

```bash
memoryd --help | head -3
```

看到 `usage: memoryd [-h] {capture,...}` 就装好了。如果没看到，看 [详细安装](installation.md)。

## 写一条记忆

```bash
echo '{"session_id":"hello","transcript_path":"","cwd":"'"$(pwd)"'"}' \
  | memoryd capture --source=manual
```

**应该看到：**

```
captured -> /Users/<you>/.local/share/memoryd/scopes/<hash>/sessions/2026-05-20-hello.md
```

memoryd 把 stdin 的 JSON 当作一次"会话结束事件"处理：算出当前目录的 scope_hash，把它落成一个 markdown 文件。这就是 memoryd 的**写入单元**。

## 看刚才写的

```bash
memoryd list --limit=5
```

**应该看到：**

```
2026-05-20-hello                          [session   ] db8435ffd199   2026-05-20T00:18:23
```

```bash
memoryd show 2026-05-20-hello
```

**应该看到：** 完整 frontmatter（slug / type / scope_hash / source / created_at）+ markdown body。

## 搜索

```bash
memoryd search "hello"
```

memoryd 自动跑全文 + 向量 + 实体三路混合，按相关性返回。

## 浏览器打开看

```bash
memoryd web --port=18765
```

**应该看到：**

```
memoryd web on http://127.0.0.1:18765/?token=<256-bit-token>
```

把整个 URL（含 `?token=...`）复制到浏览器。**不带 token 会被 401 拒绝** —— 这是 memoryd 的本地安全设计：即使 Web 跑在你机器上，没有 token 也访问不了。

你会看到：

- 顶部最近记忆列表
- 左边导航：搜索 / 知识图谱 / 待审 / 审计 / identity
- 现在你大概只有 1 条 manual session

按 `Ctrl+C` 停掉服务。

## 接下来

memoryd 真正的价值是**让你的 AI 工具自己往里写**。继续看：

| 想做什么 | 看哪里 |
|---|---|
| 让 Claude Code 自动 capture | [详细安装 · 七 一键挂三端](installation.md) |
| 让 CC 主动读记忆（MCP）| [详细安装 · 八 接 MCP server](installation.md) |
| 系统化学习 9 个常用场景 | [教程系列](../tutorials/index.md) |
| 理解 scope / DURA / 衰减 | [核心概念](concepts.md) |
| 看每个 CLI 详细参数 | [CLI 命令](../reference/cli.md) |
