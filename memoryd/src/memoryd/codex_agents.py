"""Codex AGENTS.md auto-injection.

Codex CLI reads ``~/.codex/AGENTS.md`` (and project-root ``AGENTS.md``) as
its system prompt on every turn. Unlike Claude Code, Codex has **no
SessionStart hook** to dynamically inject memoryd identity — so we instead
write a managed section into the file, bounded by markers we own:

    <!-- BEGIN memoryd:auto-include -->
    ## memoryd 画像（自动同步，{timestamp}）
    ...
    <!-- END memoryd:auto-include -->

Refreshing rewrites only this block, leaving the rest of the user's
AGENTS.md untouched. If AGENTS.md does not yet exist we create it; if it
exists but has no marker, we append the block at the bottom.

Called by:
  * ``memoryd setup install-codex-agents-include`` (one-shot)
  * cron job ``codex_agents_refresh`` (daily 03:00 — picks up new identity)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_BEGIN = "<!-- BEGIN memoryd:auto-include -->"
_END = "<!-- END memoryd:auto-include -->"


def _resolve_codex_agents_path(codex_dir: Path | None = None) -> Path:
    """Default ~/.codex/AGENTS.md; override for tests via codex_dir param."""
    base = codex_dir or (Path.home() / ".codex")
    return base / "AGENTS.md"


def render_codex_block(
    *,
    identity_max_chars: int = 1500,
    top_entities_limit: int = 12,
    recent_memories_limit: int = 8,
    scope: str | None = None,
    data_root: Path | None = None,
) -> str:
    """Build the markdown block that goes between the BEGIN/END markers.

    Reuses ``memoryd.inject.render_session_context`` for the content body
    so Codex sees exactly what Claude Code sees at SessionStart.
    """
    from .inject import render_session_context
    body = render_session_context(
        scope=scope,
        identity_max_chars=identity_max_chars,
        top_entities_limit=top_entities_limit,
        recent_memories_limit=recent_memories_limit,
        data_root=data_root,
    )
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = (
        f"{_BEGIN}\n"
        f"<!-- memoryd refreshes this section automatically. "
        f"Last update: {ts}. "
        f"Run `memoryd setup install-codex-agents-include` to refresh now. -->\n\n"
    )
    return header + body.rstrip() + f"\n\n{_END}\n"


def install_codex_agents_include(
    *,
    codex_dir: Path | None = None,
    data_root: Path | None = None,
    identity_max_chars: int = 1500,
    top_entities_limit: int = 12,
    recent_memories_limit: int = 8,
) -> Path:
    """Create or refresh the memoryd block inside ``~/.codex/AGENTS.md``.

    Idempotent: replaces the block between markers if present, otherwise
    appends. Returns the path written. If ``~/.codex/`` does not exist we
    create it so users who haven't yet bootstrapped Codex still get a valid
    file ready for their first ``codex`` run.
    """
    target = _resolve_codex_agents_path(codex_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    block = render_codex_block(
        identity_max_chars=identity_max_chars,
        top_entities_limit=top_entities_limit,
        recent_memories_limit=recent_memories_limit,
        data_root=data_root,
    )

    if target.exists():
        original = target.read_text(encoding="utf-8")
    else:
        original = "# AGENTS.md\n\n（memoryd 自动维护以下记忆区段，可随意编辑其他内容。）\n\n"

    new_text = _replace_or_append_block(original, block)
    _atomic_write(target, new_text)
    return target


def uninstall_codex_agents_include(*, codex_dir: Path | None = None) -> bool:
    """Strip the BEGIN/END block from AGENTS.md without touching anything else.

    Returns True if a block was removed; False if there was nothing to remove.
    """
    target = _resolve_codex_agents_path(codex_dir)
    if not target.exists():
        return False
    original = target.read_text(encoding="utf-8")
    stripped = _strip_block(original)
    if stripped == original:
        return False
    _atomic_write(target, stripped)
    return True


def _replace_or_append_block(original: str, block: str) -> str:
    """Replace the existing BEGIN..END block, or append at the end."""
    begin_idx = original.find(_BEGIN)
    end_idx = original.find(_END, begin_idx + 1) if begin_idx != -1 else -1
    if begin_idx != -1 and end_idx != -1:
        # replace including the END line + any trailing newline
        after = original[end_idx + len(_END):]
        # consume one optional trailing newline so we don't accumulate blank lines
        if after.startswith("\n"):
            after = after[1:]
        return original[:begin_idx].rstrip() + "\n\n" + block + after
    sep = "" if original.endswith("\n") else "\n"
    return original + sep + "\n" + block


def _strip_block(original: str) -> str:
    begin_idx = original.find(_BEGIN)
    end_idx = original.find(_END, begin_idx + 1) if begin_idx != -1 else -1
    if begin_idx == -1 or end_idx == -1:
        return original
    after = original[end_idx + len(_END):]
    if after.startswith("\n"):
        after = after[1:]
    return original[:begin_idx].rstrip() + ("\n" if original.endswith("\n") else "") + after


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


__all__ = [
    "install_codex_agents_include",
    "uninstall_codex_agents_include",
    "render_codex_block",
]
