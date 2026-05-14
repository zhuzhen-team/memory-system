"""storage.py sensitive-scope encryption tests (Task 3).

Fixture strategy
----------------
* `stub_keyring` (autouse): replaces enc._keyring with an in-memory dict so
  tests never touch macOS Keychain.
* `sensitive_env` fixture: opens a real in-memory-like tmp index DB, calls
  idx.register_sensitive_scope so the scope appears in the SQLite table.
  This is Task 3's responsibility — Task 4 CLI will later automate the
  mark-sensitive flow.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from memoryd import enc
from memoryd.index import open_index
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import load_session, save_memory


# ---------------------------------------------------------------------------
# In-memory keyring stub (same class as test_enc.py; reproduced to avoid
# cross-test-module imports).
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
    """Replace enc._keyring with in-memory stub for every test in this module."""
    fake = _InMemKeyring()
    monkeypatch.setattr(enc, "_keyring", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCOPE_HASH = "h_sensitive"


def _make_mem(slug: str = "2026-05-14-x") -> SessionMemory:
    return SessionMemory(
        frontmatter=Frontmatter(
            title="t",
            slug=slug,
            type="session",
            scope_hash=_SCOPE_HASH,
            source="manual",
            created_at=datetime(2026, 5, 14),
        ),
        body="SECRET CONTENT",
    )


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    """Prepare a data root with the scope registered as sensitive in SQLite."""
    root = tmp_path / ".data"
    root.mkdir()
    idx = open_index(root / "index.db")
    idx.register_sensitive_scope(_SCOPE_HASH, str(tmp_path))
    idx.close()
    return root


# ---------------------------------------------------------------------------
# Test 1: sensitive scope → .md.enc written (not .md)
# ---------------------------------------------------------------------------


def test_save_memory_writes_enc_file_in_sensitive_scope(data_root: Path):
    """When scope is sensitive, save_memory writes <slug>.md.enc, not .md."""
    mem = _make_mem()
    path = save_memory(data_root, mem)

    assert path.name.endswith(".md.enc"), f"expected .md.enc, got {path.name}"
    assert path.exists()
    # plaintext must NOT appear verbatim on disk
    assert b"SECRET CONTENT" not in path.read_bytes()


# ---------------------------------------------------------------------------
# Test 2: load_session decrypts .md.enc transparently
# ---------------------------------------------------------------------------


def test_load_session_decrypts_enc_file(data_root: Path):
    """load_session on a .md.enc file should return the original body."""
    mem = _make_mem()
    path = save_memory(data_root, mem)
    assert path.name.endswith(".md.enc")

    loaded = load_session(path)
    assert loaded.body == "SECRET CONTENT"
    assert loaded.frontmatter.slug == "2026-05-14-x"


# ---------------------------------------------------------------------------
# Test 3: non-sensitive scope → plain .md
# ---------------------------------------------------------------------------


def test_save_memory_writes_plain_md_in_nonsensitive_scope(tmp_path: Path):
    """When scope is not registered as sensitive, save_memory writes plain .md."""
    data_root = tmp_path / ".data"
    data_root.mkdir()
    # Do NOT register scope as sensitive — index table stays empty.
    mem = SessionMemory(
        frontmatter=Frontmatter(
            title="plain",
            slug="ns-slug",
            type="session",
            scope_hash="non_sensitive",
            source="manual",
            created_at=datetime(2026, 5, 14),
        ),
        body="PLAIN CONTENT",
    )
    path = save_memory(data_root, mem)

    assert path.suffix == ".md", f"expected .md, got {path.suffix}"
    assert ".enc" not in path.name
    assert path.read_text(encoding="utf-8").startswith("---")


# ---------------------------------------------------------------------------
# Test 4: SQLite scope_sensitive column set to 1 for sensitive scopes
# ---------------------------------------------------------------------------


def test_index_marks_scope_sensitive_column(data_root: Path):
    """After save_memory on a sensitive scope, memories.scope_sensitive = 1."""
    mem = _make_mem()
    save_memory(data_root, mem)

    idx = open_index(data_root / "index.db")
    row = idx.get_memory(mem.frontmatter.slug)
    idx.close()

    assert row is not None, "Row should exist in SQLite"
    assert row["scope_sensitive"] == 1, (
        f"expected scope_sensitive=1, got {row['scope_sensitive']}"
    )
