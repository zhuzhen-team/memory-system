"""``memoryd doctor`` — health check.

One command answers the question: *is my memoryd actually doing work?*

Each check returns a ``CheckResult`` with one of four states:
- ``ok``    — everything in order
- ``warn``  — non-fatal but something to fix (e.g. legacy MCP wired)
- ``fail``  — broken; that feature won't work until addressed
- ``info``  — purely informational (counts, not a pass/fail)

Use this module as a library (``run_all_checks``) for the CLI subcommand,
or for tests / other tooling that wants to consume the JSON shape.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

Status = Literal["ok", "warn", "fail", "info"]


@dataclass
class CheckResult:
    """One row of the health report.

    Attributes
    ----------
    id:
        Stable machine id for JSON consumers / tests.
    label:
        Human-readable name printed in the table.
    status:
        ``ok`` / ``warn`` / ``fail`` / ``info``.
    value:
        What was actually found (e.g. path, count, version).
    hint:
        Optional fix instruction shown next to non-OK rows.
    """

    id: str
    label: str
    status: Status
    value: str
    hint: Optional[str] = None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _data_root_default() -> Path:
    override = os.environ.get("MEMORYD_DATA_ROOT")
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "memoryd"


def check_binary() -> CheckResult:
    """``which memoryd`` resolves on PATH (or we are at least running it)."""
    found = shutil.which("memoryd")
    if found:
        return CheckResult("binary", "binary", "ok", found)
    # Maybe we're being invoked from inside a venv that hasn't been added to PATH
    if sys.argv and Path(sys.argv[0]).name.startswith("memoryd"):
        return CheckResult(
            "binary",
            "binary",
            "warn",
            sys.argv[0],
            hint="binary works but not on PATH; add the venv bin/ to PATH",
        )
    return CheckResult(
        "binary",
        "binary",
        "fail",
        "(not on PATH)",
        hint="install with `uv pip install -e .` or add venv bin/ to PATH",
    )


def check_python_version() -> CheckResult:
    """Memoryd needs Python >=3.11."""
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) < (3, 11):
        return CheckResult(
            "python_version",
            "python version",
            "fail",
            version,
            hint="memoryd requires Python >=3.11",
        )
    return CheckResult("python_version", "python version", "ok", version)


def check_data_root(data_root: Path) -> CheckResult:
    """The data root directory exists and ``index.db`` opens."""
    if not data_root.exists():
        return CheckResult(
            "data_root",
            "data root",
            "fail",
            str(data_root),
            hint="run any memoryd command (e.g. `memoryd list`) to bootstrap",
        )
    db = data_root / "index.db"
    if not db.exists():
        return CheckResult(
            "data_root",
            "data root",
            "warn",
            str(data_root),
            hint="index.db missing; run `memoryd rebuild-index`",
        )
    # Try to open the DB
    try:
        import sqlite3

        conn = sqlite3.connect(str(db))
        conn.execute("SELECT 1").fetchone()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "data_root",
            "data root",
            "fail",
            str(data_root),
            hint=f"index.db unreadable: {exc!s}",
        )
    return CheckResult("data_root", "data root", "ok", str(data_root))


def _open_conn(data_root: Path):
    """Open a raw sqlite3 connection or return None."""
    import sqlite3

    db = data_root / "index.db"
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def check_memory_counts(data_root: Path) -> CheckResult:
    """Print per-type counts so the user can see *what* is stored."""
    conn = _open_conn(data_root)
    if conn is None:
        return CheckResult(
            "memory_counts",
            "memory counts",
            "fail",
            "(db missing)",
            hint="run `memoryd rebuild-index`",
        )
    try:
        rows = conn.execute(
            "SELECT type, COUNT(*) FROM memories GROUP BY type"
        ).fetchall()
        counts = {r[0]: r[1] for r in rows}
        total = sum(counts.values())
    except Exception as exc:  # noqa: BLE001
        conn.close()
        return CheckResult(
            "memory_counts",
            "memory counts",
            "fail",
            "(query failed)",
            hint=f"{exc!s}; try `memoryd rebuild-index`",
        )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    parts: list[str] = []
    for t in ("session", "decision", "fact", "preference", "playbook", "warning"):
        parts.append(f"{t}:{counts.get(t, 0)}")
    value = "  ".join(parts)
    if total == 0:
        return CheckResult(
            "memory_counts",
            "memory counts",
            "warn",
            value,
            hint="no memories yet; trigger a SessionEnd hook or `memoryd capture`",
        )
    return CheckResult("memory_counts", "memory counts", "ok", value)


def check_entities(data_root: Path) -> CheckResult:
    """Knowledge graph populated? Zero entities + sessions present = KG never ran."""
    conn = _open_conn(data_root)
    if conn is None:
        return CheckResult(
            "entities",
            "entities (KG)",
            "fail",
            "(db missing)",
            hint="run `memoryd rebuild-index`",
        )
    try:
        try:
            n_ent = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        except Exception:  # noqa: BLE001 — table may not exist yet
            return CheckResult(
                "entities",
                "entities (KG)",
                "warn",
                "(table missing)",
                hint="run `memoryd rebuild-index` to apply latest migrations",
            )
        n_sessions = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE type = 'session'"
        ).fetchone()[0]
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    if n_ent > 0:
        return CheckResult("entities", "entities (KG)", "ok", f"{n_ent} entities")
    if n_sessions > 0:
        return CheckResult(
            "entities",
            "entities (KG)",
            "warn",
            f"0 entities (sessions={n_sessions})",
            hint=(
                "knowledge graph never ran; trigger `memoryd analyze-session <slug>` "
                "or set up an LLM provider so capture spawns it"
            ),
        )
    return CheckResult(
        "entities",
        "entities (KG)",
        "info",
        "0 entities (no sessions yet)",
    )


def check_pending_promotions(data_root: Path) -> CheckResult:
    """User-action backlog: how many DURA-scored candidates wait for approval.

    These are memories the LLM judged worth promoting to long-term, but they
    sit in the ``promotions`` table until the user runs ``memoryd promote``
    or batch-approves via ``memoryd digest --tui`` (press ``a``).

    Thresholds:
      * 0          → ok  (nothing to do)
      * 1..19      → info (small backlog, surface but don't alarm)
      * 20..99     → warn (real backlog accumulating)
      * >=100      → warn (large; degrades identity quality)
    """
    conn = _open_conn(data_root)
    if conn is None:
        return CheckResult(
            "pending_promotions",
            "pending promotions",
            "info",
            "(db missing)",
        )
    try:
        try:
            n_pending = conn.execute(
                "SELECT COUNT(*) FROM promotions WHERE status = 'pending'"
            ).fetchone()[0]
        except Exception:  # noqa: BLE001 — table may not exist yet
            return CheckResult(
                "pending_promotions",
                "pending promotions",
                "info",
                "(table missing)",
            )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    if n_pending == 0:
        return CheckResult(
            "pending_promotions",
            "pending promotions",
            "ok",
            "0",
        )
    if n_pending < 20:
        return CheckResult(
            "pending_promotions",
            "pending promotions",
            "info",
            f"{n_pending}",
            hint=(
                "candidates wait for approval; run `memoryd digest` to preview, "
                "`memoryd digest --tui` then press `a` to approve all"
            ),
        )
    return CheckResult(
        "pending_promotions",
        "pending promotions",
        "warn",
        f"{n_pending} candidates not yet approved → long-term库为空",
        hint=(
            "积压会拖垮 identity.md 质量。`memoryd digest --tui` 按 `a` 一键批；"
            "或 `memoryd promote --all` 批量；或 CC 里说"
            "「列出 pending memories 帮我批」让 AI 处理"
        ),
    )


def check_identity(data_root: Path) -> CheckResult:
    """Has the user's ``identity.md`` been written by the profile module?"""
    # Honor MEMORYD_PROFILE_DIR override the profile module supports.
    override = os.environ.get("MEMORYD_PROFILE_DIR")
    if override:
        profile_dir = Path(override)
    else:
        profile_dir = data_root / "profile"
    identity = profile_dir / "identity.md"
    conn = _open_conn(data_root)
    n_sessions = 0
    n_versions = 0
    last_written: str | None = None
    if conn is not None:
        try:
            try:
                n_sessions = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE type = 'session'"
                ).fetchone()[0]
            except Exception:  # noqa: BLE001
                n_sessions = 0
            try:
                row = conn.execute(
                    "SELECT MAX(version_num), MAX(written_at) FROM profile_versions"
                ).fetchone()
                n_versions = row[0] or 0
                last_written = row[1]
            except Exception:  # noqa: BLE001
                n_versions = 0
                last_written = None
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    if identity.exists():
        suffix = f"v{n_versions}" if n_versions else ""
        when = f"  last={last_written[:19]}" if last_written else ""
        return CheckResult(
            "identity",
            "identity.md",
            "ok",
            f"{identity} {suffix}{when}".strip(),
        )
    if n_sessions >= 5:
        return CheckResult(
            "identity",
            "identity.md",
            "warn",
            f"missing (sessions={n_sessions})",
            hint="run `memoryd profile rewrite` to bootstrap; needs LLM",
        )
    return CheckResult(
        "identity",
        "identity.md",
        "info",
        f"missing (sessions={n_sessions}, threshold=5)",
    )


def _read_claude_settings(path: Path | None = None) -> dict[str, Any]:
    settings = path or (Path.home() / ".claude" / "settings.json")
    if not settings.exists():
        return {}
    try:
        return json.loads(settings.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _cc_hook_command(event: str, settings: dict[str, Any]) -> str | None:
    """Return the first command string registered under hooks.<event>."""
    hooks = settings.get("hooks", {})
    entries = hooks.get(event)
    if not entries:
        return None
    for m in entries:
        for h in m.get("hooks", []) or []:
            cmd = h.get("command")
            if cmd:
                return cmd
    return None


def _command_references_file(cmd: str) -> Path | None:
    """Extract a referenced .py / .sh path out of a hook command string."""
    # Hooks normally look like: python3 "/abs/path/session-end.py"
    # We just look for tokens ending in .py/.ps1/.sh.
    import re

    tokens = re.findall(r'"([^"]+)"|(\S+)', cmd)
    for quoted, bare in tokens:
        token = quoted or bare
        for ext in (".py", ".sh", ".ps1"):
            if token.endswith(ext):
                return Path(token)
    return None


def _check_cc_hook(event: str, label: str, *, settings_path: Path | None = None) -> CheckResult:
    settings = _read_claude_settings(settings_path)
    if not settings:
        return CheckResult(
            f"cc_{event.lower()}_hook",
            f"CC {event}",
            "fail",
            "(no ~/.claude/settings.json)",
            hint=f"run `memoryd setup install-cc-hook --include-session-start`",
        )
    cmd = _cc_hook_command(event, settings)
    if cmd is None:
        return CheckResult(
            f"cc_{event.lower()}_hook",
            f"CC {event}",
            "fail",
            "(not registered)",
            hint=f"run `memoryd setup install-cc-hook` (and `--include-session-start`)",
        )
    script = _command_references_file(cmd)
    if script and not script.exists():
        return CheckResult(
            f"cc_{event.lower()}_hook",
            f"CC {event}",
            "warn",
            f"registered but script missing: {script}",
            hint="re-install via `memoryd setup install-cc-hook`",
        )
    short = str(script) if script else cmd[:60]
    return CheckResult(f"cc_{event.lower()}_hook", f"CC {event}", "ok", short)


def check_cc_session_start_hook(*, settings_path: Path | None = None) -> CheckResult:
    return _check_cc_hook("SessionStart", "CC SessionStart", settings_path=settings_path)


def check_cc_session_end_hook(*, settings_path: Path | None = None) -> CheckResult:
    return _check_cc_hook("SessionEnd", "CC SessionEnd", settings_path=settings_path)


def check_codex_notify(*, codex_config: Path | None = None) -> CheckResult:
    """Is the Codex notify line pointed at our wrapper?"""
    cfg = codex_config or (Path.home() / ".codex" / "config.toml")
    if not cfg.exists():
        return CheckResult(
            "codex_notify",
            "Codex notify wrapper",
            "info",
            "(not installed)",
            hint="optional — only if you use Codex",
        )
    text = cfg.read_text(encoding="utf-8", errors="ignore")
    if "notify-wrapper" not in text and "notify-probe" not in text:
        return CheckResult(
            "codex_notify",
            "Codex notify wrapper",
            "info",
            "Codex installed but not wrapped",
            hint="run `memoryd setup swap-codex-notify --to wrapper` to capture Codex sessions",
        )
    # Find the path
    import re

    m = re.search(r'"([^"]*notify-(wrapper|probe)\.sh)"', text)
    if not m:
        return CheckResult("codex_notify", "Codex notify wrapper", "ok", "registered")
    p = Path(m.group(1))
    if not p.exists():
        return CheckResult(
            "codex_notify",
            "Codex notify wrapper",
            "warn",
            f"path missing: {p}",
            hint="re-install via `memoryd setup swap-codex-notify --to wrapper`",
        )
    return CheckResult("codex_notify", "Codex notify wrapper", "ok", str(p))


def _launchctl_list() -> str | None:
    """Return ``launchctl list`` output, or None if unavailable."""
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if out.returncode != 0:
            return None
        return out.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


_LAUNCHD_LABELS = {
    "mirror": "com.memoryd.mirror",
    "decay": "com.memoryd.decay-sweep",
    "digest": "com.memoryd.weekly-digest",
    "weekly_identity": "com.memoryd.weekly-identity",
    "monthly_report": "com.memoryd.monthly-report",
}


def _launchd_check(
    label: str,
    *,
    launchctl_output: str | None,
    plist_dir: Path | None = None,
    hint_install: str,
) -> tuple[Status, str, str | None]:
    """Return (status, value, hint) for one launchd-managed agent."""
    plat = platform.system()
    if plat != "Darwin":
        # Linux uses systemd timers; Windows uses Schtasks. Out of scope for
        # the launchd-specific labels — treat as info so we don't false-alarm.
        return ("info", f"(skipped, platform={plat})", None)
    plist_path = (plist_dir or (Path.home() / "Library" / "LaunchAgents")) / f"{label}.plist"
    if launchctl_output and label in launchctl_output:
        # Parse PID column to surface "running" vs "loaded but not running".
        pid: str | None = None
        for line in launchctl_output.splitlines():
            if label in line:
                cols = line.split()
                if cols and cols[0].isdigit():
                    pid = cols[0]
                break
        if pid:
            return ("ok", f"running (PID {pid})", None)
        if plist_path.exists():
            return ("ok", f"loaded ({plist_path})", None)
        return ("ok", "loaded", None)
    if plist_path.exists():
        return (
            "warn",
            f"plist exists but not loaded ({plist_path})",
            f"launchctl bootstrap gui/$(id -u) {plist_path}",
        )
    return ("warn", "not registered", hint_install)


def check_launchd_mirror(*, launchctl_output: str | None = None, plist_dir: Path | None = None) -> CheckResult:
    label = _LAUNCHD_LABELS["mirror"]
    out = launchctl_output if launchctl_output is not None else _launchctl_list()
    status, value, hint = _launchd_check(
        label,
        launchctl_output=out,
        plist_dir=plist_dir,
        hint_install="run `memoryd setup install-launchd-mirror`",
    )
    return CheckResult("launchd_mirror", "launchd mirror", status, value, hint)


def check_launchd_cron(
    key: str,
    *,
    launchctl_output: str | None = None,
    plist_dir: Path | None = None,
) -> CheckResult:
    label = _LAUNCHD_LABELS[key]
    out = launchctl_output if launchctl_output is not None else _launchctl_list()
    pretty = key.replace("_", "-")
    status, value, hint = _launchd_check(
        label,
        launchctl_output=out,
        plist_dir=plist_dir,
        hint_install=f"run `memoryd setup install-cron --{pretty.replace('-', '-')}`",
    )
    # Map key → flag name memoryd CLI understands.
    flag_map = {
        "decay": "--decay",
        "digest": "--digest",
        "weekly_identity": "--weekly-identity",
        "monthly_report": "--monthly-report",
    }
    if status == "warn" and hint is not None and not hint.startswith("launchctl"):
        hint = f"run `memoryd setup install-cron {flag_map[key]}`"
    return CheckResult(f"launchd_{key}", f"launchd {pretty}", status, value, hint)


def check_llm_provider() -> CheckResult:
    """LLM provider configured + reachable (env var or CLI present)?"""
    try:
        from .config import load_config

        cfg = load_config()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "llm_provider",
            "LLM provider",
            "fail",
            "(config load failed)",
            hint=f"{exc!s}",
        )
    provider = (cfg.get("llm") or {}).get("provider", "anthropic")
    model = (cfg.get("llm") or {}).get("model", "")
    api_key_env = (cfg.get("llm") or {}).get("api_key_env", "ANTHROPIC_API_KEY")

    if provider == "claude-code":
        # Provider reuses local Claude Code subscription; binary must be present.
        claude_bin = shutil.which("claude")
        if claude_bin:
            return CheckResult(
                "llm_provider",
                "LLM provider",
                "ok",
                f"claude-code ({claude_bin})",
            )
        return CheckResult(
            "llm_provider",
            "LLM provider",
            "warn",
            "claude-code but `claude` CLI not on PATH",
            hint="install Claude Code, or change `llm.provider` in config.toml",
        )

    if provider in ("anthropic", "openai", "openrouter", "azure-openai"):
        if os.environ.get(api_key_env):
            return CheckResult(
                "llm_provider",
                "LLM provider",
                "ok",
                f"{provider} model={model or '(default)'}",
            )
        return CheckResult(
            "llm_provider",
            "LLM provider",
            "warn",
            f"{provider} but ${api_key_env} not set",
            hint=(
                "set the env var, or switch to claude-code to reuse your local CC "
                "subscription: `memoryd config set llm.provider claude-code`"
            ),
        )

    if provider in ("ollama", "local"):
        # Just check ollama binary exists if requested.
        if shutil.which("ollama"):
            return CheckResult(
                "llm_provider",
                "LLM provider",
                "ok",
                f"{provider} model={model or '(default)'}",
            )
        return CheckResult(
            "llm_provider",
            "LLM provider",
            "warn",
            f"{provider} but `ollama` CLI not on PATH",
            hint="install ollama and run `ollama serve`",
        )

    return CheckResult(
        "llm_provider",
        "LLM provider",
        "warn",
        f"unknown provider {provider!r}",
        hint="see `memoryd config show` and pick a known provider",
    )


def check_mcp_registered(*, claude_json: Path | None = None) -> CheckResult:
    """Is memoryd-mcp wired into ~/.claude.json?

    Important nuance: lots of users still have ``memoryd-server`` (legacy,
    single tool) wired in. We surface that as WARN with an upgrade hint so
    they don't silently miss the 13-tool surface.
    """
    cj = claude_json or (Path.home() / ".claude.json")
    if not cj.exists():
        return CheckResult(
            "mcp_registered",
            "MCP server",
            "warn",
            "(not registered with CC)",
            hint="add memoryd-mcp to ~/.claude.json or run `memoryd setup auto-install`",
        )
    try:
        data = json.loads(cj.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return CheckResult(
            "mcp_registered",
            "MCP server",
            "warn",
            "~/.claude.json present but unreadable",
            hint=None,
        )
    servers = data.get("mcpServers", {}) or {}
    entry = servers.get("memoryd")
    if not entry:
        return CheckResult(
            "mcp_registered",
            "MCP server",
            "warn",
            "(memoryd not in mcpServers)",
            hint="register memoryd-mcp in ~/.claude.json",
        )
    cmd = entry.get("command", "")
    if "memoryd-server" in cmd:
        return CheckResult(
            "mcp_registered",
            "MCP server",
            "warn",
            f"legacy memoryd-server: {cmd}",
            hint=(
                "edit ~/.claude.json: change mcpServers.memoryd.command to "
                "the absolute path of `memoryd-mcp` (no args). The new server "
                "exposes 13 tools instead of 1."
            ),
        )
    if "memoryd-mcp" in cmd:
        return CheckResult("mcp_registered", "MCP server", "ok", cmd)
    return CheckResult(
        "mcp_registered",
        "MCP server",
        "warn",
        f"command not recognized: {cmd}",
        hint="expected `memoryd-mcp`",
    )


def check_recent_capture(data_root: Path, *, days: int = 7) -> CheckResult:
    """Has the system captured anything in the last *days*?"""
    conn = _open_conn(data_root)
    if conn is None:
        return CheckResult(
            "recent_capture",
            f"recent capture (<{days}d)",
            "info",
            "(db missing)",
        )
    try:
        try:
            n_total = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE type='session'"
            ).fetchone()[0]
        except Exception:  # noqa: BLE001
            return CheckResult(
                "recent_capture",
                f"recent capture (<{days}d)",
                "info",
                "(query failed)",
            )
        if n_total == 0:
            return CheckResult(
                "recent_capture",
                f"recent capture (<{days}d)",
                "info",
                "0 sessions",
            )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        n_recent = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE type='session' AND created_at >= ?",
            (cutoff,),
        ).fetchone()[0]
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    if n_recent > 0:
        return CheckResult(
            "recent_capture",
            f"recent capture (<{days}d)",
            "ok",
            f"{n_recent} sessions in last {days}d",
        )
    return CheckResult(
        "recent_capture",
        f"recent capture (<{days}d)",
        "warn",
        f"0 sessions in last {days}d (total={n_total})",
        hint="SessionEnd hook may have stopped firing — check `memoryd doctor` again after a CC session",
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


_OVERALL_ORDER = {"fail": 0, "warn": 1, "info": 2, "ok": 3}


def overall_status(results: Iterable[CheckResult]) -> Status:
    """Return the worst non-info status across checks. ``info`` is ignored."""
    worst = "ok"
    for r in results:
        if r.status == "info":
            continue
        if _OVERALL_ORDER[r.status] < _OVERALL_ORDER[worst]:
            worst = r.status
    return worst  # type: ignore[return-value]


def run_all_checks(*, data_root: Path | None = None) -> list[CheckResult]:
    """Run every check in display order. Pure orchestration — no I/O of its own."""
    root = data_root if data_root is not None else _data_root_default()
    # Pre-fetch launchctl output once (slow on macOS) and share across checks.
    launchctl_out = _launchctl_list()

    results: list[CheckResult] = [
        check_binary(),
        check_python_version(),
        check_data_root(root),
        check_memory_counts(root),
        check_entities(root),
        check_pending_promotions(root),
        check_identity(root),
        check_cc_session_start_hook(),
        check_cc_session_end_hook(),
        check_codex_notify(),
        check_launchd_mirror(launchctl_output=launchctl_out),
        check_launchd_cron("decay", launchctl_output=launchctl_out),
        check_launchd_cron("digest", launchctl_output=launchctl_out),
        check_launchd_cron("weekly_identity", launchctl_output=launchctl_out),
        check_launchd_cron("monthly_report", launchctl_output=launchctl_out),
        check_sync_setup(launchctl_output=launchctl_out),
        check_llm_provider(),
        check_mcp_registered(),
        check_recent_capture(root),
    ]
    return results


def check_sync_setup(*, launchctl_output: str | None = None) -> CheckResult:
    """Cross-device sync: is sync.dir configured + sync_push cron loaded?

    Three states:
      * OK   — sync.dir set AND sync_push launchd loaded
      * INFO — sync.dir unset (single-device user, that's fine)
      * WARN — sync.dir set but cron not loaded (config drift / install gap)
    """
    try:
        from .config import load_config as _load_cfg
        cfg = _load_cfg() or {}
        sync_dir = cfg.get("sync", {}).get("dir") if isinstance(cfg, dict) else None
        if not sync_dir and hasattr(cfg, "sync"):
            sync_dir = getattr(cfg.sync, "dir", None)
    except Exception:  # noqa: BLE001
        sync_dir = None

    if not sync_dir:
        return CheckResult(
            "sync_setup",
            "sync (cross-device)",
            "info",
            "sync.dir 未配置（单设备使用，无需）",
            hint=(
                "想跨设备：`memoryd config set sync.dir ~/Dropbox/memoryd` "
                "然后 `memoryd setup auto-install` 装日跑 cron"
            ),
        )

    # sync.dir configured — check that sync_push cron is loaded
    label = "com.memoryd.sync-push"
    if launchctl_output is None:
        launchctl_output = _launchctl_list()
    loaded = label in (launchctl_output or "")
    if loaded:
        return CheckResult(
            "sync_setup",
            "sync (cross-device)",
            "ok",
            f"sync.dir={sync_dir} + 每天 03:30 自动 push",
        )
    return CheckResult(
        "sync_setup",
        "sync (cross-device)",
        "warn",
        f"sync.dir={sync_dir} 但 cron 未装",
        hint="run `memoryd setup install-cron --sync-push`",
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


_GLYPHS = {
    "ok": ("[OK]  ", "\x1b[32m"),
    "warn": ("[WARN]", "\x1b[33m"),
    "fail": ("[FAIL]", "\x1b[31m"),
    "info": ("[INFO]", "\x1b[36m"),
}


def _format_row(r: CheckResult, *, color: bool) -> str:
    glyph, color_code = _GLYPHS[r.status]
    reset = "\x1b[0m"
    label = r.label.ljust(28)
    if color:
        head = f"{color_code}{glyph}{reset} {label}"
    else:
        head = f"{glyph} {label}"
    line = f"{head} {r.value}"
    if r.hint:
        line += f"\n       → {r.hint}"
    return line


def format_human(results: list[CheckResult], *, quiet: bool = False) -> str:
    color = _supports_color()
    overall = overall_status(results)
    lines: list[str] = ["memoryd doctor — 健康检查", ""]
    for r in results:
        if quiet and r.status == "ok":
            continue
        lines.append(_format_row(r, color=color))
    lines.append("")
    counts = {"ok": 0, "warn": 0, "fail": 0, "info": 0}
    for r in results:
        counts[r.status] += 1
    lines.append(
        f"summary: ok={counts['ok']}  warn={counts['warn']}  "
        f"fail={counts['fail']}  info={counts['info']}  →  overall={overall.upper()}"
    )
    if overall != "ok":
        lines.append(
            "tip: run `memoryd setup auto-install` to fix what auto-install can; "
            "follow individual hints above for the rest."
        )
    return "\n".join(lines)


def format_json(results: list[CheckResult]) -> str:
    payload = {
        "overall": overall_status(results),
        "checks": [asdict(r) for r in results],
        "summary": _summary_text(results),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _summary_text(results: list[CheckResult]) -> str:
    counts = {"ok": 0, "warn": 0, "fail": 0, "info": 0}
    for r in results:
        counts[r.status] += 1
    return (
        f"ok={counts['ok']} warn={counts['warn']} "
        f"fail={counts['fail']} info={counts['info']}"
    )
