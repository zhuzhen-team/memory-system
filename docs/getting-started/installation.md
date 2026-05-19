---
title: 安装
keywords: 安装, uv, ripgrep, install-all, memoryd setup
---

# 安装：从零到能跑

## 前置条件

| 依赖 | 用途 | 安装方式 |
|---|---|---|
| Python ≥ 3.11 | memoryd 跑这之上 | 任意发行版 |
| [`uv`](https://github.com/astral-sh/uv) | Python 包管 + venv | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `ripgrep` | 关键词搜索后端 | `brew install ripgrep` / `apt install ripgrep` |
| `git` | scope 派生看 git root | 默认有 |
| `keyring` 后端 | 加密密钥存 OS keyring | macOS / Windows 默认；Linux 需 gnome-keyring 或 KeePassXC |

## 一、克隆仓库

```bash
git clone https://github.com/zhuzhen-team/memory-system ~/memory-system
cd ~/memory-system/memoryd
```

## 二、创建 venv + 安装 memoryd

```bash
uv venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
```

装好后会有三个命令：

| 命令 | 用途 |
|---|---|
| `memoryd` | 主 CLI（capture / search / list / show / digest / sync ...） |
| `memoryd-server` | 旧 MCP server（仅 `search_memory` 单工具，向后兼容） |
| `memoryd-mcp` | 新 MCP server，暴露 19 个 `mem_*` 工具 |

验证：

```bash
memoryd --help
memoryd config show
```

## 三、一键挂三端 + 守护进程

```bash
memoryd setup auto-install
```

这条命令按当前平台依次执行：

1. 写 `~/.claude/settings.json` 的 SessionEnd hook，指向 `plugins/claude-code/session-end.sh`
2. 安装 cron：daily decay 03:00 + weekly digest Mon 09:00（macOS launchd / Linux systemd timer / Windows Task Scheduler）

!!! note "Codex 与 OpenClaw 的挂接"
    `auto-install` 当前只挂 CC 的 hook + cron。Codex notify wrapper 与
    OpenClaw plugin 需要按各自页面（[Codex 集成](../integrations/codex.md) /
    [OpenClaw 集成](../integrations/openclaw.md)）单独跑配置子命令。

## 四、配 LLM（强烈推荐）

memoryd 的 DURA 评分 / 知识图谱实体抽取 / identity 重写都需要 LLM。默认 Anthropic Claude Haiku 4.5。

```bash
# 1. shell rc 里写：
export ANTHROPIC_API_KEY=sk-ant-xxx

# 2.（可选）改 provider / model
memoryd config set llm.provider anthropic
memoryd config set llm.model    claude-haiku-4-5
```

不配 API key 也能跑：capture 仍正常，但 DURA 候选 + KG 抽取 + identity 重写都自动跳过；jieba
分词作 KG 兜底，质量打折。

支持 provider：`anthropic` / `openai` / `ollama`。详见 [架构 · 治理](../architecture/governance.md)
最后一节。

## 五、把 MCP server 接进 Claude Code

```bash
# 1. 找到 memoryd-mcp 的绝对路径
which memoryd-mcp
# 输出例：/Users/abble/memory-system/memoryd/.venv/bin/memoryd-mcp

# 2. 用 Python 安全 merge 到 ~/.claude.json（不要直接 echo 覆盖）
python <<'EOF'
import json
from pathlib import Path

path = Path.home() / ".claude.json"
with open(path) as f:
    d = json.load(f)

d.setdefault("mcpServers", {})
d["mcpServers"]["memoryd"] = {
    "command": "/Users/abble/memory-system/memoryd/.venv/bin/memoryd-mcp",
    "args": [],
    "env": {
        "MEMORYD_DATA_ROOT": "/Users/<you>/.local/share/memoryd"
    }
}

tmp = path.with_suffix(".json.tmp")
with open(tmp, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
tmp.replace(path)
EOF
```

重启 Claude Code。`/mcp` 命令里应该看到 **memoryd** 19 个工具。

## 六、跑一遍测试（可选）

```bash
cd ~/memory-system/memoryd
uv run pytest -v
```

新模块测试约 370+，全通过。

## 卸载

```bash
memoryd setup uninstall-cron --all
memoryd setup uninstall-launchd-mirror   # macOS
# 删数据（先备份！）
memoryd sync export                       # 备份 markdown 到同步盘
rm -rf ~/.local/share/memoryd
```

## 下一步

- [首次运行](first-run.md) —— 跑一遍 capture / search / digest
- [核心概念](concepts.md) —— 了解 scope / DURA / 衰减
