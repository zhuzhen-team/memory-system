import json
from pathlib import Path

import pytest

from memoryd.sync import (
    _STATE_FILENAME,
    expand_sync_dir,
    iter_local_markdown,
    read_state,
    relative_key,
    write_state,
)


def test_expand_sync_dir_expands_tilde(tmp_path, monkeypatch):
    # Path.expanduser() reads $HOME on POSIX, not Path.home(); patch the env var.
    monkeypatch.setenv("HOME", str(tmp_path))
    out = expand_sync_dir("~/foo")
    # .resolve() normalises symlinks (e.g. /tmp -> /private/tmp on macOS).
    assert out == (tmp_path / "foo").resolve()


def test_iter_local_markdown_skips_blacklist(tmp_path):
    root = tmp_path
    (root / "scopes" / "h1" / "sessions").mkdir(parents=True)
    (root / "scopes" / "h1" / "sessions" / "a.md").write_text("x")
    (root / "scopes" / "h1" / "sessions" / "b.md.enc").write_text("y")
    (root / "scopes" / "h1" / ".memoryd-sensitive").write_text("scope_root: /x")
    # blacklisted
    (root / "scopes" / "h1" / "index.db").write_text("z")
    (root / "scopes" / "h1" / "audit").mkdir()
    (root / "scopes" / "h1" / "audit" / "audit.jsonl").write_text("{}")
    (root / "scopes" / "h1" / "logs").mkdir()
    (root / "scopes" / "h1" / "logs" / "x.log").write_text("log")
    out = list(iter_local_markdown(root))
    names = sorted(p.name for p in out)
    assert names == [".memoryd-sensitive", "a.md", "b.md.enc"]


def test_read_write_state_roundtrip(tmp_path):
    state = {"h1/sessions/a.md": "abc123"}
    write_state(tmp_path, state)
    assert (tmp_path / _STATE_FILENAME).exists()
    assert read_state(tmp_path) == state


def test_read_state_returns_empty_when_corrupt(tmp_path):
    (tmp_path / _STATE_FILENAME).write_text("{not json")
    assert read_state(tmp_path) == {}


def test_relative_key_uses_forward_slash(tmp_path):
    root = tmp_path
    (root / "scopes" / "h1" / "sessions").mkdir(parents=True)
    p = root / "scopes" / "h1" / "sessions" / "a.md"
    p.touch()
    assert relative_key(root, p) == "h1/sessions/a.md"


def test_iter_local_markdown_handles_missing_scopes_dir(tmp_path):
    """If scopes/ doesn't exist yet, yield nothing."""
    out = list(iter_local_markdown(tmp_path))
    assert out == []
