"""Codex rollout_summary mirror tests."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoryd.mirror_codex import (
    CodexRolloutHandler,
    parse_rollout_header,
    transcode_rollout,
)
from memoryd.storage import list_sessions


SAMPLE_ROLLOUT = """thread_id: 019e208d-06c4-7ab2-905c-1243dd8a2cd3
updated_at: 2026-05-13T12:46:41+00:00
rollout_path: /Users/abble/.codex/sessions/2026/05/13/rollout-2026-05-13T16-56-13-019e208d.jsonl
cwd: /Users/abble/Moonlight-Radiance-game
git_branch: codex/0-0-1-task0-preproduction

# Built an ElevenLabs audio pipeline for Moonlight Radiance.

Rollout context: discussed audio review workflow.

## Task 1: read handoff
Outcome: success
"""


def test_parse_rollout_header_extracts_kv_pairs(tmp_path: Path):
    f = tmp_path / "sample.md"
    f.write_text(SAMPLE_ROLLOUT)
    header, body = parse_rollout_header(f)
    assert header["thread_id"] == "019e208d-06c4-7ab2-905c-1243dd8a2cd3"
    assert header["cwd"] == "/Users/abble/Moonlight-Radiance-game"
    assert header["git_branch"] == "codex/0-0-1-task0-preproduction"
    assert body.startswith("# Built an ElevenLabs")


def test_parse_rollout_header_tolerates_missing_blank_line(tmp_path: Path):
    """No blank line between header and body still parses."""
    f = tmp_path / "no_blank.md"
    f.write_text("cwd: /tmp/x\n# title\nbody\n")
    header, body = parse_rollout_header(f)
    assert header == {"cwd": "/tmp/x"}
    assert body == "# title\nbody\n"


def test_parse_rollout_returns_empty_header_when_first_line_not_kv(tmp_path: Path):
    f = tmp_path / "no_header.md"
    f.write_text("# title only\n\nbody\n")
    header, body = parse_rollout_header(f)
    assert header == {}
    assert body.startswith("# title")


def test_transcode_uses_cwd_from_header(tmp_path: Path):
    project = tmp_path / "myproj"
    project.mkdir()
    (project / ".git").mkdir()  # makes resolve_scope_root pick this

    rollout = tmp_path / "rollout.md"
    rollout.write_text(SAMPLE_ROLLOUT.replace("/Users/abble/Moonlight-Radiance-game", str(project)))

    session, resolved_hash = transcode_rollout(rollout)
    assert session.frontmatter.source == "codex-rollout"
    assert session.frontmatter.scope_hash == resolved_hash
    assert resolved_hash is not None
    assert "Built an ElevenLabs" in session.body
    assert session.frontmatter.slug.startswith("2026-05-13")  # from updated_at


def test_transcode_returns_none_scope_when_cwd_missing(tmp_path: Path):
    """Rollout without cwd field → resolved_hash is None (UNSCOPED bucket)."""
    rollout = tmp_path / "no_cwd.md"
    rollout.write_text("thread_id: abc\nupdated_at: 2026-05-13T00:00:00+00:00\n\n# t\nbody\n")
    session, resolved_hash = transcode_rollout(rollout)
    assert resolved_hash is None
    assert session.frontmatter.source == "codex-rollout"


def test_handler_writes_session_to_data_root(tmp_path: Path):
    """End-to-end: handler reads file, saves under data root."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()

    rollout = tmp_path / "rollout.md"
    rollout.write_text(SAMPLE_ROLLOUT.replace("/Users/abble/Moonlight-Radiance-game", str(project)))

    data_root = tmp_path / "data"
    data_root.mkdir()

    handler = CodexRolloutHandler(memory_root=data_root)
    handler(rollout)

    from memoryd.scope import scope_hash, resolve_scope_root
    sh = scope_hash(resolve_scope_root(project))
    files = list_sessions(data_root, scope_hash=sh)
    assert len(files) == 1
    assert "codex-rollout" in files[0].read_text()


def test_handler_skips_non_md_files(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    handler = CodexRolloutHandler(memory_root=data_root)
    fake = tmp_path / "not-md.txt"
    fake.write_text("nope")
    handler(fake)  # should be a no-op, not raise
    assert list((data_root / "scopes").glob("**/*")) == []
