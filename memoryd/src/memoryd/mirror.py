"""Filesystem mirror framework: watchdog handlers + _unscoped bucket helper.

`MirrorRouter` dispatches new files to per-suffix handlers (one for Codex
rollout summaries, one for OpenClaw session jsonl). Each handler is
responsible for parsing the file and producing a SessionMemory; this module
provides the common `save_to_scope_or_unscoped` so handlers don't need to
re-implement the fallback for files whose scope can't be resolved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .schema import SessionMemory
from .storage import save_session


UNSCOPED_HASH = "_unscoped"


def save_to_scope_or_unscoped(
    memory_root: Path,
    session: SessionMemory,
    *,
    resolved_scope_hash: str | None,
) -> Path:
    """Save a session under its resolved scope, or the _unscoped bucket.

    If `resolved_scope_hash` is None (handler couldn't reverse-lookup a
    scope), rewrite the frontmatter scope_hash to UNSCOPED_HASH so the
    file ends up under `<root>/scopes/_unscoped/sessions/` instead of a
    misleading hash.
    """
    target_hash = resolved_scope_hash or UNSCOPED_HASH
    if session.frontmatter.scope_hash != target_hash:
        session = session.model_copy(
            update={
                "frontmatter": session.frontmatter.model_copy(
                    update={"scope_hash": target_hash}
                )
            }
        )
    return save_session(memory_root, session)


FileHandler = Callable[[Path], None]


@dataclass
class MirrorRouter:
    """Dispatch new files to per-suffix handlers."""

    _handlers: dict[str, FileHandler] = field(default_factory=dict)

    def register(self, *, suffix: str, handler: FileHandler) -> None:
        self._handlers[suffix.lower()] = handler

    def dispatch(self, path: Path) -> None:
        handler = self._handlers.get(path.suffix.lower())
        if handler is None:
            return
        handler(path)
