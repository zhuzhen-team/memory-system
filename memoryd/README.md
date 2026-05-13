# memoryd

Personal memory governance MCP server. Part of `project-management-personal`.

**Status:** v0.1.0a0 — Walking Skeleton (plan 1 of 8)

Currently supports:
- macOS only
- Claude Code only (Codex / OpenClaw land in plan 2)
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
