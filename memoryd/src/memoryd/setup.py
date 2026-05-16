"""User config management for Plan 2.5 wire-up.

All edits to ~/.codex/* and ~/Library/LaunchAgents/* go through this
module: backup → read → mutate → atomic write. Never sed/awk/jq.
"""
from __future__ import annotations

import json
import shutil
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Literal


def backup_file(path: Path, *, backup_dir: Path) -> Path:
    """Copy `path` to `<backup_dir>/<name>.bak.<YYYYMMDD-HHMMSS>`.

    Creates `backup_dir` if it doesn't exist. Returns the backup path.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bp = backup_dir / f"{path.name}.bak.{ts}"
    shutil.copy2(path, bp)
    return bp


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Codex notify swap
# ---------------------------------------------------------------------------

NotifyTarget = Literal["probe", "wrapper", "original"]


def _toml_set_notify(toml_text: str, new_notify: list[str]) -> str:
    """Replace the top-level `notify = [...]` line in a TOML config.

    Uses string-level rewrite because tomllib has no writer and we want
    to preserve formatting of unrelated keys.
    """
    import re
    pattern = re.compile(r"^notify\s*=\s*\[.*?\]\s*$", re.MULTILINE | re.DOTALL)
    rendered = "notify = " + json.dumps(new_notify)
    if pattern.search(toml_text):
        return pattern.sub(rendered, toml_text, count=1)
    # No existing notify — append before first [section]
    section_match = re.search(r"^\[", toml_text, re.MULTILINE)
    if section_match:
        idx = section_match.start()
        return toml_text[:idx] + rendered + "\n\n" + toml_text[idx:]
    return toml_text + ("\n" if not toml_text.endswith("\n") else "") + rendered + "\n"


def swap_codex_notify(
    *,
    to: NotifyTarget,
    codex_dir: Path,
    backup_dir: Path,
    probe_path: str,
    wrapper_path: str,
) -> Path:
    """Rewrite `~/.codex/config.toml`'s notify field; preserve everything else.

    Stores the original notify value in `<codex_dir>/.memoryd-notify-state.json`
    on first swap so we can restore via `to="original"`. Returns the path
    of the state file.
    """
    cfg = codex_dir / "config.toml"
    if not cfg.exists():
        raise FileNotFoundError(cfg)

    state_file = codex_dir / ".memoryd-notify-state.json"
    backup_file(cfg, backup_dir=backup_dir)

    toml_text = cfg.read_text(encoding="utf-8")
    parsed = tomllib.loads(toml_text)
    current = parsed.get("notify", [])

    # Snapshot original on first swap (atomic write to avoid corrupting
    # the state file if killed mid-write before config.toml itself gets
    # rewritten — without a valid state, `--to original` would later fail).
    if not state_file.exists():
        state = {"original": current}
        _atomic_write(state_file, json.dumps(state, indent=2))
    else:
        state = json.loads(state_file.read_text(encoding="utf-8"))

    if to == "probe":
        new_notify = [probe_path]
    elif to == "wrapper":
        # wrapper takes the original notify args verbatim after its own path
        original_args = state["original"][1:] if len(state["original"]) > 1 else []
        new_notify = [wrapper_path, *original_args]
    else:  # "original"
        new_notify = state["original"]

    new_text = _toml_set_notify(toml_text, new_notify)
    _atomic_write(cfg, new_text)
    return state_file


# ---------------------------------------------------------------------------
# Codex hooks.json Stop entry removal
# ---------------------------------------------------------------------------


def remove_codex_stop_hook(*, codex_dir: Path, backup_dir: Path) -> None:
    """Delete the `hooks.Stop` array from ~/.codex/hooks.json (other events kept)."""
    hooks = codex_dir / "hooks.json"
    if not hooks.exists():
        return
    backup_file(hooks, backup_dir=backup_dir)

    data = json.loads(hooks.read_text(encoding="utf-8"))
    if "hooks" in data and "Stop" in data["hooks"]:
        del data["hooks"]["Stop"]
    _atomic_write(hooks, json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# launchd plist install
# ---------------------------------------------------------------------------


def install_launchd_mirror(
    *,
    template_path: Path,
    launch_dir: Path,
    memoryd_bin: str,
    data_root: str,
) -> Path:
    """Render the plist template into `<launch_dir>/com.memoryd.mirror.plist`.

    Returns the rendered path. Caller is responsible for `launchctl bootstrap`.
    """
    launch_dir.mkdir(parents=True, exist_ok=True)
    template = template_path.read_text(encoding="utf-8")
    rendered = template.replace("__MEMORYD_BIN__", memoryd_bin).replace(
        "__MEMORYD_DATA_ROOT__", data_root
    )
    out = launch_dir / "com.memoryd.mirror.plist"
    _atomic_write(out, rendered)
    return out


def uninstall_launchd_mirror(*, launch_dir: Path) -> bool:
    """Delete the plist. Returns True if a file was deleted."""
    out = launch_dir / "com.memoryd.mirror.plist"
    if out.exists():
        out.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Plan 5 — install-cron / install-cc-hook / auto-install wrappers
# ---------------------------------------------------------------------------


def install_cron(job_key: str) -> Path | tuple[Path, Path]:
    """Wrap `setup_cron.install`. Tests can monkeypatch `memoryd.setup_cron.install`."""
    from . import setup_cron
    return setup_cron.install(job_key)


def uninstall_cron(job_key: str) -> None:
    """Wrap `setup_cron.uninstall`."""
    from . import setup_cron
    return setup_cron.uninstall(job_key)


def install_cc_hook(target_settings: Path | None = None) -> Path:
    """Wire `scripts/cc-session-end-hook` into ~/.claude/settings.json hooks.SessionEnd.

    Detect platform via :func:`memoryd.platforms.detect`:
    - Windows → `.ps1` + powershell command
    - macOS / Linux → `.py` + python3 command

    Backs up the settings file (when it exists) into `~/.claude/backups/`,
    then replaces any prior `matcher='*'` entry whose command contains
    `cc-session-end-hook` with our wrapper.
    """
    from .platforms import detect

    settings = target_settings or (Path.home() / ".claude" / "settings.json")
    if settings.exists():
        backup_file(
            settings,
            backup_dir=Path.home() / ".claude" / "backups",
        )
    repo_root = Path(__file__).resolve().parents[3]
    plat = detect()
    if plat == "windows":
        hook_path = repo_root / "scripts" / "cc-session-end-hook.ps1"
        cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{hook_path}"'
    else:
        hook_path = repo_root / "scripts" / "cc-session-end-hook.py"
        cmd = f'python3 "{hook_path}"'
    data = json.loads(settings.read_text("utf-8")) if settings.exists() else {}
    hooks = data.setdefault("hooks", {})
    session_end = hooks.setdefault("SessionEnd", [])
    # remove any prior matcher==* entry that points to our hook
    session_end[:] = [
        m for m in session_end
        if not (
            m.get("matcher") == "*"
            and any(
                "cc-session-end-hook" in (h.get("command") or "")
                for h in m.get("hooks", [])
            )
        )
    ]
    session_end.append({
        "matcher": "*",
        "hooks": [{"type": "command", "command": cmd}],
    })
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    return settings


def install_memory_searcher(
    target_dir: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    """Copy memory-searcher.md template to ~/.claude/agents/ (or --target)."""
    src = Path(__file__).parent / "templates" / "memory-searcher.md"
    if not src.exists():
        raise FileNotFoundError(f"template missing: {src}")
    if target_dir is None:
        target_dir = Path.home() / ".claude" / "agents"
    target_dir.mkdir(parents=True, exist_ok=True)
    dst = target_dir / "memory-searcher.md"
    if dst.exists() and not force:
        raise FileExistsError(f"{dst} exists; use --force to overwrite")
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def auto_install() -> dict:
    """Detect platform, install cron jobs + cc-hook; return per-step results.

    Errors are captured into `<step>_error` keys so the caller can surface
    them without aborting the whole sequence.
    """
    from .platforms import detect

    plat = detect()
    results: dict = {"platform": plat}
    try:
        results["decay_cron"] = str(install_cron("decay"))
    except Exception as e:  # noqa: BLE001 — best-effort, swallow per-step
        results["decay_cron_error"] = str(e)
    try:
        results["digest_cron"] = str(install_cron("digest"))
    except Exception as e:  # noqa: BLE001
        results["digest_cron_error"] = str(e)
    try:
        results["cc_hook"] = str(install_cc_hook())
    except Exception as e:  # noqa: BLE001
        results["cc_hook_error"] = str(e)
    return results
