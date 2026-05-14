"""Grant token management for sensitive scope authorization.

Token file: ~/.local/share/memoryd/grants/<scope_hash>.json
Schema: scope_hash, scope_root, duration, expires_at, issued_at,
        issued_by, task_id

Duration → expires_at:
  once    : now + 90 seconds
  session : now + 8 hours
  task    : 9999-12-31 (revoke required to expire)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal


Duration = Literal["once", "session", "task"]


def grants_dir() -> Path:
    root = os.environ.get("MEMORYD_DATA_ROOT")
    base = Path(root) if root else (Path.home() / ".local" / "share" / "memoryd")
    return base / "grants"


def grant_path(scope_hash: str) -> Path:
    return grants_dir() / f"{scope_hash}.json"


def write_grant(
    scope_hash: str,
    scope_root: str,
    duration: Duration,
    *,
    task_id: str | None = None,
    issued_by: str = "memoryd grant",
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    if duration == "once":
        expires = now + timedelta(seconds=90)
    elif duration == "session":
        expires = now + timedelta(hours=8)
    elif duration == "task":
        expires = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    else:
        raise ValueError(f"invalid duration: {duration!r}")

    grant = {
        "scope_hash": scope_hash,
        "scope_root": scope_root,
        "duration": duration,
        "expires_at": expires.isoformat(),
        "issued_at": now.isoformat(),
        "issued_by": issued_by,
        "task_id": task_id,
    }
    p = grant_path(scope_hash)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(grant, indent=2), encoding="utf-8")
    tmp.replace(p)
    return grant


def read_grant(scope_hash: str) -> dict | None:
    p = grant_path(scope_hash)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_grant_valid(grant: dict, *, now: datetime | None = None) -> bool:
    if grant is None:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(grant["expires_at"])
    except (KeyError, ValueError):
        return False
    return now < expires


def revoke_grant(scope_hash: str, *, task_id: str | None = None) -> bool:
    """Delete grant file. If task_id given, only delete if grant's task_id matches."""
    p = grant_path(scope_hash)
    if not p.exists():
        return False
    if task_id is not None:
        cur = read_grant(scope_hash)
        if cur is None or cur.get("task_id") != task_id:
            return False
    p.unlink()
    return True
