#!/usr/bin/env python3
"""Cross-platform Claude Code SessionStart hook for memoryd.

CC pipes our stdout into ``additionalContext``; we render a compact
markdown block via ``memoryd inject`` so the model knows who the user
is before the first turn.

Failure semantics: hook **never raises** — on any error we emit an
empty stdout and write the traceback to the log file. The CC startup
must never be blocked by memoryd.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


_TIMEOUT_SECONDS = 5
_DATA_ROOT_ENV = "MEMORYD_DATA_ROOT"


def _log_dir() -> Path:
    root = os.environ.get(_DATA_ROOT_ENV) or str(Path.home() / ".local" / "share" / "memoryd")
    return Path(root) / "logs"


def _log(msg: str) -> None:
    try:
        d = _log_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "cc-session-start.log").open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.utcnow().isoformat()}Z] {msg}\n")
    except Exception:
        # Never let logging break the hook.
        pass


def _resolve_memoryd_bin() -> str | None:
    override = os.environ.get("MEMORYD_BIN")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    venv_guess = Path.home() / "memory-system" / "memoryd" / ".venv" / "bin" / "memoryd"
    if venv_guess.exists() and os.access(venv_guess, os.X_OK):
        return str(venv_guess)
    found = shutil.which("memoryd")
    if found:
        return found
    return None


def main() -> int:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    cwd = project_dir or str(Path.home())

    bin_path = _resolve_memoryd_bin()
    if bin_path is None:
        _log("memoryd binary not found; skipping inject")
        print("")  # graceful no-op
        return 0

    cmd = [
        bin_path,
        "inject",
        "--scope=auto",
        "--max-chars=1500",
        "--top-entities=8",
        "--recent=5",
    ]
    try:
        out = subprocess.run(
            cmd,
            cwd=cwd,
            check=False,
            timeout=_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        _log(f"inject timed out (>{_TIMEOUT_SECONDS}s); emitting empty")
        print("")
        return 0
    except Exception as exc:  # noqa: BLE001 — hook must be graceful
        _log(f"inject raised: {exc!r}; emitting empty")
        print("")
        return 0

    if out.returncode != 0:
        _log(f"inject exited rc={out.returncode}; stderr={out.stderr.strip()[:300]}")
        # Still emit stdout (likely empty / fallback) so CC startup proceeds.
    sys.stdout.write(out.stdout or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
