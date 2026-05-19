"""CLI tests for `memoryd inject`."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoryd.index import open_index


def _memoryd_bin() -> str:
    """Resolve a callable memoryd entrypoint for subprocess tests.

    Prefer the venv `memoryd` console script (the same binary the
    hooks will invoke). Fall back to ``python -c "from memoryd.cli
    import main; sys.exit(main())"`` if the script is missing.
    """
    bin_path = shutil.which("memoryd")
    if bin_path:
        return bin_path
    # venv-relative guess (uv-managed)
    guess = Path(sys.executable).parent / "memoryd"
    if guess.exists():
        return str(guess)
    return ""


def _run_inject(env_root: Path, *args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["MEMORYD_DATA_ROOT"] = str(env_root)
    env["MEMORYD_PROFILE_DIR"] = str(env_root / "profile")
    if extra_env:
        env.update(extra_env)
    bin_path = _memoryd_bin()
    if bin_path:
        cmd = [bin_path, "inject", *args]
    else:
        cmd = [
            sys.executable,
            "-c",
            "import sys; from memoryd.cli import main; sys.exit(main())",
            "inject",
            *args,
        ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )


def test_cli_inject_empty_root_emits_fallback(tmp_path: Path) -> None:
    """No data → CLI exits 0 and prints the graceful fallback line."""
    result = _run_inject(tmp_path)
    assert result.returncode == 0
    assert "memoryd 未启用" in result.stdout or "_(" in result.stdout


def test_cli_inject_populated_root_emits_markdown(tmp_path: Path) -> None:
    """With identity + entities + memories, stdout contains the rendered block."""
    # Open + close to apply migrations.
    open_index(tmp_path / "index.db").close()

    # Identity file
    profile = tmp_path / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "identity.md").write_text("abble: builds memory-system locally.\n", encoding="utf-8")

    # Direct SQLite seeding
    import sqlite3
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(str(tmp_path / "index.db")) as conn:
        conn.execute(
            """INSERT INTO entities (id, name, type, aliases, context, first_seen_at,
                                    last_seen_at, mention_count, scope_hash, decay_state)
               VALUES ('entity:project:memory-system','memory-system','project','[]','',
                       ?, ?, 33, 'abc', 'fresh')""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO memories
               (slug, type, scope_hash, title, source, created_at, ttl_days,
                decay_state, recall_count, fingerprint, body_path, scope_sensitive)
               VALUES ('m1','decision','abc','choose uv','test',?, NULL, 'fresh',
                       0, '', '', 0)""",
            (now,),
        )
        conn.commit()

    result = _run_inject(tmp_path, "--scope=global")
    assert result.returncode == 0, result.stderr
    assert "## 与 abble 的最近上下文" in result.stdout
    assert "memory-system (33)" in result.stdout
    assert "choose uv" in result.stdout
    assert "builds memory-system locally" in result.stdout


def test_cli_inject_auto_scope_with_project_dir(tmp_path: Path) -> None:
    """--scope=auto + CLAUDE_PROJECT_DIR should not crash and exit 0."""
    open_index(tmp_path / "index.db").close()
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / ".git").mkdir()  # makes the scope root deterministic
    result = _run_inject(
        tmp_path,
        "--scope=auto",
        extra_env={"CLAUDE_PROJECT_DIR": str(proj)},
    )
    assert result.returncode == 0
    # No data → graceful fallback
    assert result.stdout.strip() != ""


def test_cli_inject_accepts_explicit_flags(tmp_path: Path) -> None:
    """All CLI flags parse without error even when DB is empty."""
    open_index(tmp_path / "index.db").close()
    result = _run_inject(
        tmp_path,
        "--scope=global",
        "--max-chars=100",
        "--top-entities=3",
        "--recent=2",
        "--window-days=7",
        "--types",
        "decision",
        "fact",
        "--include-trends",
    )
    assert result.returncode == 0
    # Fallback is fine here — what we're verifying is no argparse error.
    assert "error" not in result.stderr.lower() or "argparse" not in result.stderr.lower()
