from pathlib import Path

from memoryd.sync import import_


def _make_md(root, scope, type_, slug, body):
    p = root / "scopes" / scope / type_ / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_conflict_writes_local_to_conflicts_dir(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a", body="local-version")
    _make_md(sync_dir, "h1", "sessions", "a", body="sync-version")
    report = import_(data_root, sync_dir)
    assert report.conflicts == 1
    assert report.copied == 0
    # local file now holds sync version
    assert (data_root / "scopes" / "h1" / "sessions" / "a.md").read_text() == "sync-version"
    # local backup landed in _conflicts/
    conflicts_dir = data_root / "scopes" / "_conflicts"
    assert conflicts_dir.exists()
    backups = list(conflicts_dir.iterdir())
    assert len(backups) == 1
    assert backups[0].read_text() == "local-version"
    # name = "a.md-<8 hex>"
    assert backups[0].name.startswith("a.md-")
    assert len(backups[0].name.split("-")[-1]) == 8


def test_conflict_dry_run_writes_nothing(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a", body="local")
    _make_md(sync_dir, "h1", "sessions", "a", body="sync")
    report = import_(data_root, sync_dir, dry_run=True)
    assert report.conflicts == 1
    # local untouched
    assert (data_root / "scopes" / "h1" / "sessions" / "a.md").read_text() == "local"
    assert not (data_root / "scopes" / "_conflicts").exists()


def test_multiple_conflicts_separate_backups(tmp_path):
    data_root = tmp_path / "data"
    sync_dir = tmp_path / "sync"
    _make_md(data_root, "h1", "sessions", "a", body="local-a")
    _make_md(data_root, "h1", "sessions", "b", body="local-b")
    _make_md(sync_dir, "h1", "sessions", "a", body="sync-a")
    _make_md(sync_dir, "h1", "sessions", "b", body="sync-b")
    report = import_(data_root, sync_dir)
    assert report.conflicts == 2
    conflicts_dir = data_root / "scopes" / "_conflicts"
    backups = sorted(conflicts_dir.iterdir(), key=lambda p: p.name)
    assert len(backups) == 2
    assert backups[0].name.startswith("a.md-")
    assert backups[1].name.startswith("b.md-")
