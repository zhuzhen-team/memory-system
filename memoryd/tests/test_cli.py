"""CLI capture tests."""
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.cli import capture_session
from memoryd.scope import resolve_scope_root, scope_hash
from memoryd.storage import list_sessions, load_session


def _write_fake_transcript(transcript_path: Path) -> None:
    """Write a JSONL file mimicking Claude Code transcript format."""
    lines = [
        {"type": "user", "message": {"content": [{"type": "text", "text": "聊聊 wolin logo 方向"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "建议深蓝+银灰"}]}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "好"}]}},
    ]
    transcript_path.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines))


def test_capture_creates_session_file(memory_root: Path, tmp_path: Path):
    transcript = tmp_path / "transcript.jsonl"
    _write_fake_transcript(transcript)
    cwd = tmp_path / "project"
    cwd.mkdir()

    payload = {
        "session_id": "test-session-123",
        "transcript_path": str(transcript),
        "cwd": str(cwd),
    }

    capture_session(payload, memory_root=memory_root, now=datetime(2026, 5, 9, 14, 0))

    sh = scope_hash(resolve_scope_root(cwd))
    files = list_sessions(memory_root, scope_hash=sh)
    assert len(files) == 1
    md_text = files[0].read_text(encoding="utf-8")
    assert "wolin logo" in md_text
    assert "深蓝+银灰" in md_text


def test_capture_handles_missing_transcript(memory_root: Path, tmp_path: Path):
    """Missing transcript_path should not crash; it should write a stub session."""
    cwd = tmp_path / "project"
    cwd.mkdir()
    payload = {
        "session_id": "test-no-transcript",
        "transcript_path": "/nonexistent/path.jsonl",
        "cwd": str(cwd),
    }
    capture_session(payload, memory_root=memory_root, now=datetime(2026, 5, 9, 15, 0))

    sh = scope_hash(resolve_scope_root(cwd))
    files = list_sessions(memory_root, scope_hash=sh)
    assert len(files) == 1
    md_text = files[0].read_text(encoding="utf-8")
    assert "transcript unavailable" in md_text.lower() or "无 transcript" in md_text


def test_main_reads_payload_from_stdin(memory_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`memoryd capture` reads JSON payload from stdin and writes a session."""
    transcript = tmp_path / "transcript.jsonl"
    _write_fake_transcript(transcript)
    cwd = tmp_path / "project"
    cwd.mkdir()
    payload = {
        "session_id": "stdin-test",
        "transcript_path": str(transcript),
        "cwd": str(cwd),
    }

    proc = subprocess.run(
        ["uv", "run", "memoryd", "capture"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd="/Users/abble/project-management-personal/memoryd",
        env={**os.environ, "MEMORYD_DATA_ROOT": str(memory_root)},
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    sh = scope_hash(resolve_scope_root(cwd))
    files = list_sessions(memory_root, scope_hash=sh)
    assert len(files) == 1


def test_capture_sanitizes_session_id_in_slug(memory_root: Path, tmp_path: Path):
    """session_id with path separators must not escape the sessions dir."""
    cwd = tmp_path / "project"
    cwd.mkdir()
    payload = {
        "session_id": "../../etc/passwd",  # path traversal attempt
        "transcript_path": "/nonexistent",
        "cwd": str(cwd),
    }
    path = capture_session(payload, memory_root=memory_root, now=datetime(2026, 5, 9, 14, 0))

    # The saved file must be inside the scope's sessions dir, not escaped
    from memoryd.scope import resolve_scope_root, scope_hash
    sh = scope_hash(resolve_scope_root(cwd))
    expected_parent = memory_root / "scopes" / sh / "sessions"
    assert path.parent == expected_parent
    # And the slug must not contain path separators
    assert "/" not in path.stem
    assert ".." not in path.stem


def test_main_rejects_non_dict_json(memory_root: Path, tmp_path: Path):
    """A valid-JSON-but-not-dict payload should exit 2, not crash."""
    proc = subprocess.run(
        ["uv", "run", "memoryd", "capture"],
        input='["a", "b"]',  # valid JSON, not a dict
        capture_output=True,
        text=True,
        cwd="/Users/abble/project-management-personal/memoryd",
        env={**os.environ, "MEMORYD_DATA_ROOT": str(memory_root)},
    )
    assert proc.returncode == 2, f"expected 2, got {proc.returncode}; stderr: {proc.stderr}"
    assert "JSON object" in proc.stderr or "expected" in proc.stderr.lower()


def test_capture_respects_source_param(memory_root: Path, tmp_path: Path):
    """capture_session honors an explicit source value."""
    transcript = tmp_path / "transcript.jsonl"
    _write_fake_transcript(transcript)
    cwd = tmp_path / "project"
    cwd.mkdir()
    payload = {
        "session_id": "src-test-1",
        "transcript_path": str(transcript),
        "cwd": str(cwd),
    }

    capture_session(payload, memory_root=memory_root, now=datetime(2026, 5, 13, 10, 0), source="codex")
    sh = scope_hash(resolve_scope_root(cwd))
    files = list_sessions(memory_root, scope_hash=sh)
    assert len(files) == 1
    sess = load_session(files[0])
    assert sess.frontmatter.source == "codex"


def test_capture_defaults_source_to_claude_code(memory_root: Path, tmp_path: Path):
    """No source argument → frontmatter.source defaults to 'claude-code'."""
    transcript = tmp_path / "transcript.jsonl"
    _write_fake_transcript(transcript)
    cwd = tmp_path / "project"
    cwd.mkdir()
    payload = {
        "session_id": "default-src-1",
        "transcript_path": str(transcript),
        "cwd": str(cwd),
    }

    capture_session(payload, memory_root=memory_root, now=datetime(2026, 5, 13, 10, 1))
    sh = scope_hash(resolve_scope_root(cwd))
    files = list_sessions(memory_root, scope_hash=sh)
    sess = load_session(files[0])
    assert sess.frontmatter.source == "claude-code"


def test_main_passes_source_flag_to_capture(memory_root: Path, tmp_path: Path):
    """`memoryd capture --source openclaw` reaches capture_session."""
    transcript = tmp_path / "transcript.jsonl"
    _write_fake_transcript(transcript)
    cwd = tmp_path / "project"
    cwd.mkdir()
    payload = {
        "session_id": "stdin-source-test",
        "transcript_path": str(transcript),
        "cwd": str(cwd),
    }

    proc = subprocess.run(
        ["uv", "run", "memoryd", "capture", "--source", "openclaw"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd="/Users/abble/project-management-personal/memoryd",
        env={**os.environ, "MEMORYD_DATA_ROOT": str(memory_root)},
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    sh = scope_hash(resolve_scope_root(cwd))
    files = list_sessions(memory_root, scope_hash=sh)
    assert len(files) == 1
    sess = load_session(files[0])
    assert sess.frontmatter.source == "openclaw"


def test_mirror_once_scans_existing_codex_files(memory_root: Path, tmp_path: Path):
    """`memoryd mirror --codex --once --codex-dir <tmp>` mirrors existing files then exits."""
    import subprocess
    import os
    import re as _re

    codex_dir = tmp_path / "rollout_summaries"
    codex_dir.mkdir()
    sample = (
        "thread_id: t1\nupdated_at: 2026-05-14T10:00:00+00:00\n"
        f"cwd: {tmp_path}\n\n# title\nbody\n"
    )
    (codex_dir / "2026-05-14T10-00-00-id1-topic.md").write_text(sample)

    proc = subprocess.run(
        [
            "uv", "run", "memoryd", "mirror",
            "--codex",
            "--once",
            "--codex-dir", str(codex_dir),
        ],
        capture_output=True,
        text=True,
        cwd="/Users/abble/project-management-personal/memoryd",
        env={**os.environ, "MEMORYD_DATA_ROOT": str(memory_root)},
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    files = list((memory_root / "scopes").rglob("*.md"))
    assert len(files) == 1
    assert "codex-rollout" in files[0].read_text()


def test_mirror_help_includes_subcommand():
    import subprocess
    proc = subprocess.run(
        ["uv", "run", "memoryd", "mirror", "--help"],
        capture_output=True,
        text=True,
        cwd="/Users/abble/project-management-personal/memoryd",
        timeout=20,
    )
    assert proc.returncode == 0
    assert "--codex" in proc.stdout
    assert "--openclaw" in proc.stdout
    assert "--once" in proc.stdout
