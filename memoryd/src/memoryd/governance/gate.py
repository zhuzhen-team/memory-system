"""Authorization gate for sensitive scope access.

Every MCP tool invocation must call gate.check_or_raise(scope_hash, tool)
before serving data. If no valid grant: try interactive prompt if
MEMORYD_AUTH_INTERACTIVE=1 + /dev/tty available; otherwise raise
AuthorizationRequired.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .audit import append_event
from .grants import is_grant_valid, read_grant, write_grant


class AuthorizationRequired(Exception):
    """Raised when a sensitive scope read lacks a valid grant."""


def is_sensitive(scope_hash: str, memory_root) -> bool:
    """Query SQLite sensitive_scopes table."""
    from ..index import open_index
    from pathlib import Path
    idx = open_index(Path(memory_root) / "index.db")
    try:
        return idx.is_scope_sensitive(scope_hash)
    finally:
        idx.close()


def check_or_raise(scope_hash: str, tool: str, *, memory_root=None) -> None:
    """Read grant; if no valid grant, attempt interactive prompt, else raise."""
    from pathlib import Path
    if memory_root is None:
        memory_root = Path(os.environ.get("MEMORYD_DATA_ROOT") or
                           (Path.home() / ".local" / "share" / "memoryd"))
    if not is_sensitive(scope_hash, memory_root):
        return  # not sensitive, no gate needed
    grant = read_grant(scope_hash)
    if grant and is_grant_valid(grant):
        append_event({
            "scope_hash": scope_hash,
            "event_type": "access_granted",
            "tool": tool,
            "duration": grant.get("duration"),
            "result": "ok",
        })
        return
    if os.environ.get("MEMORYD_AUTH_INTERACTIVE") == "1":
        choice = interactive_prompt(scope_hash)
        if choice is not None:
            # auto-write grant
            from ..index import open_index
            idx = open_index(Path(memory_root) / "index.db")
            try:
                row = idx.conn.execute(
                    "SELECT scope_root FROM sensitive_scopes WHERE scope_hash = ?",
                    (scope_hash,),
                ).fetchone()
                scope_root = row[0] if row else "<unknown>"
            finally:
                idx.close()
            write_grant(scope_hash, scope_root, choice, issued_by="interactive prompt")
            append_event({
                "scope_hash": scope_hash,
                "event_type": "access_granted",
                "tool": tool,
                "duration": choice,
                "result": "ok",
                "reason": "interactive",
            })
            return
    append_event({
        "scope_hash": scope_hash,
        "event_type": "access_denied",
        "tool": tool,
        "result": "denied",
    })
    raise AuthorizationRequired(
        f"sensitive scope {scope_hash} requires grant; "
        f"run `memoryd grant <scope_path> --duration once|session|task`"
    )


def interactive_prompt(scope_hash: str) -> str | None:
    """Prompt user via /dev/tty for grant duration. Return choice or None."""
    try:
        tty = open("/dev/tty", "r+")
    except OSError:
        return None
    try:
        tty.write(f"\n🔒 智能体请求读取 sensitive scope {scope_hash[:12]}\n")
        tty.write("授权范围？\n  [1] 仅本次回答 (90s)\n  [2] 整个本会话 (8h)\n  [3] 本任务 (永不到期)\n  [4] 拒绝\n> ")
        tty.flush()
        ans = tty.readline().strip()
        if ans == "1":
            return "once"
        elif ans == "2":
            return "session"
        elif ans == "3":
            return "task"
        return None
    finally:
        tty.close()
