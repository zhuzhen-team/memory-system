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

## Wire into Codex

> Codex doesn't have a `SessionEnd` event; we hook the `Stop` event, which
> fires when a turn finishes. Multiple turns in one Codex session will
> overwrite the same `<date>-<session_id>.md` file — the last turn's
> summary wins. Long-term governance (incl. per-turn slugs or merged
> summaries) lands in plan 3.

1. Backup your current `~/.codex/hooks.json`:
   ```bash
   mkdir -p ~/.claude/backups
   cp ~/.codex/hooks.json ~/.claude/backups/codex.hooks.json.bak.$(date +%Y%m%d-%H%M%S) 2>/dev/null || echo "no existing hooks.json"
   ```

2. Merge the Stop hook into `~/.codex/hooks.json` (use Python so other hooks survive):
   ```python
   import json
   from pathlib import Path
   p = Path.home() / ".codex" / "hooks.json"
   p.parent.mkdir(parents=True, exist_ok=True)
   d = json.loads(p.read_text()) if p.exists() else {}
   d.setdefault("hooks", {}).setdefault("Stop", []).append({
       "hooks": [{
           "type": "command",
           "command": "/path/to/project-management-personal/scripts/codex-stop-hook.sh",
           "async": True,
           "statusMessage": "memoryd capture (codex)"
       }]
   })
   tmp = p.with_suffix(".json.tmp")
   tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False))
   tmp.replace(p)
   ```

3. Register memoryd as an MCP server in `~/.codex/config.toml`. This is the
   recall side — without it Codex can write memories (via the Stop hook)
   but cannot call `search_memory` to read them. Append (don't replace —
   `config.toml` likely already has other `[mcp_servers.*]` tables):
   ```toml
   [mcp_servers.memoryd]
   command = "/path/to/project-management-personal/memoryd/.venv/bin/memoryd-server"
   args = []

   [mcp_servers.memoryd.env]
   MEMORYD_DATA_ROOT = "/Users/<you>/.local/share/memoryd"
   ```
   Validate the file after editing with `python3 -c "import tomllib; tomllib.loads(open('/Users/<you>/.codex/config.toml').read())"` (Python 3.11+).

4. Restart Codex. Run any turn; check `~/.local/share/memoryd/logs/codex-stop.log` for `ok`. In a new Codex turn, verify the agent can use the `search_memory` tool (the MCP wiring is live).

## Wire into OpenClaw

> The OpenClaw plugin lives under `scripts/openclaw-memoryd-plugin/`. It
> registers on the `agent_end` lifecycle hook and requires the
> `allowConversationAccess` permission to read turn data.

1. Install the plugin:
   ```bash
   cd /path/to/project-management-personal/scripts/openclaw-memoryd-plugin
   openclaw plugins install --force .
   ```

2. Grant conversation read permission (the plugin does NOT inject prompts):
   ```bash
   # Replace <ENTRY_KEY> with what `openclaw plugins install` printed
   openclaw config set plugins.entries.<ENTRY_KEY>.hooks.allowConversationAccess true
   openclaw config set plugins.entries.<ENTRY_KEY>.hooks.allowPromptInjection false
   ```

3. Run any OpenClaw turn; check `~/.local/share/memoryd/logs/openclaw-agent-end.log` for `ok`.

**Note on OpenClaw backend agent:** Whatever backend agent OpenClaw routes
your messages to (Claude Code, GPT, etc.), this plugin captures the turn
from OpenClaw's view — so memories written via OpenClaw appear with
`source: openclaw`, distinct from the same backend's native source tag.

## Layout

```
src/memoryd/
  schema.py    # Pydantic Markdown frontmatter schema
  scope.py     # cwd → scope_hash (git-root preferred)
  storage.py   # save/load/list session markdowns
  search.py    # ripgrep-based search
  server.py    # FastMCP server with search_memory tool
  cli.py       # `memoryd capture` for hook scripts

tests/
  test_schema.py
  test_scope.py
  test_storage.py
  test_search.py
  test_cli.py
  test_server.py
```

Memory data root (default `~/.local/share/memoryd`):

```
scopes/
  <scope_hash>/
    sessions/
      2026-05-09-<session-id>.md
logs/
  cc-session-end.log
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
