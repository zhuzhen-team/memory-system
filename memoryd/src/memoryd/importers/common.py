"""Shared helpers for importers."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


_SLUG_BAD = re.compile(r"[^a-z0-9-]")


def kebab(text: str, max_len: int = 60) -> str:
    s = text.lower()
    s = re.sub(r"\s+", "-", s)
    s = _SLUG_BAD.sub("", s)
    return s[:max_len].strip("-") or "untitled"


def short_hash(text: str, n: int = 8) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ImportEntry:
    slug: str
    type: str
    title: str
    body: str
    triggers: list[str]
    source: str
    created_at: str


@dataclass
class ImportReport:
    parsed: int = 0
    written: int = 0
    skipped: int = 0
    by_type: dict = field(default_factory=dict)
    dry_run: bool = False


def _type_dir(t: str) -> str:
    return {
        "session": "sessions",
        "decision": "decisions",
        "preference": "preferences",
        "fact": "facts",
        "playbook": "playbooks",
        "warning": "warnings",
    }.get(t, "facts")


def write_entry(
    data_root: Path,
    scope_hash: str,
    entry: ImportEntry,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """Write a single ImportEntry via storage.save_memory. Returns True if written.

    Dry-run returns True without touching disk. When a file already exists at
    the target path, returns False unless ``force=True``.
    """
    if dry_run:
        return True
    from ..schema import Frontmatter, SessionMemory
    from ..storage import save_memory
    target = (
        data_root / "scopes" / scope_hash / _type_dir(entry.type)
        / f"{entry.slug}.md"
    )
    if target.exists() and not force:
        return False
    fm = Frontmatter(
        title=entry.title,
        slug=entry.slug,
        scope_hash=scope_hash,
        type=entry.type,
        triggers=entry.triggers,
        source=entry.source,
        created_at=entry.created_at,
    )
    save_memory(data_root, SessionMemory(frontmatter=fm, body=entry.body))
    return True
