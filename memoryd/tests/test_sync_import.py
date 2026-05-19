from unittest.mock import MagicMock
from pathlib import Path

from memoryd.sync import ImportReport, import_, _STATE_FILENAME


def _make_md(root, scope, type_, slug, body="x"):
    p = root / "scopes" / scope / type_ / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_import_copies_new_files(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(sync_dir, "h1", "sessions", "a", body="hello")
    (data_root / "scopes").mkdir(parents=True, exist_ok=True)
    report = import_(data_root, sync_dir)
    assert report.copied == 1
    assert report.conflicts == 0
    dst = data_root / "scopes" / "h1" / "sessions" / "a.md"
    assert dst.exists() and dst.read_text() == "hello"


def test_import_skips_identical(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(sync_dir, "h1", "sessions", "a", body="same")
    _make_md(data_root, "h1", "sessions", "a", body="same")
    report = import_(data_root, sync_dir)
    assert report.copied == 0
    assert report.skipped == 1
    assert report.conflicts == 0


def test_import_dry_run_writes_nothing(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(sync_dir, "h1", "sessions", "a", body="hi")
    (data_root / "scopes").mkdir(parents=True, exist_ok=True)
    report = import_(data_root, sync_dir, dry_run=True)
    assert report.copied == 1
    assert not (data_root / "scopes" / "h1" / "sessions" / "a.md").exists()


def test_import_filters_by_scope(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(sync_dir, "h1", "sessions", "a")
    _make_md(sync_dir, "h2", "sessions", "b")
    (data_root / "scopes").mkdir(parents=True, exist_ok=True)
    report = import_(data_root, sync_dir, scope_hash="h1")
    assert report.copied == 1
    assert (data_root / "scopes" / "h1" / "sessions" / "a.md").exists()
    assert not (data_root / "scopes" / "h2" / "sessions" / "b.md").exists()


def test_import_triggers_rebuild_index(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(sync_dir, "h1", "sessions", "a")
    fake = MagicMock()
    monkeypatch.setattr("memoryd.sync._rebuild_index_quiet", fake)
    import_(data_root, sync_dir)
    fake.assert_called_once_with(data_root)


def test_import_no_rebuild_when_nothing_changed(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(sync_dir, "h1", "sessions", "a", body="same")
    _make_md(data_root, "h1", "sessions", "a", body="same")
    fake = MagicMock()
    monkeypatch.setattr("memoryd.sync._rebuild_index_quiet", fake)
    import_(data_root, sync_dir)
    fake.assert_not_called()


def test_import_handles_missing_sync_scopes_dir(tmp_path):
    """If sync dir doesn't have scopes/, import should be no-op."""
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    report = import_(data_root, sync_dir)
    assert report.copied == 0
    assert report.conflicts == 0
