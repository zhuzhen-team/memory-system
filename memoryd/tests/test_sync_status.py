"""Tests for memoryd.sync.status() — per-scope counts + conflict tally."""
from pathlib import Path

from memoryd.sync import status


def _make_md(root, scope, type_, slug, body="x"):
    p = root / "scopes" / scope / type_ / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_status_lists_scopes_and_counts(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a")
    _make_md(data_root, "h1", "sessions", "b")
    _make_md(data_root, "h2", "decisions", "x")
    _make_md(sync_dir, "h1", "sessions", "a")
    _make_md(sync_dir, "h1", "sessions", "b")
    out = status(data_root, sync_dir)
    assert out["per_scope"]["h1"] == {"local": 2, "sync": 2}
    assert out["per_scope"]["h2"] == {"local": 1, "sync": 0}


def test_status_counts_conflicts(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a")
    (data_root / "scopes" / "_conflicts").mkdir(parents=True, exist_ok=True)
    (data_root / "scopes" / "_conflicts" / "a.md-deadbeef").write_text("local")
    (data_root / "scopes" / "_conflicts" / "b.md-cafebabe").write_text("local")
    out = status(data_root, sync_dir)
    assert out["conflicts"] == 2


def test_status_sync_dir_missing(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync-does-not-exist"
    _make_md(data_root, "h1", "sessions", "a")
    out = status(data_root, sync_dir)
    assert out["per_scope"]["h1"]["local"] == 1
    assert out["per_scope"]["h1"]["sync"] == 0
    assert out["conflicts"] == 0


def test_status_json_serializable(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a")
    out = status(data_root, sync_dir)
    import json
    s = json.dumps(out)
    assert "h1" in s


def test_cli_sync_status_no_dir_returns_2(tmp_path, monkeypatch, capsys):
    """When [sync] dir is empty, sync status exits 2 with stderr help."""
    from memoryd import cli
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("")
    monkeypatch.setattr("memoryd.config._config_path", lambda: cfg_file)
    rc = cli._cmd_sync_status(type("A", (), {"as_json": False})())
    assert rc == 2
