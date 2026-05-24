"""End-to-end tests for `memoryd handoff` CLI."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from memoryd.index import open_index


@pytest.fixture
def env_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated data root with empty index.db, plus a clean project dir."""
    data_root = tmp_path / "data"
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEMORYD_PROFILE_DIR", str(data_root / "profile"))
    open_index(data_root / "index.db").close()
    project = tmp_path / "myproj"
    project.mkdir()
    return project


def _run_cli(args: list[str], env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    import os
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "memoryd.cli", *args],
        capture_output=True, text=True, env=full_env, cwd=str(cwd) if cwd else None,
    )


def test_handoff_list_empty_returns_zero(env_setup: Path):
    """`handoff list` on empty project exits 0 with a hint."""
    proc = _run_cli(["handoff", "list", "--cwd", str(env_setup)])
    assert proc.returncode == 0
    assert "no HANDOFF files" in proc.stderr


def test_handoff_write_no_llm_creates_file(env_setup: Path):
    """`handoff write --no-llm` writes HANDOFF.md in cwd."""
    proc = _run_cli([
        "handoff", "write",
        "--cwd", str(env_setup),
        "--no-llm",
    ])
    assert proc.returncode == 0, proc.stderr
    out_path = env_setup / "HANDOFF.md"
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "HANDOFF — myproj" in content


def test_handoff_write_refuses_overwrite_without_force(env_setup: Path):
    """Existing HANDOFF.md is preserved unless --force passed."""
    (env_setup / "HANDOFF.md").write_text("# existing\n", encoding="utf-8")
    proc = _run_cli([
        "handoff", "write",
        "--cwd", str(env_setup),
        "--no-llm",
    ])
    assert proc.returncode == 1
    assert "refusing to overwrite" in proc.stderr
    # original survives
    assert (env_setup / "HANDOFF.md").read_text() == "# existing\n"


def test_handoff_write_force_overwrites(env_setup: Path):
    (env_setup / "HANDOFF.md").write_text("# existing\n", encoding="utf-8")
    proc = _run_cli([
        "handoff", "write",
        "--cwd", str(env_setup),
        "--no-llm",
        "--force",
    ])
    assert proc.returncode == 0, proc.stderr
    assert "HANDOFF — myproj" in (env_setup / "HANDOFF.md").read_text()


def test_handoff_write_out_path_outside_cwd(env_setup: Path, tmp_path: Path):
    """--out is a deliberate escape hatch: writes to an arbitrary path, even outside cwd.

    Documented behavior. We do NOT constrain --out to live under cwd because users
    legitimately want to dump HANDOFF to /tmp, ~/Desktop, etc. for quick review.
    The HANDOFF.md convention (project root) is enforced by the *default* (cwd/HANDOFF.md),
    not by sandboxing --out.
    """
    elsewhere = tmp_path / "elsewhere" / "scratch.md"
    elsewhere.parent.mkdir(parents=True, exist_ok=True)
    proc = _run_cli([
        "handoff", "write",
        "--cwd", str(env_setup),
        "--no-llm",
        "--out", str(elsewhere),
    ])
    assert proc.returncode == 0, proc.stderr
    assert elsewhere.exists()
    assert "HANDOFF — myproj" in elsewhere.read_text(encoding="utf-8")
    # Default location not touched
    assert not (env_setup / "HANDOFF.md").exists()


def test_handoff_write_snapshot_dated_file(env_setup: Path):
    """--snapshot writes HANDOFF-YYYY-MM-DD.md alongside (not over) HANDOFF.md."""
    (env_setup / "HANDOFF.md").write_text("# existing\n", encoding="utf-8")
    proc = _run_cli([
        "handoff", "write",
        "--cwd", str(env_setup),
        "--no-llm",
        "--snapshot",
    ])
    assert proc.returncode == 0, proc.stderr
    # canonical untouched
    assert (env_setup / "HANDOFF.md").read_text() == "# existing\n"
    # exactly one dated file created
    dated = list(env_setup.glob("HANDOFF-*.md"))
    assert len(dated) == 1


def test_handoff_write_dry_run_does_not_write(env_setup: Path):
    proc = _run_cli([
        "handoff", "write",
        "--cwd", str(env_setup),
        "--no-llm",
        "--dry-run",
    ])
    assert proc.returncode == 0, proc.stderr
    assert not (env_setup / "HANDOFF.md").exists()
    assert "HANDOFF — myproj" in proc.stdout


def test_handoff_read_returns_content(env_setup: Path):
    (env_setup / "HANDOFF.md").write_text("# hi\n", encoding="utf-8")
    proc = _run_cli(["handoff", "read", "--cwd", str(env_setup)])
    assert proc.returncode == 0
    assert "# hi" in proc.stdout


def test_handoff_read_returns_nonzero_when_missing(env_setup: Path):
    proc = _run_cli(["handoff", "read", "--cwd", str(env_setup)])
    assert proc.returncode == 1
    assert "no HANDOFF.md" in proc.stderr


def test_handoff_list_shows_canonical_and_dated(env_setup: Path):
    (env_setup / "HANDOFF.md").write_text("a", encoding="utf-8")
    (env_setup / "HANDOFF-2026-05-20.md").write_text("b", encoding="utf-8")
    proc = _run_cli(["handoff", "list", "--cwd", str(env_setup)])
    assert proc.returncode == 0
    assert "HANDOFF.md" in proc.stdout
    assert "HANDOFF-2026-05-20.md" in proc.stdout
