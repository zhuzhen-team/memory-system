"""Shared helpers for the memoryd MCP tool handlers.

Centralizes:
- data root resolution (mirrors ``cli._data_root``)
- scope auto-detection ("auto" → derive from cwd)
- error wrapping so tool handlers return uniform ``{"ok": bool, ...}`` shapes
  instead of leaking raw exceptions across the MCP boundary
- common timestamp / slug utilities
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..scope import resolve_scope_root, scope_hash as _scope_hash


DEFAULT_DATA_ROOT = Path.home() / ".local" / "share" / "memoryd"

_LONG_TERM_TYPES = ("decision", "preference", "fact", "playbook", "warning")
_ALL_TYPES = ("session", *_LONG_TERM_TYPES)
_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]")


def data_root() -> Path:
    """Resolve memoryd's on-disk data directory.

    Honours ``MEMORYD_DATA_ROOT`` (used by tests and per-user overrides);
    falls back to ``~/.local/share/memoryd``.
    """
    override = os.environ.get("MEMORYD_DATA_ROOT")
    if override:
        return Path(override)
    return DEFAULT_DATA_ROOT


def default_scope() -> str | None:
    """Optional fallback scope used when a tool caller passes ``scope="auto"``
    and the cwd is not under any git-rooted folder.
    """
    return os.environ.get("MEMORYD_DEFAULT_SCOPE") or None


def derive_scope(scope: str = "auto", cwd: Path | None = None) -> str:
    """Resolve a scope argument into a 12-char scope_hash.

    Rules:
    - ``scope == "auto"``: walk up from cwd to the nearest ``.git`` ancestor;
      hash that path. If no git root + ``MEMORYD_DEFAULT_SCOPE`` set, use it.
    - Otherwise: treat as a literal scope_hash and return verbatim.

    Test helpers can pass ``cwd`` to bypass ``Path.cwd()``.
    """
    if scope and scope != "auto":
        return scope
    fallback = default_scope()
    here = cwd or Path.cwd()
    try:
        root = resolve_scope_root(here)
        # If resolve_scope_root returned cwd itself (no .git found) AND a
        # MEMORYD_DEFAULT_SCOPE is set, prefer that — tests rely on this.
        if not (root / ".git").exists() and fallback:
            return fallback
        return _scope_hash(root)
    except Exception:
        if fallback:
            return fallback
        raise ValueError("could not derive scope_hash from cwd; pass scope explicitly")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def safe_slug(title: str, *, prefix_date: bool = True, max_title: int = 40) -> str:
    """Generate a filesystem-safe slug for a memory file.

    Format: ``YYYY-MM-DD-{sanitized-title}-{unix-ts}``. The unix-ts suffix
    keeps slugs unique even if the same title is captured multiple times
    within a single day.
    """
    cleaned = _SAFE_SLUG_RE.sub("_", title or "untitled")[:max_title].strip("_") or "untitled"
    ts = int(time.time())
    if prefix_date:
        d = now_utc().strftime("%Y-%m-%d")
        return f"{d}-{cleaned}-{ts}"
    return f"{cleaned}-{ts}"


def ok(**payload: Any) -> dict[str, Any]:
    """Success envelope for tool responses."""
    return {"ok": True, **payload}


def fail(message: str, *, code: str = "error", **extra: Any) -> dict[str, Any]:
    """Failure envelope. Tools should return this instead of raising whenever
    the failure is *expected* (missing memory, unknown scope, etc.) — MCP
    callers cannot inspect Python exceptions cleanly.
    """
    return {"ok": False, "error": {"code": code, "message": message, **extra}}


def open_db() -> sqlite3.Connection:
    """Return a sqlite3 connection on the index DB, with Row factory.

    Goes through :func:`memoryd.index.open_index` so pending SQL migrations
    are applied on first use; the returned connection is the underlying
    one stripped of the ``Index`` wrapper because handlers typically just
    want raw SQL access.
    """
    from ..index import open_index

    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    idx = open_index(root / "index.db")
    return idx.conn


def long_term_types() -> tuple[str, ...]:
    return _LONG_TERM_TYPES


def all_types() -> tuple[str, ...]:
    return _ALL_TYPES


def is_long_term(type_: str) -> bool:
    return type_ in _LONG_TERM_TYPES


__all__ = [
    "DEFAULT_DATA_ROOT",
    "all_types",
    "data_root",
    "default_scope",
    "derive_scope",
    "fail",
    "is_long_term",
    "long_term_types",
    "now_utc",
    "ok",
    "open_db",
    "safe_slug",
]
