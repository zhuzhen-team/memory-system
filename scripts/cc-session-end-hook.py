#!/usr/bin/env python3
"""Cross-platform Claude Code SessionEnd hook for memoryd.

Reads CLAUDE_CODE_TRANSCRIPT_PATH (or first argv) and invokes
`memoryd capture --client claude-code --transcript <path>`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys


def _fork_sync_export(memoryd_bin: str) -> None:
    """Fire-and-forget `memoryd sync export --auto`.

    --auto makes memoryd internally honor [sync] enabled +
    auto_export_on_session_end gates, so this is safe even if the user
    has not opted in (will silently no-op).
    """
    try:
        subprocess.Popen(
            [memoryd_bin, "sync", "export", "--auto"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def main() -> int:
    path = os.environ.get("CLAUDE_CODE_TRANSCRIPT_PATH") or (
        sys.argv[1] if len(sys.argv) > 1 else ""
    )
    if not path:
        return 0  # nothing to capture
    memoryd_bin = shutil.which("memoryd") or "memoryd"
    cmd = [
        memoryd_bin,
        "capture",
        "--client",
        "claude-code",
        "--transcript",
        path,
    ]
    try:
        subprocess.run(cmd, check=False, timeout=30)
    except Exception:
        pass
    # After capture completes, opportunistically fork a sync export. The --auto
    # flag means memoryd itself decides whether to run based on config.
    _fork_sync_export(memoryd_bin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
