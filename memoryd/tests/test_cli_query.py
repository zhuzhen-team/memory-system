"""Plan 9 Task 1: CLI search / list / show subcommands."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from memoryd import cli
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_session


def _save(
    root: Path,
    *,
    slug: str,
    scope: str = "h1",
    type_: str = "session",
    body: str = "body",
    triggers: list[str] | None = None,
    title: str | None = None,
) -> Path:
    """Save a memory via the real storage helpers so SQLite stays in sync."""
    mem = SessionMemory(
        frontmatter=Frontmatter(
            title=title or slug,
            slug=slug,
            type=type_,
            scope_hash=scope,
            triggers=triggers or [],
            source="test",
            created_at=datetime(2026, 5, 9, 9, 30),
        ),
        body=body,
    )
    return save_session(root, mem)


def _args(**kwargs: object) -> object:
    return type("Args", (), kwargs)()


def test_cli_search_finds_match(tmp_path, monkeypatch, capsys):
    _save(tmp_path, slug="a", body="logo direction blue silver")
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(query="logo", scope=None, type_=None, limit=20, as_json=False)
    rc = cli.cmd_search(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "a" in out


def test_cli_search_json_output(tmp_path, monkeypatch, capsys):
    _save(tmp_path, slug="abc", body="hello world")
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(query="hello", scope=None, type_=None, limit=20, as_json=True)
    rc = cli.cmd_search(args)
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) >= 1
    assert parsed[0]["slug"] == "abc"


def test_cli_search_returns_0_on_no_hits(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(
        query="missingxyz", scope=None, type_=None, limit=20, as_json=False,
    )
    rc = cli.cmd_search(args)
    assert rc == 0


def test_cli_list_shows_memories(tmp_path, monkeypatch, capsys):
    _save(tmp_path, slug="alpha")
    _save(tmp_path, slug="beta", type_="decision")
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(type_=None, scope=None, limit=50, as_json=False)
    rc = cli.cmd_list(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" in out


def test_cli_list_filters_by_type(tmp_path, monkeypatch, capsys):
    _save(tmp_path, slug="s1")
    _save(tmp_path, slug="d1", type_="decision")
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(type_="decision", scope=None, limit=50, as_json=False)
    rc = cli.cmd_list(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "d1" in out
    assert "s1" not in out


def test_cli_list_filters_by_scope(tmp_path, monkeypatch, capsys):
    _save(tmp_path, slug="x1", scope="h1")
    _save(tmp_path, slug="x2", scope="h2")
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(type_=None, scope="h2", limit=50, as_json=False)
    rc = cli.cmd_list(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "x2" in out
    assert "x1" not in out


def test_cli_show_returns_body(tmp_path, monkeypatch, capsys):
    _save(tmp_path, slug="viewme", body="this is the body content")
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(slug="viewme", scope=None)
    rc = cli.cmd_show(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "this is the body content" in out


def test_cli_show_returns_1_for_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(slug="missing", scope=None)
    rc = cli.cmd_show(args)
    assert rc == 1
