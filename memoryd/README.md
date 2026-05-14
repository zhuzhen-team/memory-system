# memoryd

Personal memory governance MCP server. Part of `project-management-personal`.

**Status:** v0.1.0a0 — Walking Skeleton (plan 1 of 8)

Currently supports:
- macOS only
- **Claude Code, Codex, and OpenClaw three clients share a single scope** (was: Claude Code only)
- Single machine (multi-machine sync in plan 6)
- Session capture only (decisions/preferences/promotions in plan 3)
- Plain Markdown storage (encryption in plan 4)
- ripgrep-based search (semantic search in plan 3)

## Install (macOS)

Prereqs: Python 3.11+, [`uv`](https://github.com/astral-sh/uv), `ripgrep` (`brew install ripgrep`).

```bash
cd /path/to/project-management-personal/memoryd
uv venv
uv pip install -e ".[dev]"
```

## Wire into Claude Code

Add the following to `~/.claude/settings.json` (merge with existing keys):

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/project-management-personal/scripts/cc-session-end-hook.sh"
          }
        ]
      }
    ]
  }
}
```

Add the `memoryd` entry to `~/.claude.json` (the flat file at your home root — **not** `~/.claude/.mcp.json`, which CC ignores for user-level servers). Merge under the existing top-level `mcpServers` key using Python to avoid corrupting other entries (tokens, other servers):

```python
import json
from pathlib import Path

path = Path.home() / ".claude.json"
with open(path) as f:
    d = json.load(f)

d.setdefault("mcpServers", {})
d["mcpServers"]["memoryd"] = {
    "command": "/path/to/project-management-personal/memoryd/.venv/bin/memoryd-server",
    "args": [],
    "env": {
        "MEMORYD_DATA_ROOT": "/Users/<you>/.local/share/memoryd"
    }
}

tmp = path.with_suffix(".json.tmp")
with open(tmp, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
tmp.replace(path)
```

Restart Claude Code. Run `/mcp` and verify `memoryd` appears with `search_memory` tool.

## Wire into Codex（Plan 2.5 双通路）

> Codex.app 的 hooks engine 当前版本对所有事件零触发（已实测）；Plan 2.5
> 改走两条互补通路：notify wrapper 实时捕获 + 文件系统监听 rollout_summary。
> 旧的 `scripts/codex-stop-hook.sh` 已删除。

### 1. 备份并替换 notify 字段（实时通路）

```bash
# 完整切到 wrapper（先做一遍 probe 才知道 notify 真实 schema；
# 详见下面 Phase 1 手册）
/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd setup swap-codex-notify --to wrapper
```

子命令自动：
- 把 `~/.codex/config.toml` 备份到 `~/.claude/backups/`
- 用 Python tomllib 读，正则替换 `notify` 字段保留其他 keys
- 把原 notify target 存到 `~/.codex/.memoryd-notify-state.json`，便于 `--to original` 回滚

### 2. 删除死的 Stop hook 条目

```bash
memoryd setup remove-codex-stop-hook
```

### 3. 启动 FS-watch daemon（事后通路）

```bash
memoryd setup install-launchd-mirror
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.memoryd.mirror.plist
launchctl print gui/$(id -u)/com.memoryd.mirror  # 验证 daemon 在跑
```

Daemon 监听 `~/.codex/memories/rollout_summaries/`；Codex.app 每个 session 结束后
会自己往那里写一份 summary `.md`，daemon 把它转码成 memoryd 的 `source=codex-rollout`
记忆条目。

### 4. 验证

跑一轮 Codex.app turn，检查：

```bash
# 实时通路日志
tail ~/.local/share/memoryd/logs/codex-notify.log

# FS-watch 通路日志
tail ~/.local/share/memoryd/logs/mirror.stderr.log

# 新生成的 memoryd 条目
find ~/.local/share/memoryd/scopes -name "*.md" -newer /tmp -ls
```

## Wire into OpenClaw（Plan 2.5 双通路）

> OpenClaw 2026.5.7 的插件 SDK 是 `definePluginEntry` + `registerAgentEventSubscription`，
> 不再支持旧的 `api.on('agent_end', ...)`。Plan 2.5 重写插件入口；同时让 launchd
> daemon 监听 `~/.openclaw/agents/*/sessions/` 作 fallback。

### 1. 安装插件

```bash
cd /Users/abble/project-management-personal/scripts/openclaw-memoryd-plugin
openclaw plugins install --force .
openclaw plugins list | grep memoryd-openclaw
```

### 2. 授权对话访问

```bash
# 用 install 输出的 entry key（通常就是 memoryd-openclaw）
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowConversationAccess true
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowPromptInjection false
```

### 3. FS-watch daemon

Codex 那一步装的 launchd plist 已经同时覆盖 OpenClaw 路径（`--codex --openclaw` 双开）；无需额外操作。

### 4. 验证

跑一轮 OpenClaw turn，检查：

```bash
tail ~/.local/share/memoryd/logs/openclaw-events.log  # SDK 通路诊断
find ~/.local/share/memoryd/scopes -newer /tmp -name "*.md" -ls
```

`source: openclaw`（SDK 实时）或 `source: openclaw-fs`（FS-watch）。

## Layout

```
src/memoryd/
  schema.py          # Pydantic Markdown frontmatter schema
  scope.py           # cwd → scope_hash (git-root preferred)
  storage.py         # save/load/list session markdowns
  search.py          # ripgrep-based search
  server.py          # FastMCP server with search_memory tool
  cli.py             # `memoryd capture` / `mirror` / `setup` subcommands
  mirror.py          # watchdog handler framework + _unscoped bucket（Plan 2.5）
  mirror_codex.py    # Codex rollout_summary 转码（source=codex-rollout，Plan 2.5）
  mirror_openclaw.py # OpenClaw session jsonl 转码（source=openclaw-fs，Plan 2.5）
  setup.py           # 用户配置管理：notify swap / hooks 清理 / launchd 安装（Plan 2.5）

tests/
  test_schema.py
  test_scope.py
  test_storage.py
  test_search.py
  test_cli.py
  test_server.py
  test_mirror.py           # Plan 2.5
  test_mirror_codex.py     # Plan 2.5
  test_mirror_openclaw.py  # Plan 2.5
  test_setup.py            # Plan 2.5
```

Memory data root (default `~/.local/share/memoryd`):

```
scopes/
  <scope_hash>/
    sessions/
      2026-05-09-<session-id>.md
  _unscoped/                  # 反推不到 scope 时兜底（Plan 2.5）
    sessions/...
logs/
  cc-session-end.log
  codex-notify.log            # Plan 2.5 实时通路
  openclaw-events.log         # Plan 2.5 OpenClaw SDK 事件
  mirror.stdout.log           # Plan 2.5 launchd daemon
  mirror.stderr.log
probe/
  notify-probe.log            # Plan 2.5 Phase 1 探针
```

## Run tests

```bash
uv run pytest -v
```

## Limitations of v1.0-α

- Naive truncation summary (no LLM call). Plan 3 replaces with 4-criteria filter.
- No SQLite index — all search via ripgrep. Plan 3 adds SQLite for type filters.
- No encryption. Don't put secrets here in plan 1.
- No sync. Single-machine only.

See `docs/superpowers/plans/2026-05-09-v1-alpha-walking-skeleton.md` for full plan history; subsequent plans live alongside it.
