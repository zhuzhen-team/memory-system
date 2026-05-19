---
title: 详细安装
keywords: 安装, uv, ripgrep, install-all, memoryd setup, macOS, Linux, Windows
---

# 详细安装：照着做就能跑

如果你只想最快跑起来，看 [5 分钟快速开始](quickstart.md)。本页给你**每一步都解释 + 每一步都告诉你应该看到什么**，遇到问题不慌。

## 一、确认前置依赖

memoryd 是 Python 写的本地后台服务，不需要数据库、不需要联网（联网只是为了让 LLM 帮你打分）。但你机器上要有：

| 依赖 | 干什么的 | macOS 装 | Linux 装 | Windows 装 |
|---|---|---|---|---|
| Python ≥ 3.11 | memoryd 本体 | `brew install python@3.11` | `apt install python3.11` 或 pyenv | python.org 下载安装包 |
| `uv` | 装 Python 包 + 建 venv（快 5–10 倍） | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | 同 macOS | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| `ripgrep` | 关键词搜索后端 | `brew install ripgrep` | `apt install ripgrep` | `winget install BurntSushi.ripgrep.MSVC` |
| `git` | clone 仓库 + 算 scope | 默认有 | 默认有 | `winget install Git.Git` |

**验证：**

```bash
python3 --version    # 应该 ≥ 3.11
uv --version         # uv 0.x.y
rg --version         # ripgrep x.y.z
git --version
```

四条命令都正常输出版本号才能往下走。

!!! tip "在 Windows 上"
    建议用 WSL2 + Ubuntu，原生支持更好。原生 Windows 也能跑，但 keyring / cron / launchd 这些 OS 集成层会自动切到 Windows DPAPI + Task Scheduler，路径有零星差异。

## 二、克隆仓库

```bash
git clone https://github.com/zhuzhen-team/memory-system ~/memory-system
cd ~/memory-system/memoryd
```

仓库里有两块：

- `~/memory-system/memoryd/` — Python 包，主程序
- `~/memory-system/plugins/` — Claude Code / Codex / OpenClaw 三端的胶水脚本

后面装的时候会两块都用到。

## 三、建 venv 装包

```bash
uv venv
source .venv/bin/activate     # Windows PowerShell：.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
```

`-e .` 是 editable install —— 仓库里的代码改了立刻生效，不用重装。`.[dev]` 把测试依赖也装上。

**应该看到：**

```
Resolved 80+ packages
Installed 80+ packages in 5s
+ memoryd==1.0.0 (from file:///.../memoryd)
+ mcp==x.y.z
+ pydantic==2.x.y
... (一长串)
```

如果中间有 `ModuleNotFoundError` 或 `error: failed to build`，多半是 Python 版本不对、或没装 build essentials（Linux：`apt install build-essential python3-dev`）。

## 四、验证安装

```bash
which memoryd
which memoryd-mcp
memoryd --help | head -5
```

**应该看到：**

```
/Users/<you>/memory-system/memoryd/.venv/bin/memoryd
/Users/<you>/memory-system/memoryd/.venv/bin/memoryd-mcp
usage: memoryd [-h] {capture,analyze-session,mirror,...} ...
```

装好后多了三条命令：

| 命令 | 用途 |
|---|---|
| `memoryd` | 主 CLI（capture / search / list / show / digest / sync ...） |
| `memoryd-mcp` | MCP server（暴露 19 个 `mem_*` 工具给三端 AI） |
| `memoryd-server` | 旧 MCP server（仅 `search_memory` 单工具，向后兼容） |

!!! warning "退出 venv 后命令找不到"
    venv 不激活时 `memoryd` 不在 PATH。两个办法：
    1. 给 `~/.zshrc` / `~/.bashrc` 加一行 `alias memoryd=~/memory-system/memoryd/.venv/bin/memoryd`（同样给 `memoryd-mcp`）；
    2. 或者每次新开 shell 都先 `source ~/memory-system/memoryd/.venv/bin/activate`。

## 五、看一眼默认配置

```bash
memoryd config show
```

**应该看到：**

```json
{
  "llm": {
    "provider": "anthropic",
    "model": "claude-haiku-4-5",
    "api_key_env": "ANTHROPIC_API_KEY",
    "request_timeout_sec": 30
  },
  "prompts": {
    "dura_extract": ""
  }
}
```

这就是 memoryd 的"工厂设置"。LLM 默认用 Anthropic Haiku；要换 OpenAI / Ollama，看下一节。

## 六、配 LLM（强烈推荐，但可跳过）

memoryd 的「DURA 评分 / 知识图谱实体抽取 / identity 重写」三个高级功能需要 LLM。**不配也能跑**：capture / search / list / show 全功能照常，只是这三个会自动跳过、用 jieba 中文分词兜底，质量打折。

最常见的方式：在你的 shell rc 里设环境变量。

```bash
# macOS / Linux：写进 ~/.zshrc 或 ~/.bashrc
export ANTHROPIC_API_KEY=sk-ant-xxx

# Windows PowerShell：
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-xxx", "User")
```

想换 provider：

```bash
memoryd config set llm.provider openai      # 或 anthropic / ollama
memoryd config set llm.model    gpt-4o-mini
memoryd config set llm.api_key_env OPENAI_API_KEY
```

更详细的 LLM 配置看 [架构 · 治理](../architecture/governance.md) 最后一节。

## 七、一键挂三端 + 守护进程

```bash
memoryd setup auto-install
```

**应该看到：**

```json
{
  "platform": "macOS",
  "cron": "...",
  "cc_hook": "/Users/<you>/.claude/settings.json"
}
```

这条命令做了两件事：

1. 写 `~/.claude/settings.json` 的 SessionEnd hook —— 以后每次 Claude Code 会话结束自动 capture
2. 装 cron（macOS launchd / Linux systemd timer / Windows Task Scheduler）
    - daily 03:00 跑 decay-sweep
    - weekly Mon 09:00 跑 digest

!!! note "Codex 与 OpenClaw"
    `auto-install` 当前只挂 Claude Code 的 hook + cron。其他两端按各自页面单独挂：
    - [Codex 集成](../integrations/codex.md)
    - [OpenClaw 集成](../integrations/openclaw.md)

!!! tip "只想试试，不想动 ~/.claude/"
    跳过这一步。memoryd 不挂 hook 也能用，你手动 `memoryd capture` 就行。

## 八、把 MCP server 接进 Claude Code

挂了 hook 后 AI 还需要一个**主动查询记忆**的入口，这就是 MCP server。

```bash
# 1. 找到 memoryd-mcp 的绝对路径
which memoryd-mcp
# 输出例：/Users/<you>/memory-system/memoryd/.venv/bin/memoryd-mcp
```

```bash
# 2. 用 Python 安全 merge 到 ~/.claude.json（不要 echo 直接覆盖）
python3 <<'EOF'
import json
from pathlib import Path

path = Path.home() / ".claude.json"
d = json.loads(path.read_text()) if path.exists() else {}

d.setdefault("mcpServers", {})
d["mcpServers"]["memoryd"] = {
    "command": str(Path.home() / "memory-system/memoryd/.venv/bin/memoryd-mcp"),
    "args": [],
    "env": {}
}

tmp = path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False))
tmp.replace(path)
print(f"wrote {path}")
EOF
```

重启 Claude Code。在 CC 里敲 `/mcp`，应该看到 **memoryd** 这一项，下挂 13 个 `mem_*` 工具（admin 模式启动会更多）。

## 九、跑一遍冒烟（可选但推荐）

确认整个链路通：

```bash
# 写一条 manual session
echo '{"session_id":"hello","transcript_path":"","cwd":"'"$(pwd)"'"}' \
  | memoryd capture --source=manual

# 列出来
memoryd list --limit=5

# 搜回来
memoryd search "hello"

# 看一条详情
memoryd show 2026-XX-XX-hello       # 替换为上面 list 输出的 slug
```

四步都应该有输出，且第二、三、四步能找到刚写的那条。

## 十、跑测试套件（可选，给开发者）

```bash
cd ~/memory-system/memoryd
uv run pytest -v
```

500+ 个测试，全通过约 1–2 分钟。

> 📸 截图待补：终端跑完 `pytest` 看到全绿的样子（路径预留 `docs/assets/install-success.png`）。

## 常见错误

### `ModuleNotFoundError: No module named 'memoryd'`

venv 没激活，或激活的不是 memory-system 的那个。`which python3` 看看走的是哪条。

### `error: failed to build wheel for tokenizers / onnxruntime`

需要系统编译工具链：

- macOS：`xcode-select --install`
- Linux：`apt install build-essential python3-dev`

### `keyring.errors.NoKeyringError`

Linux 上没装 keyring 后端。两个选择：
- 装 `gnome-keyring` 或 `KeePassXC`
- 或走 passphrase 模式：`memoryd set-passphrase`（密码托管在自己脑子里）

### `Address already in use` 启动 web 时

8765 端口被占。换一个：

```bash
memoryd web --port=18765
```

### auto-install 之后 CC 没触发 hook

看 [故障排查](../operations/troubleshooting.md) 里 "CC 会话结束后没 capture" 节。

## 卸载

```bash
memoryd setup uninstall-cron --all
memoryd setup uninstall-launchd-mirror   # macOS 才需要

# 数据先备份再删（重要）
memoryd sync export                       # 备份 markdown 到同步盘
rm -rf ~/.local/share/memoryd
```

详见 [卸载](../operations/uninstall.md)。

## 下一步

- 5 分钟跑完闭环：[5 分钟快速开始](quickstart.md)
- 跑第一次的最小例子：[首次运行](first-run.md)
- 系统化学习：[教程系列](../tutorials/index.md)
- 理解概念：[核心概念](concepts.md)
