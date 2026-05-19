"""scope_meta tests."""
from pathlib import Path

import pytest

from memoryd.scope_meta import (
    MARKER_FILENAME,
    find_sensitive_root,
    is_path_sensitive,
    mark_sensitive,
    unmark_sensitive,
)


def test_find_returns_none_when_no_marker(tmp_path: Path):
    assert find_sensitive_root(tmp_path) is None


def test_find_returns_self_when_marker_at_path(tmp_path: Path):
    (tmp_path / MARKER_FILENAME).write_text("x")
    assert find_sensitive_root(tmp_path) == tmp_path.resolve()


def test_find_returns_ancestor_when_marker_above(tmp_path: Path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (tmp_path / MARKER_FILENAME).write_text("x")
    assert find_sensitive_root(deep) == tmp_path.resolve()


def test_is_path_sensitive_true_when_ancestor_marked(tmp_path: Path):
    (tmp_path / MARKER_FILENAME).write_text("x")
    assert is_path_sensitive(tmp_path / "sub")


def test_mark_sensitive_writes_marker_with_scope_root(tmp_path: Path):
    p = mark_sensitive(tmp_path)
    assert p.exists()
    assert "scope_root:" in p.read_text()


def test_mark_sensitive_refuses_when_parent_already_sensitive(tmp_path: Path):
    (tmp_path / MARKER_FILENAME).write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    with pytest.raises(ValueError, match="parent already sensitive"):
        mark_sensitive(sub)


def test_unmark_sensitive_removes_marker(tmp_path: Path):
    (tmp_path / MARKER_FILENAME).write_text("x")
    unmark_sensitive(tmp_path)
    assert not (tmp_path / MARKER_FILENAME).exists()


def test_unmark_sensitive_noop_when_missing(tmp_path: Path):
    unmark_sensitive(tmp_path)  # should not raise
