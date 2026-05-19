"""Plan 9 Task 2: CLI delete + promote write subcommands.

`memoryd delete <slug>`  — unlinks .md + SQLite memories/triggers rows;
                          sensitive scope must go through gate.
`memoryd promote <id>`   — calls governance.analyze.approve_promotion which
                          now also writes the final .md (not just flips status).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from memoryd import cli
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_session


def _save(
    root: Path,
    *,
    slug: str,
    scope: str = "h1",
    type_: str = "session",
    body: str = "body content",
) -> Path:
    """Save a memory via the real storage helpers so SQLite stays in sync."""
    mem = SessionMemory(
        frontmatter=Frontmatter(
            title=slug,
            slug=slug,
            type=type_,
            scope_hash=scope,
            triggers=[],
            source="test",
            created_at=datetime(2026, 5, 9, 9, 30),
        ),
        body=body,
    )
    return save_session(root, mem)


def _args(**kwargs: object) -> object:
    return type("Args", (), kwargs)()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_cli_delete_removes_file_with_force(tmp_path, monkeypatch, capsys):
    """delete --force unlinks the .md and removes SQLite row."""
    path = _save(tmp_path, slug="del-me", body="byebye")
    assert path.exists()
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(slug="del-me", scope=None, force=True)
    rc = cli.cmd_delete(args)
    assert rc == 0
    assert not path.exists()


def test_cli_delete_returns_1_for_missing(tmp_path, monkeypatch, capsys):
    """unknown slug → rc=1, friendly stderr."""
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(slug="nope", scope=None, force=True)
    rc = cli.cmd_delete(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "nope" in err


def test_cli_delete_prompts_without_force(tmp_path, monkeypatch, capsys):
    """default y/N prompt: answering 'n' must keep the file intact."""
    path = _save(tmp_path, slug="keep-me", body="stay")
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    # User answers "n"
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    args = _args(slug="keep-me", scope=None, force=False)
    rc = cli.cmd_delete(args)
    # rc==0 (clean abort) or 1 (decline) both acceptable; file MUST still exist
    assert rc in (0, 1)
    assert path.exists()


def test_cli_delete_unindexes_sqlite(tmp_path, monkeypatch, capsys):
    """After delete: SQLite memories table no longer has the row."""
    _save(tmp_path, slug="idx-del", body="x")
    db = tmp_path / "index.db"
    assert db.exists()
    pre = sqlite3.connect(str(db))
    n_before = pre.execute(
        "SELECT COUNT(*) FROM memories WHERE slug='idx-del'"
    ).fetchone()[0]
    pre.close()
    assert n_before == 1
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(slug="idx-del", scope=None, force=True)
    rc = cli.cmd_delete(args)
    assert rc == 0
    post = sqlite3.connect(str(db))
    n_after = post.execute(
        "SELECT COUNT(*) FROM memories WHERE slug='idx-del'"
    ).fetchone()[0]
    post.close()
    assert n_after == 0


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------


def _bootstrap_index_db(data_root: Path) -> Path:
    """Run real migrations to create a full index.db at data_root/index.db."""
    from memoryd.index import open_index
    idx = open_index(data_root / "index.db")
    idx.close()
    return data_root / "index.db"


def test_cli_promote_writes_md(tmp_path, monkeypatch, capsys):
    """approve_promotion writes the actual .md to the type-dir and sets status."""
    _bootstrap_index_db(tmp_path)
    # Insert a real promotion row (Plan 3 schema has scope_hash + created_at).
    conn = sqlite3.connect(str(tmp_path / "index.db"))
    conn.execute(
        "INSERT INTO promotions (id, source_session_slug, proposed_type, "
        "proposed_title, proposed_body, proposed_triggers, dura_score, "
        "reasoning, proposed_supersedes, scope_hash, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            7,
            "sess-1",
            "decision",
            "logo direction",
            "Adopt deep-blue+silver palette",
            "[]",
            "{}",
            "test reasoning",
            "[]",
            "scopeA",
            "pending",
            "2026-05-15T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)

    args = _args(promotion_id=7)
    rc = cli.cmd_promote(args)
    assert rc == 0

    # Status flipped
    conn = sqlite3.connect(str(tmp_path / "index.db"))
    status_ = conn.execute(
        "SELECT status FROM promotions WHERE id=7"
    ).fetchone()[0]
    conn.close()
    assert status_ == "approved"

    # A decision .md exists under scopes/scopeA/decisions/
    decisions_dir = tmp_path / "scopes" / "scopeA" / "decisions"
    decisions = list(decisions_dir.glob("*.md"))
    assert len(decisions) >= 1
    text = decisions[0].read_text(encoding="utf-8")
    assert "logo direction" in text
    assert "Adopt deep-blue+silver palette" in text


def test_cli_promote_unknown_id(tmp_path, monkeypatch, capsys):
    """unknown promotion_id → rc=1."""
    _bootstrap_index_db(tmp_path)
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path)
    args = _args(promotion_id=999)
    rc = cli.cmd_promote(args)
    assert rc == 1
