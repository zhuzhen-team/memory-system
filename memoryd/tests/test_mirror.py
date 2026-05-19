"""Mirror framework tests."""
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.mirror import (
    MirrorRouter,
    UNSCOPED_HASH,
    save_to_scope_or_unscoped,
)
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import list_sessions


def _build_session(scope_hash: str = "abc123", source: str = "codex-rollout") -> SessionMemory:
    return SessionMemory(
        frontmatter=Frontmatter(
            title="t",
            slug="2026-05-14-t",
            type="session",
            scope_hash=scope_hash,
            triggers=[],
            source=source,
            created_at=datetime(2026, 5, 14, 10, 0),
        ),
        body="b",
    )


def test_save_with_real_scope_lands_in_scope_dir(memory_root: Path):
    sess = _build_session(scope_hash="real_scope")
    path = save_to_scope_or_unscoped(memory_root, sess, resolved_scope_hash="real_scope")
    assert "real_scope" in str(path)
    assert UNSCOPED_HASH not in str(path)
    assert path.exists()


def test_save_with_none_scope_lands_in_unscoped(memory_root: Path):
    sess = _build_session()
    path = save_to_scope_or_unscoped(memory_root, sess, resolved_scope_hash=None)
    assert UNSCOPED_HASH in str(path)
    # frontmatter scope_hash should be rewritten to UNSCOPED_HASH
    from memoryd.storage import load_session
    loaded = load_session(path)
    assert loaded.frontmatter.scope_hash == UNSCOPED_HASH


def test_router_routes_by_suffix(memory_root: Path, tmp_path: Path):
    """MirrorRouter dispatches new files to a registered handler by suffix."""
    triggered: list[Path] = []

    def fake_handler(path: Path) -> None:
        triggered.append(path)

    router = MirrorRouter()
    router.register(suffix=".md", handler=fake_handler)

    test_file = tmp_path / "x.md"
    test_file.write_text("hello")
    router.dispatch(test_file)
    assert triggered == [test_file]


def test_router_ignores_unknown_suffix(tmp_path: Path):
    router = MirrorRouter()
    router.register(suffix=".md", handler=lambda p: None)
    triggered = []
    router.register(suffix=".jsonl", handler=lambda p: triggered.append(p))

    router.dispatch(tmp_path / "y.txt")
    assert triggered == []
