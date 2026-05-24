"""User config management for Plan 2.5 wire-up.

All edits to ~/.codex/* and ~/Library/LaunchAgents/* go through this
module: backup → read → mutate → atomic write. Never sed/awk/jq.
"""
from __future__ import annotations

import json
import os
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
    """Wire `plugins/claude-code/session-end` into ~/.claude/settings.json hooks.SessionEnd.

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
        hook_path = repo_root / "plugins" / "claude-code" / "session-end.ps1"
        cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{hook_path}"'
    else:
        hook_path = repo_root / "plugins" / "claude-code" / "session-end.py"
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


def install_cc_session_start_hook(target_settings: Path | None = None) -> Path:
    """Wire ``plugins/claude-code/session-start`` into ``~/.claude/settings.json``.

    Symmetric with :func:`install_cc_hook`, but registers under the
    ``SessionStart`` event instead of ``SessionEnd``. SessionStart
    hooks emit ``additionalContext`` for the new session via stdout, so
    the script must be a fast, non-blocking command.

    Detect platform via :func:`memoryd.platforms.detect`:
    - Windows → ``session-start.ps1`` + powershell command
    - macOS / Linux → ``session-start.py`` + python3 command

    Backs up the settings file (when it exists) into ``~/.claude/backups/``,
    then replaces any prior ``matcher='*'`` SessionStart entry whose
    command contains ``cc-session-start-hook`` or our script name with
    the new one (idempotent re-install).
    """
    from .platforms import detect

    settings = target_settings or (Path.home() / ".claude" / "settings.json")
    if settings.exists():
        backup_file(settings, backup_dir=Path.home() / ".claude" / "backups")
    repo_root = Path(__file__).resolve().parents[3]
    plat = detect()
    if plat == "windows":
        hook_path = repo_root / "plugins" / "claude-code" / "session-start.ps1"
        cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{hook_path}"'
    else:
        hook_path = repo_root / "plugins" / "claude-code" / "session-start.py"
        cmd = f'python3 "{hook_path}"'
    data = json.loads(settings.read_text("utf-8")) if settings.exists() else {}
    hooks = data.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])
    session_start[:] = [
        m for m in session_start
        if not (
            m.get("matcher") == "*"
            and any(
                ("cc-session-start-hook" in (h.get("command") or ""))
                or ("session-start.py" in (h.get("command") or ""))
                or ("session-start.ps1" in (h.get("command") or ""))
                or ("session-start.sh" in (h.get("command") or ""))
                for h in m.get("hooks", [])
            )
        )
    ]
    session_start.append({
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
    """Detect platform, install cron jobs + cc-hooks + harness adapters; return per-step results.

    Best-effort per step — errors are captured into ``<step>_error`` keys so the
    caller can surface them without aborting the whole sequence. Steps that
    target tools the user doesn't have installed (codex, openclaw, claude CLI)
    are skipped silently and recorded as ``<step>_skipped``.
    """
    from .platforms import detect

    plat = detect()
    results: dict = {"platform": plat}

    # ---- cron jobs ----
    for job in ("decay", "digest", "weekly_identity", "monthly_report"):
        key = f"{job}_cron"
        try:
            results[key] = str(install_cron(job))
        except Exception as e:  # noqa: BLE001
            results[f"{key}_error"] = str(e)

    # sync_push cron — only install if user has configured sync.dir; otherwise
    # the daily run would no-op repeatedly. User can opt in later via
    # `memoryd config set sync.dir <path> && memoryd setup install-cron --sync-push`.
    try:
        from .config import load_config as _load_cfg
        _cfg = _load_cfg()
        # `_load_cfg()` returns a `Config` object with `.sync` attr; only
        # the test stubs return a plain dict. Handle both.
        if hasattr(_cfg, "sync"):
            sync_dir = getattr(_cfg.sync, "dir", None)
            sync_enabled = getattr(_cfg.sync, "enabled", False)
        else:
            sync_obj = (_cfg or {}).get("sync", {}) if _cfg else {}
            sync_dir = sync_obj.get("dir")
            sync_enabled = sync_obj.get("enabled", False)
        if sync_dir:
            # The cron template invokes `memoryd sync export` directly (no
            # ``--auto`` flag). That bypasses the SessionEnd-only gate and
            # respects only ``sync.dir`` being configured. We still flip
            # ``sync.enabled`` to True for any other code paths that
            # consult it.
            try:
                if not sync_enabled:
                    try:
                        from .config import set_config_key
                        set_config_key("sync.enabled", True)
                    except Exception:  # noqa: BLE001 — config is optional
                        pass
                results["sync_push_cron"] = str(install_cron("sync_push"))
            except Exception as e:  # noqa: BLE001
                results["sync_push_cron_error"] = str(e)
        else:
            results["sync_push_cron_skipped"] = "sync.dir not configured; set it then re-run auto-install"
    except Exception as e:  # noqa: BLE001
        results["sync_push_cron_error"] = str(e)

    # ---- Claude Code hooks ----
    try:
        results["cc_hook"] = str(install_cc_hook())
    except Exception as e:  # noqa: BLE001
        results["cc_hook_error"] = str(e)
    try:
        results["cc_session_start_hook"] = str(install_cc_session_start_hook())
    except Exception as e:  # noqa: BLE001
        results["cc_session_start_hook_error"] = str(e)

    # ---- Codex AGENTS.md auto-include (refresh memoryd identity block) ----
    try:
        codex_dir_path = Path.home() / ".codex"
        if codex_dir_path.exists():
            from .codex_agents import install_codex_agents_include
            out = install_codex_agents_include(codex_dir=codex_dir_path)
            results["codex_agents_include"] = str(out)
        else:
            results["codex_agents_include_skipped"] = "codex not installed (~/.codex/ missing)"
    except Exception as e:  # noqa: BLE001
        results["codex_agents_include_error"] = str(e)

    # ---- Codex notify wrapper (auto-detect if codex is installed) ----
    try:
        codex_dir = Path.home() / ".codex"
        codex_cfg = codex_dir / "config.toml"
        repo_root = Path(__file__).resolve().parents[3]
        wrapper = repo_root / "plugins" / "codex" / "notify-wrapper.sh"
        probe = repo_root / "plugins" / "codex" / "notify-probe.sh"
        if not codex_cfg.exists():
            results["codex_wrapper_skipped"] = "codex not installed (~/.codex/config.toml missing)"
        elif not wrapper.exists():
            results["codex_wrapper_error"] = f"wrapper script missing: {wrapper}"
        else:
            swap_codex_notify(
                to="wrapper",
                codex_dir=codex_dir,
                backup_dir=Path.home() / ".claude" / "backups",
                probe_path=str(probe),
                wrapper_path=str(wrapper),
            )
            results["codex_wrapper"] = str(wrapper)
    except Exception as e:  # noqa: BLE001
        results["codex_wrapper_error"] = str(e)

    # ---- launchd mirror (macOS only; FS-watch for codex + openclaw rollout files) ----
    if plat == "darwin":
        try:
            repo_root = Path(__file__).resolve().parents[3]
            template = repo_root / "plugins" / "codex" / "launchd" / "com.memoryd.mirror.plist"
            memoryd_bin = repo_root / "memoryd" / ".venv" / "bin" / "memoryd"
            if not template.exists():
                results["launchd_mirror_error"] = f"template missing: {template}"
            elif not memoryd_bin.exists():
                results["launchd_mirror_error"] = f"memoryd binary missing: {memoryd_bin}"
            else:
                out = install_launchd_mirror(
                    template_path=template,
                    launch_dir=Path.home() / "Library" / "LaunchAgents",
                    memoryd_bin=str(memoryd_bin),
                    data_root=str(Path.home() / ".local" / "share" / "memoryd"),
                )
                results["launchd_mirror"] = str(out)
        except Exception as e:  # noqa: BLE001
            results["launchd_mirror_error"] = str(e)
    else:
        results["launchd_mirror_skipped"] = f"platform {plat} (launchd is macOS-only)"

    # ---- OpenClaw plugin (auto-detect if openclaw CLI is on PATH) ----
    try:
        import shutil as _sh
        import subprocess as _sp
        openclaw_bin = _sh.which("openclaw")
        if openclaw_bin is None:
            results["openclaw_plugin_skipped"] = "openclaw CLI not on PATH"
        else:
            repo_root = Path(__file__).resolve().parents[3]
            plugin_dir = repo_root / "plugins" / "openclaw"
            r = _sp.run(
                [openclaw_bin, "plugins", "install", "--force", str(plugin_dir)],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0:
                results["openclaw_plugin"] = str(plugin_dir)
            else:
                # OpenClaw blocks plugins with `child_process` for safety.
                # The memoryd plugin legitimately spawns the local memoryd CLI;
                # tell the user how to bypass instead of failing silently.
                stderr = (r.stderr or r.stdout or "").strip()[:300]
                if "dangerous code patterns" in stderr or "child_process" in stderr:
                    results["openclaw_plugin_skipped"] = (
                        "OpenClaw safety policy blocks the memoryd plugin "
                        "(uses child_process to spawn local memoryd CLI). "
                        "Run with --trust manually: "
                        f"openclaw plugins install --force --trust {plugin_dir} "
                        "or rely on the launchd mirror fallback (already installed)."
                    )
                else:
                    results["openclaw_plugin_error"] = (
                        f"openclaw plugins install rc={r.returncode}: {stderr}"
                    )
    except Exception as e:  # noqa: BLE001
        results["openclaw_plugin_error"] = str(e)

    # ---- MCP registration: upgrade legacy memoryd-server → memoryd-mcp ----
    try:
        cc_json = Path.home() / ".claude.json"
        if not cc_json.exists():
            results["mcp_registration_skipped"] = "~/.claude.json not present"
        else:
            data = json.loads(cc_json.read_text(encoding="utf-8"))
            servers = data.setdefault("mcpServers", {})
            repo_root = Path(__file__).resolve().parents[3]
            target_bin = repo_root / "memoryd" / ".venv" / "bin" / "memoryd-mcp"
            data_root = str(Path.home() / ".local" / "share" / "memoryd")
            existing = servers.get("memoryd") or {}
            wants_upgrade = (
                "memoryd" not in servers
                or "memoryd-server" in (existing.get("command") or "")
                or existing.get("command") != str(target_bin)
            )
            if wants_upgrade and target_bin.exists():
                backup_file(cc_json, backup_dir=Path.home() / ".claude" / "backups")
                servers["memoryd"] = {
                    "command": str(target_bin),
                    "env": {"MEMORYD_DATA_ROOT": data_root},
                }
                _atomic_write(cc_json, json.dumps(data, indent=2, ensure_ascii=False))
                results["mcp_registration"] = str(target_bin)
            elif not target_bin.exists():
                results["mcp_registration_error"] = f"memoryd-mcp binary missing: {target_bin}"
            else:
                results["mcp_registration_skipped"] = "already pointing at memoryd-mcp"
    except Exception as e:  # noqa: BLE001
        results["mcp_registration_error"] = str(e)

    # ---- Default LLM provider: prefer claude-code if claude CLI exists ----
    try:
        import shutil as _sh
        from .config import load_config, set_config_key as set_config_value
        cfg = load_config()
        current_provider = cfg.get("llm", {}).get("provider")
        claude_bin = _sh.which("claude")
        if current_provider == "claude-code":
            results["llm_provider_skipped"] = "already claude-code"
        elif claude_bin is None:
            results["llm_provider_skipped"] = "claude CLI not on PATH — keep existing provider"
        elif current_provider == "anthropic" and "ANTHROPIC_API_KEY" not in os.environ:
            # User configured anthropic but has no key, AND has claude CLI → switch.
            set_config_value("llm.provider", "claude-code")
            set_config_value("llm.model", "claude-haiku-4-5")
            results["llm_provider"] = "switched to claude-code (was anthropic without key)"
        else:
            results["llm_provider_skipped"] = f"keep {current_provider} (active credentials)"
    except Exception as e:  # noqa: BLE001
        results["llm_provider_error"] = str(e)

    return results
