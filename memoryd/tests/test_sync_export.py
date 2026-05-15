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


from memoryd.sync import _fingerprint, export


def _make_md(root, scope, type_, slug, body="x"):
    p = root / "scopes" / scope / type_ / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_export_copies_new_md(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a", body="hello")
    report = export(data_root, sync_dir)
    assert report.copied == 1
    assert report.skipped == 0
    dst = sync_dir / "scopes" / "h1" / "sessions" / "a.md"
    assert dst.exists()
    assert dst.read_text() == "hello"


def test_export_skips_unchanged(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a", body="hello")
    export(data_root, sync_dir)
    # second run: nothing changed
    report = export(data_root, sync_dir)
    assert report.copied == 0
    assert report.skipped == 1


def test_export_dry_run_writes_nothing(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a")
    report = export(data_root, sync_dir, dry_run=True)
    assert report.copied == 1
    assert not (sync_dir / "scopes" / "h1" / "sessions" / "a.md").exists()
    # state file NOT written either
    from memoryd.sync import _STATE_FILENAME
    assert not (sync_dir / _STATE_FILENAME).exists()


def test_export_filters_by_scope(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a")
    _make_md(data_root, "h2", "sessions", "b")
    report = export(data_root, sync_dir, scope_hash="h1")
    assert report.copied == 1
    assert (sync_dir / "scopes" / "h1" / "sessions" / "a.md").exists()
    assert not (sync_dir / "scopes" / "h2" / "sessions" / "b.md").exists()


def test_export_skips_blacklist(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a")
    # poisoned blacklist files
    (data_root / "scopes" / "h1" / "index.db").write_text("db")
    (data_root / "scopes" / "h1" / "audit").mkdir()
    (data_root / "scopes" / "h1" / "audit" / "audit.jsonl").write_text("{}")
    (data_root / "scopes" / "h1" / "logs").mkdir()
    (data_root / "scopes" / "h1" / "logs" / "x.log").write_text("log")
    report = export(data_root, sync_dir)
    assert report.copied == 1
    assert not (sync_dir / "scopes" / "h1" / "index.db").exists()
    assert not (sync_dir / "scopes" / "h1" / "audit").exists()
    assert not (sync_dir / "scopes" / "h1" / "logs").exists()


def test_export_state_file_persisted(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a")
    export(data_root, sync_dir)
    from memoryd.sync import _STATE_FILENAME
    state = (sync_dir / _STATE_FILENAME).read_text()
    import json
    parsed = json.loads(state)
    assert "h1/sessions/a.md" in parsed


def test_export_picks_up_modified_file(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    p = _make_md(data_root, "h1", "sessions", "a", body="v1")
    export(data_root, sync_dir)
    p.write_text("v2", encoding="utf-8")
    report = export(data_root, sync_dir)
    assert report.copied == 1
    assert (sync_dir / "scopes" / "h1" / "sessions" / "a.md").read_text() == "v2"
