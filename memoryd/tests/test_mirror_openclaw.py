"""OpenClaw session jsonl mirror tests."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.mirror_openclaw import (
    OpenClawSessionHandler,
    reverse_lookup_scope_from_content,
    transcode_session_jsonl,
)
from memoryd.storage import list_sessions


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


def test_reverse_lookup_finds_deepest_known_root(tmp_path: Path):
    root_shallow = tmp_path / "root"
    root_deep = root_shallow / "nested"
    root_shallow.mkdir()
    root_deep.mkdir()

    content = f"I was editing {root_deep}/src/x.py for a while."
    resolved = reverse_lookup_scope_from_content(
        content,
        known_roots=[root_shallow, root_deep],
    )
    assert resolved == root_deep.resolve()


def test_reverse_lookup_returns_none_when_zero_matches(tmp_path: Path):
    other = tmp_path / "other"
    other.mkdir()
    resolved = reverse_lookup_scope_from_content(
        "no path mentioned at all",
        known_roots=[other],
    )
    assert resolved is None


def test_reverse_lookup_returns_none_when_multiple_unrelated_roots_match(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    content = f"work in {a}/file and also {b}/other-file"
    resolved = reverse_lookup_scope_from_content(content, known_roots=[a, b])
    assert resolved is None  # ambiguous → unscoped


def test_transcode_session_jsonl_extracts_text(tmp_path: Path):
    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(jsonl, [
        {"role": "user", "content": "in /Users/abble/myproj let's fix X"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ])

    project = tmp_path / "Users-abble-myproj"
    project.mkdir()
    (project / ".git").mkdir()

    session, resolved = transcode_session_jsonl(
        jsonl,
        known_roots=[project.parent],  # parent of all candidates
    )
    assert session.frontmatter.source == "openclaw-fs"
    assert "let's fix X" in session.body
    assert "ok" in session.body


def test_transcode_tolerates_malformed_lines(tmp_path: Path):
    jsonl = tmp_path / "bad.jsonl"
    jsonl.write_text('{"role":"user","content":"good"}\nnot-json\n{"role":"x"}\n')
    session, _ = transcode_session_jsonl(jsonl, known_roots=[])
    assert "good" in session.body


def test_handler_saves_to_data_root(tmp_path: Path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / ".git").mkdir()

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(jsonl, [
        {"role": "user", "content": f"cwd: {proj} doing work"},
        {"role": "assistant", "content": "ok"},
    ])

    data_root = tmp_path / "data"
    data_root.mkdir()

    handler = OpenClawSessionHandler(memory_root=data_root, known_roots=[proj])
    handler(jsonl)

    from memoryd.scope import scope_hash, resolve_scope_root
    sh = scope_hash(resolve_scope_root(proj))
    files = list_sessions(data_root, scope_hash=sh)
    assert len(files) == 1


def test_handler_routes_to_unscoped_on_ambiguous_content(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    jsonl = tmp_path / "amb.jsonl"
    _write_jsonl(jsonl, [{"role": "user", "content": f"{a} and also {b}"}])
    data_root = tmp_path / "data"
    data_root.mkdir()

    handler = OpenClawSessionHandler(memory_root=data_root, known_roots=[a, b])
    handler(jsonl)

    from memoryd.mirror import UNSCOPED_HASH
    files = list_sessions(data_root, scope_hash=UNSCOPED_HASH)
    assert len(files) == 1
