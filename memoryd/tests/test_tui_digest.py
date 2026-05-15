"""Tests for the digest TUI reducer functions (no textual rendering).

The Textual App layer requires a TTY and is exercised manually. These
tests only cover the reducer helpers in ``memoryd.tui.digest`` plus the
underlying module-level promotion helpers in ``memoryd.governance.analyze``.
"""
import sqlite3
from unittest.mock import MagicMock

import pytest

from memoryd.tui.digest import (
    approve_all_pending,
    list_pending,
    reject_one,
)


def _init_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE promotions ("
        "id INTEGER PRIMARY KEY, source_session_slug TEXT, "
        "proposed_type TEXT, proposed_title TEXT, "
        "proposed_body TEXT, proposed_triggers TEXT, "
        "reasoning TEXT, status TEXT)"
    )
    return conn


def test_list_pending_returns_empty_when_no_db(tmp_path):
    assert list_pending(tmp_path) == []


def test_list_pending_returns_pending_rows(tmp_path):
    conn = _init_db(tmp_path / "index.db")
    conn.execute(
        "INSERT INTO promotions (source_session_slug, proposed_type, "
        "proposed_title, status) VALUES (?, ?, ?, ?)",
        ("sess-1", "decision", "logo blue", "pending"),
    )
    conn.execute(
        "INSERT INTO promotions (source_session_slug, proposed_type, "
        "proposed_title, status) VALUES (?, ?, ?, ?)",
        ("sess-2", "fact", "db v15", "approved"),
    )
    conn.commit()
    conn.close()
    items = list_pending(tmp_path)
    assert len(items) == 1
    assert items[0]["proposed_title"] == "logo blue"


def test_approve_all_pending_marks_each(tmp_path):
    conn = _init_db(tmp_path / "index.db")
    for i in range(3):
        conn.execute(
            "INSERT INTO promotions (source_session_slug, proposed_type, "
            "proposed_title, status) VALUES (?, ?, ?, ?)",
            (f"s{i}", "decision", f"t{i}", "pending"),
        )
    conn.commit()
    conn.close()
    approved = approve_all_pending(tmp_path)
    assert len(approved) == 3
    # all now status=approved
    conn = sqlite3.connect(str(tmp_path / "index.db"))
    rows = conn.execute("SELECT status FROM promotions").fetchall()
    conn.close()
    assert all(r[0] == "approved" for r in rows)


def test_approve_all_pending_uses_injected_fns(tmp_path):
    """When approve_fn/list_fn injected, do NOT touch real db."""
    fake_list = MagicMock(return_value=[{"id": 1}, {"id": 2}])
    fake_approve = MagicMock()
    approved = approve_all_pending(
        tmp_path, approve_fn=fake_approve, list_fn=fake_list
    )
    assert approved == [1, 2]
    fake_list.assert_called_once_with(tmp_path)
    assert fake_approve.call_count == 2
    fake_approve.assert_any_call(tmp_path, 1)
    fake_approve.assert_any_call(tmp_path, 2)


def test_reject_one_marks_rejected(tmp_path):
    conn = _init_db(tmp_path / "index.db")
    conn.execute(
        "INSERT INTO promotions (id, source_session_slug, proposed_type, "
        "proposed_title, status) VALUES (?, ?, ?, ?, ?)",
        (42, "s", "decision", "t", "pending"),
    )
    conn.commit()
    conn.close()
    reject_one(tmp_path, 42)
    conn = sqlite3.connect(str(tmp_path / "index.db"))
    status_ = conn.execute(
        "SELECT status FROM promotions WHERE id = 42"
    ).fetchone()[0]
    conn.close()
    assert status_ == "rejected"


def test_reject_one_raises_for_unknown_id(tmp_path):
    conn = _init_db(tmp_path / "index.db")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError):
        reject_one(tmp_path, 999)


def test_approve_promotion_raises_when_db_missing(tmp_path):
    from memoryd.governance.analyze import approve_promotion
    with pytest.raises(FileNotFoundError):
        approve_promotion(tmp_path, 1)
