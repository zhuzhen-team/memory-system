"""mark/unmark-sensitive CLI integration tests (Task 4).

Fixture strategy
----------------
* ``stub_keyring`` (autouse): replaces enc._keyring with an in-memory dict so
  tests never touch macOS Keychain.
* Tests call cmd_mark_sensitive / cmd_unmark_sensitive directly (same process)
  so the monkeypatch stub stays effective.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pytest

from memoryd import enc
from memoryd.cli import cmd_mark_sensitive, cmd_unmark_sensitive
from memoryd.index import open_index
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.scope import resolve_scope_root, scope_hash
from memoryd.scope_meta import MARKER_FILENAME
from memoryd.storage import save_memory


# ---------------------------------------------------------------------------
# In-memory keyring stub
# ---------------------------------------------------------------------------


class _InMemKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.store[(service, account)] = password

    def delete_password(self, service: str, account: str) -> None:
        self.store.pop((service, account), None)


@pytest.fixture(autouse=True)
def stub_keyring(monkeypatch):
    fake = _InMemKeyring()
    monkeypatch.setattr(enc, "_keyring", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scope(tmp_path: Path) -> tuple[Path, Path, str]:
    """Create a fake scope dir and data root, return (scope_root, data_root, sh)."""
    scope_root = tmp_path / "myproject"
    scope_root.mkdir()
    data_root = tmp_path / "memoryd_data"
    data_root.mkdir()
    sh = scope_hash(scope_root)
    return scope_root, data_root, sh


def _save_plain_session(data_root: Path, sh: str, slug: str = "2026-05-14-plain") -> Path:
    """Write a plain (non-sensitive) .md file for the given scope."""
    mem = SessionMemory(
        frontmatter=Frontmatter(
            title="plain session",
            slug=slug,
            type="session",
            scope_hash=sh,
            source="manual",
            created_at=datetime(2026, 5, 14),
        ),
        body="SENSITIVE BODY CONTENT",
    )
    return save_memory(data_root, mem)


def _make_args(func_name: str, scope_path: str) -> argparse.Namespace:
    return argparse.Namespace(scope_path=scope_path)


# ---------------------------------------------------------------------------
# Test 1: mark-sensitive writes marker + encrypts existing .md
# ---------------------------------------------------------------------------


def test_mark_sensitive_writes_marker_and_encrypts(tmp_path, monkeypatch):
    scope_root, data_root, sh = _make_scope(tmp_path)
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(data_root))

    # Write a plain session first (scope not yet sensitive → plain .md)
    plain_path = _save_plain_session(data_root, sh)
    assert plain_path.suffix == ".md"
    assert plain_path.exists()

    # Run mark-sensitive
    args = _make_args("mark_sensitive", str(scope_root))
    rc = cmd_mark_sensitive(args)
    assert rc == 0

    # .memoryd-sensitive marker must exist at scope_root
    assert (scope_root / MARKER_FILENAME).exists()

    # Original .md must be gone; .md.enc must appear
    assert not plain_path.exists()
    enc_path = plain_path.with_suffix(".md.enc")
    assert enc_path.exists()

    # SQLite: scope_sensitive=1
    idx = open_index(data_root / "index.db")
    row = idx.get_memory("2026-05-14-plain")
    idx.close()
    assert row is not None
    assert row["scope_sensitive"] == 1


# ---------------------------------------------------------------------------
# Test 2: unmark-sensitive decrypts .md.enc back to .md
# ---------------------------------------------------------------------------


def test_unmark_sensitive_restores_plain_md(tmp_path, monkeypatch):
    scope_root, data_root, sh = _make_scope(tmp_path)
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(data_root))

    _save_plain_session(data_root, sh)

    # Mark
    args_mark = _make_args("mark_sensitive", str(scope_root))
    assert cmd_mark_sensitive(args_mark) == 0

    enc_path = data_root / "scopes" / sh / "sessions" / "2026-05-14-plain.md.enc"
    assert enc_path.exists()

    # Unmark
    args_unmark = _make_args("unmark_sensitive", str(scope_root))
    rc = cmd_unmark_sensitive(args_unmark)
    assert rc == 0

    # .md.enc gone; .md back
    assert not enc_path.exists()
    md_path = enc_path.with_name("2026-05-14-plain.md")
    assert md_path.exists()

    # Content readable (not garbage)
    content = md_path.read_text(encoding="utf-8")
    assert "SENSITIVE BODY CONTENT" in content

    # SQLite: scope_sensitive=0
    idx = open_index(data_root / "index.db")
    row = idx.get_memory("2026-05-14-plain")
    sensitive_scopes = idx.list_sensitive_scopes()
    idx.close()
    assert row["scope_sensitive"] == 0
    assert not any(s["scope_hash"] == sh for s in sensitive_scopes)

    # .memoryd-sensitive marker removed
    assert not (scope_root / MARKER_FILENAME).exists()


# ---------------------------------------------------------------------------
# Test 3: mark-sensitive is idempotent (second call is a no-op, no error)
# ---------------------------------------------------------------------------


def test_mark_sensitive_idempotent(tmp_path, monkeypatch):
    scope_root, data_root, sh = _make_scope(tmp_path)
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(data_root))

    _save_plain_session(data_root, sh)

    args = _make_args("mark_sensitive", str(scope_root))
    rc1 = cmd_mark_sensitive(args)
    assert rc1 == 0

    # Second call: scope is already marked → marker exists (mark_sensitive is
    # idempotent when existing == scope_root).  No .md files left to encrypt
    # (all already .md.enc), so the second call should succeed (rc=0).
    rc2 = cmd_mark_sensitive(args)
    assert rc2 == 0

    # Exactly one .md.enc (not double-encrypted)
    enc_files = list((data_root / "scopes" / sh / "sessions").glob("*.md.enc"))
    assert len(enc_files) == 1
    # No plain .md remaining
    md_files = list((data_root / "scopes" / sh / "sessions").glob("*.md"))
    assert len(md_files) == 0


# ---------------------------------------------------------------------------
# Test 4: mark-sensitive refuses when parent is already marked
# ---------------------------------------------------------------------------


def test_mark_sensitive_refuses_when_parent_marked(tmp_path, monkeypatch):
    """If parent directory already has a .memoryd-sensitive marker, CLI exits 1."""
    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / "child"
    child.mkdir()

    data_root = tmp_path / "memoryd_data"
    data_root.mkdir()
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(data_root))

    # Mark the parent first
    (parent / MARKER_FILENAME).write_text("scope_root: parent\n")

    # Trying to mark the child should fail (exit code 1)
    args = _make_args("mark_sensitive", str(child))
    rc = cmd_mark_sensitive(args)
    assert rc == 1
