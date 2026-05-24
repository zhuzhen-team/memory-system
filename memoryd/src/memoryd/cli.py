"""CLI entry points.

v1.0-α subcommands:
  memoryd capture   — invoked by tool hooks; reads JSON payload from stdin
                       and writes a session markdown to the data root
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .index import open_index as _open_idx
from .mirror import MirrorRouter
from .mirror_codex import CodexRolloutHandler
from .mirror_openclaw import OpenClawSessionHandler
from .schema import Frontmatter, SessionMemory
from .scope import resolve_scope_root, scope_hash
from .storage import load_session, save_session
from . import setup as setup_mod
from . import config as config_mod
from .governance.analyze import analyze_session as _analyze_session
from .llm import LLMUnavailable, get_provider
from . import scope_meta as _scope_meta
from . import enc as _enc


DEFAULT_DATA_ROOT = Path.home() / ".local" / "share" / "memoryd"


def _data_root() -> Path:
    override = os.environ.get("MEMORYD_DATA_ROOT")
    if override:
        return Path(override)
    return DEFAULT_DATA_ROOT


def _read_transcript_text(transcript_path: str) -> str | None:
    """Read up to last 50 message contents from a Claude Code transcript JSONL.

    Returns None if file missing/unreadable.
    """
    try:
        path = Path(transcript_path)
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        # Take last 50 lines for v1.0-α naive summary
        recent = lines[-50:]
        chunks: list[str] = []
        for raw in recent:
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message", {})
            content = msg.get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        chunks.append(c.get("text", ""))
            elif isinstance(content, str):
                chunks.append(content)
        return "\n".join(chunks).strip() or None
    except OSError:
        return None


def _summarize_naively(text: str, max_chars: int = 2000) -> str:
    """Naive truncation summary for v1.0-α. Plan 3 replaces with LLM call."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[...truncated]"


def capture_session(
    payload: dict[str, Any],
    *,
    memory_root: Path | None = None,
    now: datetime | None = None,
    source: str = "claude-code",
) -> Path:
    """Convert a SessionEnd hook payload into a SessionMemory markdown file.

    `source` is recorded in frontmatter for downstream filtering. Conventional
    values: claude-code | codex | openclaw | manual.
    """
    if memory_root is None:
        memory_root = _data_root()
    if now is None:
        now = datetime.now()

    session_id = payload.get("session_id", "unknown")
    # Sanitize session_id since it flows into the filename slug;
    # CC currently emits UUIDs so this is defense in depth.
    session_id = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
    # Collapse consecutive dots so ".." cannot form a path traversal component.
    session_id = re.sub(r"\.{2,}", "_", session_id)
    transcript_path = payload.get("transcript_path", "")
    cwd = payload.get("cwd", str(Path.cwd()))

    scope_root = resolve_scope_root(Path(cwd))
    sh = scope_hash(scope_root)

    transcript_text = _read_transcript_text(transcript_path)
    if transcript_text is None:
        body = (
            f"## 无 transcript（transcript unavailable）\n\n"
            f"transcript_path: `{transcript_path}`\n"
            f"session_id: `{session_id}`\n"
        )
    else:
        summary = _summarize_naively(transcript_text)
        body = f"## 摘要（朴素截断，v1.0-α）\n\n{summary}\n"

    slug = f"{now:%Y-%m-%d}-{session_id}"
    title = f"{now:%Y-%m-%d} 会话 {session_id[:8]}"

    session = SessionMemory(
        frontmatter=Frontmatter(
            title=title,
            slug=slug,
            type="session",
            scope_hash=sh,
            triggers=[],
            source=source,
            created_at=now,
        ),
        body=body,
    )
    path = save_session(memory_root, session)
    _spawn_analyze(session.frontmatter.slug)
    return path


def _spawn_analyze(session_slug: str) -> None:
    """Background spawn `memoryd analyze-session <slug>`. Never blocks."""
    try:
        subprocess.Popen(
            [sys.executable, "-m", "memoryd", "analyze-session", session_slug],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


_AUTO_IMPORT_THROTTLE_SEC = 300  # 5 minutes


def _maybe_auto_import() -> None:
    """Pre-capture hook: fork `memoryd sync import --auto` if user opted in.

    Gated on `[sync] enabled=true AND auto_import_on_session_start=true`.
    Throttled via mtime of `<data_root>/last_import_at` marker to avoid
    fork avalanches when rapid-fire sessions land.

    Never raises; capture flow must continue regardless.
    """
    try:
        from .config import load_config
        cfg = load_config()
        if not (cfg.sync.enabled and cfg.sync.auto_import_on_session_start):
            return
    except Exception:
        return
    # Throttle marker lives under user data dir so respects MEMORYD_DATA_ROOT
    # indirectly via Path.home() (tests patch Path.home).
    marker = Path.home() / ".local" / "share" / "memoryd" / "last_import_at"
    if marker.exists():
        try:
            if time.time() - marker.stat().st_mtime < _AUTO_IMPORT_THROTTLE_SEC:
                return
        except OSError:
            pass
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        # If we cannot write marker, still fork once (best-effort, no throttle).
        pass
    memoryd_bin = shutil.which("memoryd") or sys.executable
    try:
        subprocess.Popen(
            [memoryd_bin, "sync", "import", "--auto"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def cmd_analyze_session(args: argparse.Namespace) -> int:
    memory_root = _data_root()
    try:
        provider = get_provider()
    except LLMUnavailable as e:
        print(f"analyze-session skip: {e}", file=sys.stderr)
        return 0
    _analyze_session(memory_root, session_slug=args.session_slug, provider=provider)
    print("analyze-session: ok", file=sys.stderr)
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    # Best-effort: fire-and-forget background sync import if user opted in.
    # Throttled (5min) to avoid fork avalanches on rapid sessions.
    _maybe_auto_import()
    raw = sys.stdin.read()
    if not raw.strip():
        print("error: empty stdin; expected JSON payload", file=sys.stderr)
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON on stdin: {e}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print(f"error: expected JSON object, got {type(payload).__name__}", file=sys.stderr)
        return 2
    path = capture_session(payload, source=args.source)
    print(f"captured -> {path}", file=sys.stderr)
    return 0


def cmd_inject(args: argparse.Namespace) -> int:
    """Print a SessionStart context block to stdout.

    Output is meant to be piped into a CC SessionStart hook (which feeds
    stdout into ``additionalContext``). Always exits 0 — even on failure
    we emit the graceful fallback line from :mod:`memoryd.inject`.
    """
    from .inject import render_session_context

    scope = args.scope
    # `--scope=auto` is shorthand for "infer from CLAUDE_PROJECT_DIR / cwd"
    # so SessionStart hooks don't have to plumb scope_hash themselves.
    if scope == "auto":
        cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        try:
            scope = scope_hash(resolve_scope_root(Path(cwd)))
        except Exception:
            scope = None
    elif scope in ("", "global", "_global"):
        scope = None

    text = render_session_context(
        scope=scope,
        identity_max_chars=args.max_chars,
        top_entities_window_days=args.window_days,
        top_entities_limit=args.top_entities,
        recent_memories_limit=args.recent,
        recent_memory_types=args.types,
        include_trends=args.include_trends,
    )
    # stdout (no stderr prefix) — CC pipes our stdout into additionalContext.
    print(text)
    return 0


def _build_router_for_args(args: argparse.Namespace, memory_root: Path) -> MirrorRouter:
    router = MirrorRouter()
    if args.codex:
        router.register(suffix=".md", handler=CodexRolloutHandler(memory_root))
    if args.openclaw:
        known = [Path(p) for p in (args.known_roots or [])]
        router.register(
            suffix=".jsonl",
            handler=OpenClawSessionHandler(memory_root, known_roots=known),
        )
    return router


def _watch_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    if args.codex:
        codex_dir = Path(args.codex_dir or (Path.home() / ".codex" / "memories" / "rollout_summaries"))
        codex_dir.mkdir(parents=True, exist_ok=True)
        paths.append(codex_dir)
    if args.openclaw:
        openclaw_root = Path(args.openclaw_dir or (Path.home() / ".openclaw" / "agents"))
        openclaw_root.mkdir(parents=True, exist_ok=True)
        paths.append(openclaw_root)
    return paths


def cmd_mirror(args: argparse.Namespace) -> int:
    if not args.codex and not args.openclaw:
        print("error: pass at least one of --codex / --openclaw", file=sys.stderr)
        return 2

    memory_root = _data_root()
    router = _build_router_for_args(args, memory_root)
    paths = _watch_paths(args)

    # First, scan existing files in target dirs once (catch-up pass)
    for watch_root in paths:
        for f in watch_root.rglob("*"):
            if f.is_file():
                router.dispatch(f)

    if args.once:
        return 0

    # Run watchdog observer until SIGINT
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    class _Adapter(FileSystemEventHandler):
        def __init__(self, router: MirrorRouter) -> None:
            self.router = router

        def on_created(self, event):
            if event.is_directory:
                return
            self.router.dispatch(Path(event.src_path))

        def on_modified(self, event):
            # Some apps write files in two steps; re-dispatch on modify too.
            if event.is_directory:
                return
            self.router.dispatch(Path(event.src_path))

    observer = Observer()
    adapter = _Adapter(router)
    for p in paths:
        observer.schedule(adapter, str(p), recursive=True)
    observer.start()
    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join(timeout=5)
    return 0


def cmd_rebuild_index(args: argparse.Namespace) -> int:
    from .index import rebuild_index
    result = rebuild_index(_data_root())
    print(
        f"rebuild-index: {result['indexed']} memories indexed "
        f"({result['errors']} errors)",
        file=sys.stderr,
    )
    return 0


def _cmd_swap_notify(args: argparse.Namespace) -> int:
    setup_mod.swap_codex_notify(
        to=args.to,
        codex_dir=Path(args.codex_dir),
        backup_dir=Path(args.backup_dir),
        probe_path=args.probe_path,
        wrapper_path=args.wrapper_path,
    )
    print(f"swap-codex-notify: notify swapped to {args.to}", file=sys.stderr)
    return 0


def _cmd_remove_stop_hook(args: argparse.Namespace) -> int:
    setup_mod.remove_codex_stop_hook(
        codex_dir=Path(args.codex_dir),
        backup_dir=Path(args.backup_dir),
    )
    print("remove-codex-stop-hook: ok", file=sys.stderr)
    return 0


def _cmd_install_launchd(args: argparse.Namespace) -> int:
    out = setup_mod.install_launchd_mirror(
        template_path=Path(args.template),
        launch_dir=Path(args.launch_dir),
        memoryd_bin=args.memoryd_bin,
        data_root=args.data_root,
    )
    print(f"install-launchd-mirror: rendered to {out}", file=sys.stderr)
    print(
        "next step: launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.memoryd.mirror.plist",
        file=sys.stderr,
    )
    return 0


def _cmd_uninstall_launchd(args: argparse.Namespace) -> int:
    deleted = setup_mod.uninstall_launchd_mirror(launch_dir=Path(args.launch_dir))
    print(f"uninstall-launchd-mirror: {'deleted' if deleted else 'not installed'}", file=sys.stderr)
    return 0


def _cmd_install_cron(args: argparse.Namespace) -> int:
    keys: list[str] = []
    # --task is the canonical knob; the old per-job flags stay as shortcuts.
    if getattr(args, "task", None):
        keys.append(args.task)
    if args.all or args.decay:
        keys.append("decay")
    if args.all or args.digest:
        keys.append("digest")
    if args.all or getattr(args, "weekly_identity", False):
        keys.append("weekly_identity")
    if args.all or getattr(args, "monthly_report", False):
        keys.append("monthly_report")
    # dedupe preserving order
    keys = list(dict.fromkeys(keys))
    if not keys:
        print(
            "install-cron: pass --decay / --digest / --weekly-identity / "
            "--monthly-report / --task=<name> / --all",
            file=sys.stderr,
        )
        return 2
    for k in keys:
        out = setup_mod.install_cron(k)
        print(f"installed {k}: {out}", file=sys.stderr)
    return 0


def _cmd_uninstall_cron(args: argparse.Namespace) -> int:
    keys: list[str] = []
    if getattr(args, "task", None):
        keys.append(args.task)
    if args.all or args.decay:
        keys.append("decay")
    if args.all or args.digest:
        keys.append("digest")
    if args.all or getattr(args, "weekly_identity", False):
        keys.append("weekly_identity")
    if args.all or getattr(args, "monthly_report", False):
        keys.append("monthly_report")
    keys = list(dict.fromkeys(keys))
    if not keys:
        print(
            "uninstall-cron: pass --decay / --digest / --weekly-identity / "
            "--monthly-report / --task=<name> / --all",
            file=sys.stderr,
        )
        return 2
    for k in keys:
        setup_mod.uninstall_cron(k)
        print(f"uninstalled {k}", file=sys.stderr)
    return 0


def _cmd_install_cc_hook(args: argparse.Namespace) -> int:
    out = setup_mod.install_cc_hook()
    print(f"wired CC SessionEnd hook in {out}", file=sys.stderr)
    if getattr(args, "include_session_start", False):
        try:
            ss = setup_mod.install_cc_session_start_hook()
            print(f"wired CC SessionStart hook in {ss}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — install is best-effort
            print(f"warn: SessionStart install failed: {exc}", file=sys.stderr)
    return 0


def _cmd_install_cc_session_start_hook(args: argparse.Namespace) -> int:
    out = setup_mod.install_cc_session_start_hook()
    print(f"wired CC SessionStart hook in {out}", file=sys.stderr)
    return 0


def _cmd_auto_install(args: argparse.Namespace) -> int:
    import json as _json
    res = setup_mod.auto_install()
    print(_json.dumps(res, indent=2))
    return 0


def _cmd_llm_test(args: argparse.Namespace) -> int:
    """Run a single round-trip against the configured LLM provider.

    Useful to verify "the wiring is end-to-end working" without needing to
    trigger a full capture/analyze flow. Prints provider, model, latency,
    and the assistant's literal reply.
    """
    import asyncio as _asyncio
    import time as _time
    from .config import load_config
    from .llm import LLMMessage, LLMUnavailable, get_llm

    cfg = load_config()
    provider_name = cfg.get("llm", {}).get("provider", "anthropic")
    model = cfg.get("llm", {}).get("model")

    print(f"provider: {provider_name}")
    print(f"model:    {model}")
    try:
        llm = get_llm(provider=provider_name, model=model)
    except LLMUnavailable as exc:
        print(f"FAIL: provider construction → {exc}")
        return 2

    t0 = _time.monotonic()
    try:
        reply = _asyncio.run(
            llm.generate([
                LLMMessage(role="system", content="Reply with exactly the word OK and nothing else."),
                LLMMessage(role="user", content="ping"),
            ])
        )
    except LLMUnavailable as exc:
        print(f"FAIL: generate() → {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: unexpected → {type(exc).__name__}: {exc}")
        return 2
    elapsed = _time.monotonic() - t0
    reply_one_line = " ".join(reply.split())[:200]
    print(f"latency:  {elapsed:.2f}s")
    print(f"reply:    {reply_one_line!r}")
    if "OK" in reply.upper():
        print("OK: provider returned the expected token.")
        return 0
    print("WARN: provider replied but did not include 'OK'; output may be noisy or model misbehaving.")
    return 1


def _cmd_backfill(args: argparse.Namespace) -> int:
    """Batch-run ``analyze-session`` on every session that has no promotions yet.

    Useful right after enabling an LLM provider: replays history so KG entities
    + DURA promotions get populated for sessions captured before LLM was wired.
    """
    import os as _os
    import subprocess as _sp
    import sys as _sys
    import time as _time
    from pathlib import Path as _Path
    from .index import open_index as _open_index

    data_root = _Path(
        _os.environ.get(
            "MEMORYD_DATA_ROOT",
            str(_Path.home() / ".local" / "share" / "memoryd"),
        )
    )
    idx = _open_index(data_root / "index.db")
    cur = idx.conn.execute(
        """
        SELECT m.slug
        FROM memories m
        WHERE m.type = 'session'
          AND NOT EXISTS (
              SELECT 1 FROM promotions p WHERE p.source_session_slug = m.slug
          )
        ORDER BY m.created_at DESC
        LIMIT ?
        """,
        (args.limit,),
    )
    slugs = [r[0] for r in cur.fetchall()]
    if not slugs:
        print("backfill: nothing to do (every session already analyzed)")
        return 0
    avg_sec = 8  # rough wall-clock per analyze (claude CLI spawn + LLM round-trip)
    est_min = max(1, (len(slugs) * avg_sec + 59) // 60)
    print(f"backfill: {len(slugs)} session(s) pending — ETA ~{est_min} 分钟（每条 ~{avg_sec}s）")
    if args.dry_run:
        for s in slugs:
            print(f"  [dry] {s}")
        return 0
    memoryd_bin = _sys.argv[0]
    t_start = _time.monotonic()
    succeeded = failed = 0
    for i, slug in enumerate(slugs, 1):
        t0 = _time.monotonic()
        try:
            r = _sp.run(
                [memoryd_bin, "analyze-session", slug],
                capture_output=True, text=True, timeout=240,
            )
            tail = (r.stdout or r.stderr or "").strip().split("\n")[-1][:60]
            if r.returncode == 0:
                succeeded += 1
                status = "✓"
            else:
                failed += 1
                status = f"✗ rc={r.returncode}"
        except _sp.TimeoutExpired:
            failed += 1
            status = "✗ TIMEOUT(>240s)"
            tail = ""
        except Exception as e:  # noqa: BLE001
            failed += 1
            status = "✗"
            tail = f"ERROR: {e}"[:60]
        dt = _time.monotonic() - t0
        elapsed = _time.monotonic() - t_start
        remaining = max(0, len(slugs) - i)
        avg_per = elapsed / i
        eta_sec = int(remaining * avg_per)
        eta = f"剩 {eta_sec//60}m{eta_sec%60:02d}s" if remaining else "完成"
        print(f"  [{i:3d}/{len(slugs)}] {dt:4.1f}s {status:14s} {slug[:48]:<48s} {tail:<40s} {eta}")
    total = _time.monotonic() - t_start
    print(f"backfill: 完成 — {succeeded} 成功, {failed} 失败, 总耗时 {int(total//60)}m{int(total%60):02d}s")
    print("跑 `memoryd kg entities --top=30` 看抽出的实体；`memoryd profile show` 看画像。")
    return 0 if failed == 0 else 1
    print(f"backfill: done. Run `memoryd kg entities --top=30` to see what got extracted.")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Run every health check and print a one-shot status report.

    Exit code maps to the worst non-info status: 0 = ok, 1 = warn, 2 = fail.
    Scripts can `--json` for machine-readable output.
    """
    from . import doctor as doctor_mod

    results = doctor_mod.run_all_checks()
    if args.json:
        print(doctor_mod.format_json(results))
    else:
        print(doctor_mod.format_human(results, quiet=args.quiet))
    overall = doctor_mod.overall_status(results)
    if overall == "fail":
        return 2
    if overall == "warn":
        return 1
    return 0


def _cmd_uninstall_all(args: argparse.Namespace) -> int:
    """Reverse everything `setup auto-install` did.

    Removes:
      * 4 cron LaunchAgents (decay / digest / weekly_identity / monthly_report)
      * launchd mirror
      * CC SessionStart + SessionEnd hooks from ~/.claude/settings.json
      * Codex notify wrapper (restore original via swap --to original)
      * Codex AGENTS.md memoryd block
      * MCP server registration in ~/.claude.json (memoryd entry)

    Does NOT touch:
      * The data dir ~/.local/share/memoryd/  (memories stay; delete by hand)
      * ~/.config/memoryd/config.toml (settings stay)
      * The cloned repo or .venv

    Idempotent: missing items skipped silently.
    """
    import json as _json
    results: dict = {}

    # --- cron jobs ---
    from .setup_cron import _JOBS, uninstall as _uninstall_cron
    for key in list(_JOBS):
        try:
            _uninstall_cron(key)
            results[f"{key}_cron"] = "uninstalled"
        except Exception as exc:  # noqa: BLE001
            results[f"{key}_cron_error"] = str(exc)

    # --- launchd mirror ---
    try:
        from .setup import uninstall_launchd_mirror
        removed = uninstall_launchd_mirror(launch_dir=Path.home() / "Library" / "LaunchAgents")
        results["launchd_mirror"] = "removed" if removed else "absent"
    except Exception as exc:  # noqa: BLE001
        results["launchd_mirror_error"] = str(exc)

    # --- CC hooks ---
    try:
        cc_settings = Path.home() / ".claude" / "settings.json"
        if cc_settings.exists():
            data = _json.loads(cc_settings.read_text(encoding="utf-8"))
            hooks = data.get("hooks") or {}
            for event in ("SessionEnd", "SessionStart"):
                if event in hooks:
                    hooks[event] = [
                        m for m in hooks[event]
                        if not any(
                            "memoryd" in (h.get("command") or "") or
                            "plugins/claude-code/session" in (h.get("command") or "")
                            for h in (m.get("hooks") or [])
                        )
                    ]
                    if not hooks[event]:
                        del hooks[event]
            if hooks:
                data["hooks"] = hooks
            else:
                data.pop("hooks", None)
            cc_settings.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            results["cc_hooks"] = "removed memoryd hook entries"
        else:
            results["cc_hooks_skipped"] = "~/.claude/settings.json absent"
    except Exception as exc:  # noqa: BLE001
        results["cc_hooks_error"] = str(exc)

    # --- Codex notify (restore original) ---
    try:
        from .setup import swap_codex_notify
        codex_dir = Path.home() / ".codex"
        if (codex_dir / "config.toml").exists() and (codex_dir / ".memoryd-notify-state.json").exists():
            swap_codex_notify(
                to="original",
                codex_dir=codex_dir,
                backup_dir=Path.home() / ".claude" / "backups",
                probe_path="",
                wrapper_path="",
            )
            results["codex_notify"] = "restored original"
        else:
            results["codex_notify_skipped"] = "no swap-state to restore"
    except Exception as exc:  # noqa: BLE001
        results["codex_notify_error"] = str(exc)

    # --- Codex AGENTS.md memoryd block ---
    try:
        from .codex_agents import uninstall_codex_agents_include
        removed = uninstall_codex_agents_include()
        results["codex_agents_include"] = "removed" if removed else "absent"
    except Exception as exc:  # noqa: BLE001
        results["codex_agents_include_error"] = str(exc)

    # --- MCP server registration ---
    try:
        cc_json = Path.home() / ".claude.json"
        if cc_json.exists():
            data = _json.loads(cc_json.read_text(encoding="utf-8"))
            servers = data.get("mcpServers") or {}
            if "memoryd" in servers:
                del servers["memoryd"]
                cc_json.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                results["mcp_registration"] = "removed"
            else:
                results["mcp_registration_skipped"] = "no memoryd MCP entry"
        else:
            results["mcp_registration_skipped"] = "~/.claude.json absent"
    except Exception as exc:  # noqa: BLE001
        results["mcp_registration_error"] = str(exc)

    print(_json.dumps(results, indent=2, ensure_ascii=False))
    return 0


def _cmd_install_codex_agents_include(args: argparse.Namespace) -> int:
    """Refresh ~/.codex/AGENTS.md memoryd block — Codex now sees identity."""
    from .codex_agents import install_codex_agents_include
    try:
        out = install_codex_agents_include()
    except Exception as exc:  # noqa: BLE001
        print(f"install-codex-agents-include: {exc}", file=sys.stderr)
        return 1
    print(f"refreshed memoryd block in {out}", file=sys.stderr)
    return 0


def _cmd_uninstall_codex_agents_include(args: argparse.Namespace) -> int:
    """Strip the memoryd block from ~/.codex/AGENTS.md."""
    from .codex_agents import uninstall_codex_agents_include
    removed = uninstall_codex_agents_include()
    print(
        "removed memoryd block from ~/.codex/AGENTS.md" if removed
        else "no memoryd block found (already clean)",
        file=sys.stderr,
    )
    return 0


def _cmd_install_memory_searcher(args: argparse.Namespace) -> int:
    try:
        out = setup_mod.install_memory_searcher(
            target_dir=args.target, force=args.force
        )
    except FileExistsError as e:
        print(str(e), file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"install-memory-searcher: {e}", file=sys.stderr)
        return 1
    print(f"installed memory-searcher: {out}", file=sys.stderr)
    return 0


def cmd_decay_sweep(args: argparse.Namespace) -> int:
    from .governance.decay import sweep_decay
    counts = sweep_decay(_data_root())
    print(
        f"decay-sweep: to_dim={counts['to_dim']} "
        f"to_soft_forgotten={counts['to_soft_forgotten']} "
        f"to_forgotten_dir={counts['to_forgotten_dir']}",
        file=sys.stderr,
    )
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    from .governance.merge import merge_memories
    merge_memories(_data_root(), keep_slug=args.keep, drop_slugs=args.drop)
    print(f"merge: keep={args.keep} drop={','.join(args.drop)}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Plan 9: manual control CLI — search / list / show / delete / promote
# ---------------------------------------------------------------------------


def _iter_scopes(data_root: Path) -> list[str]:
    """List scope_hash directories under <data_root>/scopes/ (skip _* internals)."""
    scopes_dir = data_root / "scopes"
    if not scopes_dir.exists():
        return []
    return [
        d.name for d in scopes_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    ]


def cmd_search(args: argparse.Namespace) -> int:
    """Full-text search across memories; aggregates across scopes by default."""
    from .search import search_sessions
    data_root = _data_root()
    target_scopes = [args.scope] if args.scope else _iter_scopes(data_root)
    hits: list[dict[str, Any]] = []
    for sh in target_scopes:
        try:
            scope_hits = search_sessions(
                data_root, sh, args.query,
                type_=args.type_, limit=args.limit,
            )
        except Exception:
            # SQLite missing / migration error → skip this scope
            continue
        for h in scope_hits:
            hits.append({
                "slug": h.slug,
                "title": h.title,
                "scope_hash": sh,
                "excerpt": h.excerpt,
                "path": str(h.path),
            })
    # Apply final limit across aggregated hits
    hits = hits[: args.limit]
    if args.as_json:
        print(json.dumps(hits, indent=2, ensure_ascii=False, default=str))
    else:
        if not hits:
            print("no hits", file=sys.stderr)
            return 0
        for h in hits:
            slug = h.get("slug", "?")
            scope = h.get("scope_hash", "?")
            excerpt = h.get("excerpt", "") or ""
            print(f"{slug:40} {scope:14} {excerpt[:80]}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List memories filtered by type / scope."""
    data_root = _data_root()
    rows = _list_memories(
        data_root,
        type_=args.type_,
        scope_hash=args.scope,
        limit=args.limit,
    )
    if args.as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
    else:
        if not rows:
            print("no memories", file=sys.stderr)
            return 0
        for r in rows:
            print(
                f"{r.get('slug',''):40} "
                f"[{r.get('type',''):10}] "
                f"{r.get('scope_hash',''):14} "
                f"{r.get('created_at','') or ''}"
            )
    return 0


def _list_memories(
    data_root: Path,
    *,
    type_: str | None = None,
    scope_hash: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query SQLite memories table; fallback to filesystem scan if table missing."""
    import sqlite3
    db = data_root / "index.db"
    if not db.exists():
        return _scan_filesystem_memories(data_root, type_, scope_hash, limit)
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        q = (
            "SELECT slug, type, scope_hash, ttl_days, last_recalled_at, "
            "recall_count, body_path, created_at "
            "FROM memories WHERE 1=1"
        )
        params: list[Any] = []
        if type_:
            q += " AND type = ?"
            params.append(type_)
        if scope_hash:
            q += " AND scope_hash = ?"
            params.append(scope_hash)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
    except sqlite3.OperationalError:
        # memories table missing or column mismatch → fallback
        conn.close()
        return _scan_filesystem_memories(data_root, type_, scope_hash, limit)
    else:
        conn.close()
    out = [dict(r) for r in rows]
    if not out:
        # SQLite present but empty → still try filesystem (e.g. tests writing raw .md)
        return _scan_filesystem_memories(data_root, type_, scope_hash, limit)
    return out


def _scan_filesystem_memories(
    data_root: Path,
    type_: str | None,
    scope_hash: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Fallback: walk scopes/<hash>/<type_dir>/*.md when SQLite missing/empty."""
    type_dir_map = {
        "sessions": "session",
        "decisions": "decision",
        "preferences": "preference",
        "facts": "fact",
        "playbooks": "playbook",
        "warnings": "warning",
    }
    out: list[dict[str, Any]] = []
    scopes = data_root / "scopes"
    if not scopes.exists():
        return out
    for sh_dir in scopes.iterdir():
        if not sh_dir.is_dir() or sh_dir.name.startswith("_"):
            continue
        sh = sh_dir.name
        if scope_hash and sh != scope_hash:
            continue
        for type_dir in sh_dir.iterdir():
            if not type_dir.is_dir():
                continue
            t = type_dir_map.get(type_dir.name)
            if t is None:
                continue
            if type_ and t != type_:
                continue
            for md in type_dir.glob("*.md"):
                out.append({
                    "slug": md.stem,
                    "type": t,
                    "scope_hash": sh,
                    "body_path": str(md.relative_to(data_root)),
                    "created_at": None,
                })
            for enc_path in type_dir.glob("*.md.enc"):
                out.append({
                    "slug": enc_path.name[:-len(".md.enc")],
                    "type": t,
                    "scope_hash": sh,
                    "body_path": str(enc_path.relative_to(data_root)),
                    "created_at": None,
                })
    out.sort(key=lambda r: r["slug"], reverse=True)
    return out[:limit]


def cmd_show(args: argparse.Namespace) -> int:
    """Display a single memory (frontmatter + body)."""
    data_root = _data_root()
    path = _resolve_slug(data_root, args.slug, scope_hash=args.scope)
    if path is None:
        print(f"slug not found: {args.slug}", file=sys.stderr)
        return 1
    # Derive scope_hash from path for gate check
    try:
        sh = path.relative_to(data_root / "scopes").parts[0]
    except (IndexError, ValueError):
        sh = None
    if sh is not None:
        from .governance import gate
        try:
            gate.check_or_raise(sh, "memoryd show", memory_root=data_root)
        except gate.AuthorizationRequired as e:
            print(f"AUTHORIZATION_REQUIRED: {e}", file=sys.stderr)
            print(
                "Run `memoryd grant <scope_path> --duration session` first.",
                file=sys.stderr,
            )
            return 1
    if path.name.endswith(".md.enc"):
        if sh is None:
            print(f"cannot derive scope_hash for encrypted file {path}",
                  file=sys.stderr)
            return 1
        text = _enc.decrypt_bytes(sh, path.read_bytes()).decode("utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    print(text)
    return 0


def _resolve_slug(
    data_root: Path,
    slug: str,
    *,
    scope_hash: str | None = None,
) -> Path | None:
    """Find the .md / .md.enc path for *slug*.

    If *scope_hash* given, only search that scope. Otherwise iterate non-internal
    scopes. Prefers plain `.md` over `.md.enc` when both exist.
    """
    scopes = data_root / "scopes"
    if not scopes.exists():
        return None
    if scope_hash:
        roots = [scopes / scope_hash]
    else:
        roots = [
            d for d in scopes.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        ]
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob(f"{slug}.md"):
            candidates.append(p)
        for p in root.rglob(f"{slug}.md.enc"):
            candidates.append(p)
    if not candidates:
        return None
    # Prefer plain .md over .md.enc
    for c in candidates:
        if c.suffix == ".md":
            return c
    return candidates[0]


def cmd_delete(args: argparse.Namespace) -> int:
    """Delete a memory: unlink the .md/.md.enc and drop SQLite rows.

    Sensitive scopes must pass through `gate.check_or_raise` before deletion,
    so we don't silently nuke a user's encrypted memory.
    """
    data_root = _data_root()
    path = _resolve_slug(data_root, args.slug, scope_hash=args.scope)
    if path is None:
        print(f"slug not found: {args.slug}", file=sys.stderr)
        return 1
    # Derive scope_hash from path for gate check
    try:
        sh = path.relative_to(data_root / "scopes").parts[0]
    except (IndexError, ValueError):
        sh = None
    if sh is not None:
        from .governance import gate
        try:
            gate.check_or_raise(sh, "memoryd delete", memory_root=data_root)
        except gate.AuthorizationRequired as e:
            print(f"AUTHORIZATION_REQUIRED: {e}", file=sys.stderr)
            print(
                "Run `memoryd grant <scope_path> --duration session` first.",
                file=sys.stderr,
            )
            return 1
    if not args.force:
        try:
            ans = input(f"delete {args.slug}? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("aborted", file=sys.stderr)
            return 1
        if ans not in ("y", "yes"):
            print("aborted", file=sys.stderr)
            return 1
    # Unlink the file (.md or .md.enc)
    try:
        path.unlink()
    except OSError as e:
        print(f"failed to unlink {path}: {e}", file=sys.stderr)
        return 1
    # Drop SQLite rows (memories cascades to triggers via FK)
    db = data_root / "index.db"
    if db.exists():
        import sqlite3
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM memories WHERE slug = ?", (args.slug,))
            # Defense in depth: explicitly drop triggers in case FK not on.
            conn.execute("DELETE FROM triggers WHERE slug = ?", (args.slug,))
            conn.commit()
        finally:
            conn.close()
    print(f"deleted {args.slug}", file=sys.stderr)
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    """Promote pending promotions: single id, batch --all, or filtered by score.

    Single:   memoryd promote <id>
    Batch:    memoryd promote --all              # approve every pending row
    Filtered: memoryd promote --auto-high        # approve only DURA >= 0.85 (4 dims avg)
    Preview:  memoryd promote --all --dry-run
    """
    from .governance.analyze import approve_promotion, list_pending_promotions

    data_root = _data_root()

    # Batch / filter modes
    if getattr(args, "all", False) or getattr(args, "auto_high", False):
        pending = list_pending_promotions(data_root)
        if getattr(args, "auto_high", False):
            def _avg(p: dict) -> float:
                import json as _json
                try:
                    dura = _json.loads(p.get("dura_score") or "{}")
                except Exception:  # noqa: BLE001
                    return 0.0
                vals = [v for v in dura.values() if isinstance(v, (int, float))]
                return sum(vals) / len(vals) if vals else 0.0
            pending = [p for p in pending if _avg(p) >= 0.85]
        if not pending:
            print("promote: 没有待审批的 promotion（all 模式）", file=sys.stderr)
            return 0
        if getattr(args, "dry_run", False):
            print(f"promote --dry-run: 将批准 {len(pending)} 条", file=sys.stderr)
            for p in pending[:20]:
                print(f"  #{p['id']:>4} [{p.get('proposed_type','?')}] {p.get('proposed_title','')[:60]}",
                      file=sys.stderr)
            if len(pending) > 20:
                print(f"  ... 还有 {len(pending) - 20} 条", file=sys.stderr)
            return 0
        ok = err = 0
        for p in pending:
            try:
                approve_promotion(data_root, int(p["id"]))
                ok += 1
            except Exception as e:  # noqa: BLE001 - best-effort batch
                err += 1
                print(f"  ✗ #{p['id']}: {e}", file=sys.stderr)
        print(f"promote: 已批准 {ok} 条" + (f"，失败 {err} 条" if err else ""),
              file=sys.stderr)
        return 0 if err == 0 else 1

    # Single id mode (legacy)
    if args.promotion_id is None:
        print(
            "error: promote 需要 <id>，或用 --all / --auto-high",
            file=sys.stderr,
        )
        return 2
    if getattr(args, "dry_run", False):
        # Preview single-id: look up the row and print, do NOT mutate.
        from .index import open_index as _open_idx
        idx = _open_idx(data_root / "index.db")
        try:
            row = idx.conn.execute(
                "SELECT id, proposed_type, proposed_title, status "
                "FROM promotions WHERE id = ?",
                (args.promotion_id,),
            ).fetchone()
        finally:
            idx.close()
        if row is None:
            print(f"error: promotion #{args.promotion_id} not found", file=sys.stderr)
            return 1
        print(
            f"promote --dry-run: 将批准 #{row[0]} [{row[1]}] {row[2][:80]} (当前 status={row[3]})",
            file=sys.stderr,
        )
        return 0
    try:
        path = approve_promotion(data_root, args.promotion_id)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if path is None:
        print(
            f"promotion #{args.promotion_id} approved (no body to write)",
            file=sys.stderr,
        )
    else:
        print(f"promoted -> {path}", file=sys.stderr)
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    if getattr(args, "tui", False):
        from .tui.digest import run_tui
        return run_tui(_data_root())
    from .governance.digest import build_digest, render_digest_text
    d = build_digest(_data_root())
    if args.json:
        import json as _j
        print(_j.dumps(d, indent=2, ensure_ascii=False))
    else:
        print(render_digest_text(d))
    if getattr(args, "notify", False):
        from .notify import notify
        from .config import load_config
        cfg = load_config()
        n_promos = len(d.get("promotions") or [])
        n_dups = len(d.get("duplicates") or [])
        n_decay = len(d.get("decayed") or [])
        total = n_promos + n_dups + n_decay
        body = (
            f"{total} pending items "
            f"({n_promos} promotions / {n_dups} duplicates / {n_decay} decayed) "
            "— run 'memoryd digest' to review"
        )
        smtp = cfg.notify.smtp if hasattr(cfg, "notify") else None
        notify("memoryd weekly digest ready", body, smtp)
    return 0


def cmd_mark_sensitive(args: argparse.Namespace) -> int:
    """mark-sensitive <scope_path>: encrypt existing .md files and register scope."""
    scope_root = resolve_scope_root(Path(args.scope_path))
    sh = scope_hash(scope_root)
    memory_root = _data_root()

    # 1. Write .memoryd-sensitive marker (raises ValueError if parent already marked)
    try:
        _scope_meta.mark_sensitive(scope_root)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # 2. Get-or-create AES key in Keychain
    _enc.get_or_create_scope_key(sh)

    # 3. Register in SQLite sensitive_scopes table
    idx = _open_idx(memory_root / "index.db")
    try:
        # Idempotent: register_sensitive_scope uses INSERT OR REPLACE
        idx.register_sensitive_scope(sh, str(scope_root))

        # 4. Encrypt existing .md files in <root>/scopes/<hash>/**
        scope_dir = memory_root / "scopes" / sh
        encrypted = 0
        if scope_dir.exists():
            for md_path in sorted(scope_dir.rglob("*.md")):
                plaintext = md_path.read_bytes()
                blob = _enc.encrypt_bytes(sh, plaintext)
                enc_path = md_path.with_suffix(".md.enc")
                enc_path.write_bytes(blob)
                # Update SQLite body_path for this file
                old_rel = str(md_path.relative_to(memory_root))
                new_rel = str(enc_path.relative_to(memory_root))
                idx.conn.execute(
                    "UPDATE memories SET body_path = ? WHERE body_path = ?",
                    (new_rel, old_rel),
                )
                md_path.unlink()
                encrypted += 1

        # 5. UPDATE scope_sensitive=1 for all memories in this scope
        idx.conn.execute(
            "UPDATE memories SET scope_sensitive = 1 WHERE scope_hash = ?",
            (sh,),
        )
        idx.conn.commit()
    finally:
        idx.close()

    print(
        f"mark-sensitive: scope={scope_root} hash={sh} files_encrypted={encrypted}",
        file=sys.stderr,
    )
    return 0


def cmd_unmark_sensitive(args: argparse.Namespace) -> int:
    """unmark-sensitive <scope_path>: decrypt .md.enc files and remove registration."""
    scope_root = resolve_scope_root(Path(args.scope_path))
    sh = scope_hash(scope_root)
    memory_root = _data_root()

    # 1. Decrypt existing .md.enc files
    idx = _open_idx(memory_root / "index.db")
    try:
        scope_dir = memory_root / "scopes" / sh
        decrypted = 0
        if scope_dir.exists():
            for enc_path in sorted(scope_dir.rglob("*.md.enc")):
                blob = enc_path.read_bytes()
                plaintext = _enc.decrypt_bytes(sh, blob)
                md_path = enc_path.with_name(enc_path.name[:-4])  # strip .enc
                md_path.write_bytes(plaintext)
                # Update SQLite body_path
                old_rel = str(enc_path.relative_to(memory_root))
                new_rel = str(md_path.relative_to(memory_root))
                idx.conn.execute(
                    "UPDATE memories SET body_path = ? WHERE body_path = ?",
                    (new_rel, old_rel),
                )
                enc_path.unlink()
                decrypted += 1

        # 2. UPDATE scope_sensitive=0
        idx.conn.execute(
            "UPDATE memories SET scope_sensitive = 0 WHERE scope_hash = ?",
            (sh,),
        )
        idx.conn.commit()

        # 3. Unregister from sensitive_scopes
        idx.unregister_sensitive_scope(sh)
    finally:
        idx.close()

    # 4. Delete Keychain key (best-effort)
    _enc.delete_scope_key(sh)

    # 5. Remove .memoryd-sensitive marker
    _scope_meta.unmark_sensitive(scope_root)

    print(
        f"unmark-sensitive: scope={scope_root} hash={sh} files_decrypted={decrypted}",
        file=sys.stderr,
    )
    return 0


def cmd_grant(args: argparse.Namespace) -> int:
    from .governance.grants import write_grant
    scope_root = resolve_scope_root(Path(args.scope_path))
    sh = scope_hash(scope_root)
    g = write_grant(sh, str(scope_root), args.duration, task_id=args.task)
    print(f"grant: scope={scope_root} duration={args.duration} expires={g['expires_at']}", file=sys.stderr)
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    from .governance.grants import revoke_grant
    sh = scope_hash(resolve_scope_root(Path(args.scope_path)))
    deleted = revoke_grant(sh, task_id=args.task)
    print(f"revoke: {'ok' if deleted else 'no-op'}", file=sys.stderr)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    from datetime import datetime
    from .governance.audit import audit_log_path, query_events, verify_chain

    # --verify: walk the audit chain and report tampering instead of dumping
    # events. Exit code 0 = chain intact, 1 = broken.
    if getattr(args, "verify", False):
        valid, broken_at = verify_chain()
        path = audit_log_path()
        if getattr(args, "json", False):
            import json as _j
            print(_j.dumps(
                {
                    "valid": valid,
                    "first_broken_line": broken_at,
                    "audit_log_path": str(path),
                },
                indent=2,
                ensure_ascii=False,
            ))
        else:
            if valid:
                print(f"audit verify: OK  ({path})", file=sys.stderr)
            else:
                print(
                    f"audit verify: BROKEN at line {broken_at}  ({path})",
                    file=sys.stderr,
                )
        return 0 if valid else 1

    since = None
    if args.since:
        since = datetime.fromisoformat(args.since)
    events = query_events(
        scope_hash=args.scope,
        since=since,
        event_type=args.event_type,
    )
    if args.json:
        import json as _j
        print(_j.dumps(events, indent=2, ensure_ascii=False))
    else:
        # text table
        print(f"{'ts':<28} {'event_type':<24} {'scope':<14} {'tool':<24} {'result':<10}")
        print("-" * 100)
        for e in events:
            print(
                f"{e.get('ts','')[:28]:<28} "
                f"{e.get('event_type','')[:24]:<24} "
                f"{(e.get('scope_hash') or '')[:14]:<14} "
                f"{(e.get('tool') or '')[:24]:<24} "
                f"{e.get('result','')[:10]:<10}"
            )
    return 0


def _cmd_sync_export(args: argparse.Namespace) -> int:
    from .sync import expand_sync_dir, export
    from .config import load_config
    cfg = load_config()
    # --auto: triggered by SessionEnd hook. Silently no-op unless user enabled
    # both sync.enabled and sync.auto_export_on_session_end.
    if getattr(args, "auto", False):
        if not (cfg.sync.enabled and cfg.sync.auto_export_on_session_end):
            return 0
    if not cfg.sync.dir:
        if getattr(args, "auto", False):
            return 0  # auto mode never errors out
        print(
            "sync.dir 未配置；编辑 ~/.config/memoryd/config.toml [sync] dir=...",
            file=sys.stderr,
        )
        return 2
    sync_dir = expand_sync_dir(cfg.sync.dir)
    report = export(
        _data_root(),
        sync_dir,
        scope_hash=args.scope,
        dry_run=args.dry_run,
    )
    print(
        f"export: copied={report.copied} skipped={report.skipped} "
        f"dry_run={report.dry_run}",
        file=sys.stderr,
    )
    return 0


def _cmd_sync_import(args: argparse.Namespace) -> int:
    from .sync import expand_sync_dir, import_
    from .config import load_config
    cfg = load_config()
    # --auto: triggered pre-capture. Silently no-op unless user enabled both
    # sync.enabled and sync.auto_import_on_session_start.
    if getattr(args, "auto", False):
        if not (cfg.sync.enabled and cfg.sync.auto_import_on_session_start):
            return 0
    if not cfg.sync.dir:
        if getattr(args, "auto", False):
            return 0
        print("sync.dir 未配置", file=sys.stderr)
        return 2
    sync_dir = expand_sync_dir(cfg.sync.dir)
    report = import_(
        _data_root(),
        sync_dir,
        scope_hash=args.scope,
        dry_run=args.dry_run,
    )
    print(
        f"import: copied={report.copied} skipped={report.skipped} "
        f"conflicts={report.conflicts} dry_run={report.dry_run}",
        file=sys.stderr,
    )
    return 0


def _cmd_sync_status(args: argparse.Namespace) -> int:
    from .sync import expand_sync_dir, status
    from .config import load_config
    cfg = load_config()
    if not cfg.sync.dir:
        print("sync.dir 未配置", file=sys.stderr)
        return 2
    sync_dir = expand_sync_dir(cfg.sync.dir)
    result = status(_data_root(), sync_dir)
    if getattr(args, "as_json", False):
        import json as _j
        print(_j.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"sync dir:   {result['sync_dir']}")
        print(f"state entries: {result['state_entries']}")
        for h, counts in sorted(result["per_scope"].items()):
            sym = "ok" if counts["local"] == counts["sync"] else "!!"
            print(
                f"  {h}  local {counts['local']} / sync {counts['sync']}  {sym}"
            )
        print(f"_conflicts: {result['conflicts']}")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    from .scope import resolve_scope_root, scope_hash as _scope_hash
    scope = args.scope or _scope_hash(resolve_scope_root(Path.cwd()))
    kind = args.import_kind
    if kind == "claude-md":
        from .importers import claude_md as mod
    elif kind == "auto-memory":
        from .importers import auto_memory as mod
    elif kind == "agents-md":
        from .importers import agents_md as mod
    elif kind == "mcp-memory-service":
        from .importers import mcp_mem as mod
    else:
        print(f"unknown import kind: {kind}", file=sys.stderr)
        return 2
    report = mod.run(
        args.path,
        _data_root(),
        scope,
        dry_run=args.dry_run,
        force=args.force,
        source_tag=args.source_tag,
    )
    import json as _json
    print(_json.dumps({
        "kind": kind,
        "path": str(args.path),
        "scope_hash": scope,
        "parsed": report.parsed,
        "written": report.written,
        "skipped": report.skipped,
        "by_type": report.by_type,
        "dry_run": report.dry_run,
    }, indent=2, ensure_ascii=False))
    return 0


def _cmd_set_passphrase(args: argparse.Namespace) -> int:
    import getpass
    from . import passphrase
    p1 = getpass.getpass("Master passphrase: ")
    p2 = getpass.getpass("Confirm: ")
    if p1 != p2:
        print("passphrase mismatch", file=sys.stderr)
        return 1
    try:
        passphrase.set_(p1)
    except passphrase.PassphraseError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"failed to store passphrase: {e}", file=sys.stderr)
        return 1
    print("master passphrase stored locally", file=sys.stderr)
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    if args.config_action == "show":
        import json as _j
        print(_j.dumps(config_mod.show_config(), indent=2, ensure_ascii=False))
    elif args.config_action == "set":
        # try to coerce value to int/float/bool/str
        v: object = args.value
        try:
            v = int(args.value)
        except ValueError:
            try:
                v = float(args.value)
            except ValueError:
                if args.value.lower() in ("true", "false"):
                    v = args.value.lower() == "true"
        config_mod.set_config_key(args.key, v)
        print(f"set {args.key} = {v!r}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Plan 10: knowledge_graph + profile self-learning subcommands
# ---------------------------------------------------------------------------


def _open_kg_store():
    """Open the shared index DB and wrap it in a KnowledgeGraphStore.

    Imports are local so users who didn't install networkx etc. (or who never
    touch `memoryd kg`) don't pay the import cost just to run `memoryd list`.
    """
    from .index import open_index
    from .knowledge_graph import KnowledgeGraphStore

    idx = open_index(_data_root() / "index.db")
    return idx, KnowledgeGraphStore(idx.conn)


def _open_profile_store():
    """Open the shared index DB and wrap it in a ProfileStore."""
    from .index import open_index
    from .profile import ProfileStore

    idx = open_index(_data_root() / "index.db")
    return idx, ProfileStore(idx.conn)


def _resolve_entity_id(store, name_or_id: str) -> str | None:
    """Treat the CLI arg as an entity id if it looks like one, else search by name."""
    if name_or_id.startswith("entity:"):
        return name_or_id
    matches = store.find_entities_by_name(name_or_id, fuzzy=True)
    if not matches:
        return None
    return matches[0].id


def _cmd_kg_entities(args: argparse.Namespace) -> int:
    idx, store = _open_kg_store()
    try:
        if args.type_:
            # `top_entities` doesn't take a type filter, so for --type queries
            # we use list_entities + cap.
            ents = store.list_entities(type=args.type_, scope_hash=args.scope)
            ents = ents[: args.top]
        else:
            ents = store.top_entities(
                scope_hash=args.scope,
                window_days=args.window_days,
                top_k=args.top,
            )
    finally:
        idx.close()

    if args.as_json:
        rows = [
            {
                "id": e.id,
                "name": e.name,
                "type": e.type,
                "mention_count": e.mention_count,
                "decay_state": e.decay_state,
                "scope_hash": e.scope_hash,
                "last_seen_at": e.last_seen_at.isoformat() if e.last_seen_at else None,
            }
            for e in ents
        ]
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if not ents:
        print("no entities", file=sys.stderr)
        return 0
    print(f"{'name':<32} {'type':<14} {'×count':<8} {'state':<8} {'scope':<14}")
    print("-" * 78)
    for e in ents:
        print(
            f"{e.name[:32]:<32} "
            f"{e.type[:14]:<14} "
            f"{e.mention_count:<8} "
            f"{e.decay_state[:8]:<8} "
            f"{(e.scope_hash or '')[:14]:<14}"
        )
    return 0


def _cmd_kg_memories_about(args: argparse.Namespace) -> int:
    from .knowledge_graph import memories_about_entity

    idx, store = _open_kg_store()
    try:
        eid = _resolve_entity_id(store, args.entity)
        if eid is None:
            print(f"entity not found: {args.entity}", file=sys.stderr)
            return 1
        types = None
        if args.types:
            types = [t.strip() for t in args.types.split(",") if t.strip()]
        slugs = memories_about_entity(store, eid, types=types)
    finally:
        idx.close()

    if args.as_json:
        print(json.dumps({"entity_id": eid, "slugs": slugs}, indent=2, ensure_ascii=False))
        return 0
    if not slugs:
        print(f"no memories mention {eid}", file=sys.stderr)
        return 0
    print(f"entity: {eid}")
    for s in slugs:
        print(f"  {s}")
    return 0


def _cmd_kg_evolution(args: argparse.Namespace) -> int:
    from .knowledge_graph import evolution_chain

    idx, store = _open_kg_store()
    try:
        eid = _resolve_entity_id(store, args.entity)
        if eid is None:
            print(f"entity not found: {args.entity}", file=sys.stderr)
            return 1
        chain = evolution_chain(store, eid)
    finally:
        idx.close()

    if args.as_json:
        print(json.dumps({"entity_id": eid, "chain": chain}, indent=2, ensure_ascii=False))
        return 0
    if not chain:
        print(f"no supersede chain for {eid}", file=sys.stderr)
        return 0
    print(f"evolution chain for {eid} (old → new):")
    for i, mem_id in enumerate(chain):
        print(f"  {i+1}. {mem_id}")
    return 0


def _cmd_kg_subgraph(args: argparse.Namespace) -> int:
    from .knowledge_graph import n_hop_subgraph, to_cytoscape_elements

    idx, store = _open_kg_store()
    try:
        eid = _resolve_entity_id(store, args.entity)
        if eid is None:
            print(f"entity not found: {args.entity}", file=sys.stderr)
            return 1
        g = n_hop_subgraph(store, eid, depth=args.depth)
        if args.format == "cytoscape":
            payload = to_cytoscape_elements(g)
        else:
            payload = {
                "entity_id": eid,
                "depth": args.depth,
                "nodes": [
                    {"id": n, **dict(a)} for n, a in g.nodes(data=True)
                ],
                "edges": [
                    {"source": s, "target": t, **dict(a)}
                    for s, t, a in g.edges(data=True)
                ],
            }
    finally:
        idx.close()

    blob = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(blob, encoding="utf-8")
        print(f"wrote subgraph → {out}", file=sys.stderr)
    else:
        print(blob)
    return 0


def _cmd_kg_conflicts(args: argparse.Namespace) -> int:
    from .knowledge_graph import find_conflicts

    idx, store = _open_kg_store()
    try:
        pairs = find_conflicts(store, scope_hash=args.scope)
    finally:
        idx.close()

    if args.as_json:
        print(json.dumps(
            [{"mem_a": a, "mem_b": b} for a, b in pairs],
            indent=2,
            ensure_ascii=False,
        ))
        return 0
    if not pairs:
        print("no conflict candidates", file=sys.stderr)
        return 0
    print(f"{'memory A':<40} {'memory B':<40}")
    print("-" * 82)
    for a, b in pairs:
        print(f"{a[:40]:<40} {b[:40]:<40}")
    return 0


def _cmd_profile_show(args: argparse.Namespace) -> int:
    from .profile import read_current_identity

    text = read_current_identity(max_chars=args.max_chars)
    if not text:
        print("(尚无 identity.md — 跑 `memoryd profile rewrite` 生成首版)", file=sys.stderr)
        return 0
    print(text)
    return 0


def _cmd_profile_history(args: argparse.Namespace) -> int:
    idx, store = _open_profile_store()
    try:
        versions = store.list_versions(limit=args.limit)
    finally:
        idx.close()
    # list_versions returns ascending; flip to newest-first for human readers.
    versions = list(reversed(versions))
    if args.as_json:
        rows = [
            {
                "version_num": v.version_num,
                "written_at": v.written_at.isoformat(),
                "trigger": v.trigger,
                "change_summary": v.change_summary,
                "sources_count": v.sources_count,
            }
            for v in versions
        ]
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not versions:
        print("no profile versions yet", file=sys.stderr)
        return 0
    print(f"{'v#':<6} {'written_at':<32} {'trigger':<14} summary")
    print("-" * 90)
    for v in versions:
        summary = (v.change_summary or "")[:60]
        print(f"{v.version_num:<6} {v.written_at.isoformat()[:32]:<32} {v.trigger[:14]:<14} {summary}")
    return 0


def _cmd_profile_diff(args: argparse.Namespace) -> int:
    import difflib

    idx, store = _open_profile_store()
    try:
        versions = {v.version_num: v for v in store.list_versions()}
    finally:
        idx.close()
    if args.from_ not in versions:
        print(f"version {args.from_} not found", file=sys.stderr)
        return 1
    if args.to not in versions:
        print(f"version {args.to} not found", file=sys.stderr)
        return 1
    a = versions[args.from_].content_md
    b = versions[args.to].content_md
    diff = "".join(
        difflib.unified_diff(
            a.splitlines(keepends=True),
            b.splitlines(keepends=True),
            fromfile=f"identity.v{args.from_}.md",
            tofile=f"identity.v{args.to}.md",
            n=3,
        )
    )
    if not diff:
        print(f"(v{args.from_} and v{args.to} have identical content)", file=sys.stderr)
        return 0
    print(diff, end="")
    return 0


def _cmd_profile_rewrite(args: argparse.Namespace) -> int:
    import asyncio

    from .profile import rewrite_identity_weekly

    idx, store = _open_profile_store()
    try:
        # Lazy LLM import: only resolve a provider when we actually need it.
        try:
            from .llm import get_provider, LLMUnavailable
            llm = get_provider()
        except LLMUnavailable as e:
            print(
                f"profile rewrite: {e}\n"
                "set ANTHROPIC_API_KEY (or configure another provider) "
                "via ~/.config/memoryd/config.toml.",
                file=sys.stderr,
            )
            return 1
        result = asyncio.run(
            rewrite_identity_weekly(
                idx.conn,
                store,
                llm=llm,
                sources_window_days=args.window_days,
                max_words=args.max_words,
                dry_run=args.dry_run,
                trigger="manual" if not args.dry_run else "manual_dry_run",
            )
        )
    finally:
        idx.close()

    if args.dry_run:
        # rewrite_identity_weekly returns a dict in dry_run mode.
        print("--- dry-run preview ---", file=sys.stderr)
        print(result.get("content_md", ""))
        summary = result.get("summary")
        if summary:
            print(f"\n[change_summary] {summary}", file=sys.stderr)
        return 0

    # Persisted ProfileVersion object.
    print(
        f"profile rewrite: v{result.version_num} written "
        f"(summary: {result.change_summary or 'n/a'})",
        file=sys.stderr,
    )
    return 0


def _cmd_profile_report(args: argparse.Namespace) -> int:
    import asyncio
    from datetime import datetime

    from .profile import generate_monthly_change_report

    # --current-month convenience used by the cron template.
    if getattr(args, "current_month", False):
        now = datetime.now()
        year, month = now.year, now.month
    else:
        if not args.month:
            print(
                "profile report: pass --month=YYYY-MM (or --current-month)",
                file=sys.stderr,
            )
            return 2
        try:
            year_s, month_s = args.month.split("-", 1)
            year, month = int(year_s), int(month_s)
        except ValueError:
            print(f"invalid --month value: {args.month!r}", file=sys.stderr)
            return 2

    idx, store = _open_profile_store()
    try:
        if not args.regenerate and not args.dry_run:
            existing = store.get_change_report(f"{year:04d}-{month:02d}")
            if existing:
                print(
                    f"profile report: {year:04d}-{month:02d} already exists "
                    "(use --regenerate to overwrite)",
                    file=sys.stderr,
                )
                print(existing["content_md"])
                return 0
        try:
            from .llm import get_provider, LLMUnavailable
            llm = get_provider()
        except LLMUnavailable as e:
            print(
                f"profile report: {e}\n"
                "set ANTHROPIC_API_KEY (or configure another provider) "
                "via ~/.config/memoryd/config.toml.",
                file=sys.stderr,
            )
            return 1
        result = asyncio.run(
            generate_monthly_change_report(
                idx.conn,
                store,
                llm=llm,
                year=year,
                month=month,
                dry_run=args.dry_run,
            )
        )
    finally:
        idx.close()

    print(result.get("content_md", ""))
    where = result.get("path")
    if where:
        print(f"\n(saved to {where})", file=sys.stderr)
    elif args.dry_run:
        print("\n(dry-run: nothing persisted)", file=sys.stderr)
    return 0


def _cmd_profile_trends(args: argparse.Namespace) -> int:
    from .profile import (
        recall_hot,
        render_trends_section,
        rising_triggers,
        top_triggers,
    )

    idx, _ = _open_profile_store()
    try:
        if args.as_json:
            payload = {
                "window_days": args.window_days,
                "top_triggers": [
                    {"trigger": t, "hits": h}
                    for t, h in top_triggers(idx.conn, window_days=args.window_days)
                ],
                "rising_triggers": [
                    {"trigger": t, "recent": r, "prior": p}
                    for t, r, p in rising_triggers(idx.conn, recent_days=args.window_days)
                ],
                "recall_hot": recall_hot(idx.conn),
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        else:
            print(render_trends_section(idx.conn, window_days=args.window_days))
    finally:
        idx.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="memoryd")
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_capture = subs.add_parser("capture", help="read SessionEnd payload from stdin and save")
    p_capture.add_argument(
        "--source",
        default="claude-code",
        help="origin tool tag written to frontmatter (claude-code | codex | openclaw | ...)",
    )
    p_capture.set_defaults(func=cmd_capture)

    p_az = subs.add_parser("analyze-session", help="run DURA extraction on a session (called by capture hook)")
    p_az.add_argument("session_slug")
    p_az.set_defaults(func=cmd_analyze_session)

    # `inject` — SessionStart hook back-end. Renders a small markdown block
    # (identity excerpt + top entities + recent long-term) for CC to feed
    # into additionalContext at the start of a new session.
    p_inject = subs.add_parser(
        "inject",
        help="render SessionStart context (identity + top entities + recent long-term)",
    )
    p_inject.add_argument(
        "--scope",
        default="auto",
        help="scope_hash, 'auto' (infer from CLAUDE_PROJECT_DIR/cwd), or 'global' for cross-scope",
    )
    p_inject.add_argument(
        "--max-chars",
        type=int,
        default=500,
        dest="max_chars",
        help="hard cap on identity.md excerpt (paragraph-aware truncation)",
    )
    p_inject.add_argument(
        "--top-entities",
        type=int,
        default=8,
        dest="top_entities",
        help="max number of top entities to list (default 8)",
    )
    p_inject.add_argument(
        "--window-days",
        type=int,
        default=30,
        dest="window_days",
        help="entity activity window in days (default 30)",
    )
    p_inject.add_argument(
        "--recent",
        type=int,
        default=5,
        help="max number of recent long-term memories to list",
    )
    p_inject.add_argument(
        "--types",
        nargs="+",
        default=None,
        help="memory types for the 'recent' list (default: decision preference fact)",
    )
    p_inject.add_argument(
        "--include-trends",
        action="store_true",
        dest="include_trends",
        help="append a 'recent triggers' single-line block",
    )
    p_inject.set_defaults(func=cmd_inject)

    p_mirror = subs.add_parser(
        "mirror",
        help="watch Codex / OpenClaw session log dirs and mirror new files into memoryd",
    )
    p_mirror.add_argument("--codex", action="store_true", help="mirror Codex rollout_summaries")
    p_mirror.add_argument("--openclaw", action="store_true", help="mirror OpenClaw session jsonl")
    p_mirror.add_argument(
        "--codex-dir",
        default=None,
        help="override Codex rollout dir (default: ~/.codex/memories/rollout_summaries)",
    )
    p_mirror.add_argument(
        "--openclaw-dir",
        default=None,
        help="override OpenClaw agents root (default: ~/.openclaw/agents)",
    )
    p_mirror.add_argument(
        "--known-roots",
        nargs="*",
        default=None,
        help="paths to use for OpenClaw content-based scope reverse-lookup",
    )
    p_mirror.add_argument(
        "--once",
        action="store_true",
        help="scan existing files once and exit (no watchdog)",
    )
    p_mirror.set_defaults(func=cmd_mirror)

    p_rebuild = subs.add_parser("rebuild-index", help="wipe and rebuild SQLite index from all Markdown files")
    p_rebuild.set_defaults(func=cmd_rebuild_index)

    p_setup = subs.add_parser(
        "setup",
        help="manage user-side config wire-up (~/.codex/, ~/Library/LaunchAgents/)",
    )
    setup_subs = p_setup.add_subparsers(dest="setup_cmd", required=True)

    # swap-codex-notify
    p_swap = setup_subs.add_parser("swap-codex-notify", help="swap Codex notify between probe/wrapper/original")
    p_swap.add_argument("--to", choices=["probe", "wrapper", "original"], required=True)
    p_swap.add_argument("--codex-dir", default=str(Path.home() / ".codex"))
    p_swap.add_argument("--backup-dir", default=str(Path.home() / ".claude" / "backups"))
    p_swap.add_argument("--probe-path", default="/Users/abble/memory-system/plugins/codex/notify-probe.sh")
    p_swap.add_argument("--wrapper-path", default="/Users/abble/memory-system/plugins/codex/notify-wrapper.sh")
    p_swap.set_defaults(func=_cmd_swap_notify)

    # remove-codex-stop-hook
    p_rm = setup_subs.add_parser("remove-codex-stop-hook", help="drop the dead Stop entry from ~/.codex/hooks.json")
    p_rm.add_argument("--codex-dir", default=str(Path.home() / ".codex"))
    p_rm.add_argument("--backup-dir", default=str(Path.home() / ".claude" / "backups"))
    p_rm.set_defaults(func=_cmd_remove_stop_hook)

    # install-launchd-mirror
    p_inst = setup_subs.add_parser("install-launchd-mirror", help="render and install LaunchAgent plist")
    p_inst.add_argument("--template", default="/Users/abble/memory-system/plugins/codex/launchd/com.memoryd.mirror.plist")
    p_inst.add_argument("--launch-dir", default=str(Path.home() / "Library" / "LaunchAgents"))
    p_inst.add_argument("--memoryd-bin", default="/Users/abble/memory-system/memoryd/.venv/bin/memoryd")
    p_inst.add_argument("--data-root", default=str(Path.home() / ".local" / "share" / "memoryd"))
    p_inst.set_defaults(func=_cmd_install_launchd)

    # uninstall-launchd-mirror
    p_un = setup_subs.add_parser("uninstall-launchd-mirror")
    p_un.add_argument("--launch-dir", default=str(Path.home() / "Library" / "LaunchAgents"))
    p_un.set_defaults(func=_cmd_uninstall_launchd)

    # install-cron --decay/--digest/--weekly-identity/--monthly-report/--all
    # (Plan 5 + Plan 10)
    p_inst_cron = setup_subs.add_parser(
        "install-cron",
        help="install decay / digest / weekly-identity / monthly-report cron jobs",
    )
    p_inst_cron.add_argument("--decay", action="store_true")
    p_inst_cron.add_argument("--digest", action="store_true")
    p_inst_cron.add_argument(
        "--weekly-identity",
        action="store_true",
        dest="weekly_identity",
        help="weekly LLM rewrite of profile/identity.md (Sun 02:00)",
    )
    p_inst_cron.add_argument(
        "--monthly-report",
        action="store_true",
        dest="monthly_report",
        help="monthly profile evolution report (1st of month 04:00)",
    )
    p_inst_cron.add_argument(
        "--task",
        default=None,
        help="install a specific cron task_key (e.g. weekly_identity, monthly_report)",
    )
    p_inst_cron.add_argument("--all", action="store_true")
    p_inst_cron.set_defaults(func=_cmd_install_cron)

    # uninstall-cron mirrors the install flags.
    p_un_cron = setup_subs.add_parser("uninstall-cron", help="uninstall cron jobs")
    p_un_cron.add_argument("--decay", action="store_true")
    p_un_cron.add_argument("--digest", action="store_true")
    p_un_cron.add_argument(
        "--weekly-identity",
        action="store_true",
        dest="weekly_identity",
    )
    p_un_cron.add_argument(
        "--monthly-report",
        action="store_true",
        dest="monthly_report",
    )
    p_un_cron.add_argument("--task", default=None)
    p_un_cron.add_argument("--all", action="store_true")
    p_un_cron.set_defaults(func=_cmd_uninstall_cron)

    # install-cc-hook
    p_inst_cc = setup_subs.add_parser(
        "install-cc-hook",
        help="wire CC SessionEnd hook (cross-platform Python wrapper)",
    )
    p_inst_cc.add_argument(
        "--include-session-start",
        action="store_true",
        dest="include_session_start",
        help="also install the SessionStart hook (recommended for full identity injection)",
    )
    p_inst_cc.set_defaults(func=_cmd_install_cc_hook)

    # install-cc-session-start-hook
    p_inst_cc_ss = setup_subs.add_parser(
        "install-cc-session-start-hook",
        help="wire CC SessionStart hook (injects identity / top entities / recent long-term)",
    )
    p_inst_cc_ss.set_defaults(func=_cmd_install_cc_session_start_hook)

    # auto-install
    p_auto = setup_subs.add_parser(
        "auto-install",
        help="detect platform and install cron + cc-hook in one shot",
    )
    p_auto.set_defaults(func=_cmd_auto_install)

    # install-memory-searcher
    p_ims = setup_subs.add_parser(
        "install-memory-searcher",
        help="copy memory-searcher.md template to ~/.claude/agents/",
    )
    p_ims.add_argument(
        "--target",
        type=Path,
        default=None,
        help="target directory; default ~/.claude/agents/",
    )
    p_ims.add_argument("--force", action="store_true")
    p_ims.set_defaults(func=_cmd_install_memory_searcher)

    # install-codex-agents-include — auto-inject identity into ~/.codex/AGENTS.md
    p_cai = setup_subs.add_parser(
        "install-codex-agents-include",
        help="把 memoryd identity + top entities + 最近决策注入到 ~/.codex/AGENTS.md（Codex 自动读 = 每次会话有上下文）",
    )
    p_cai.set_defaults(func=_cmd_install_codex_agents_include)

    p_uci = setup_subs.add_parser(
        "uninstall-codex-agents-include",
        help="从 ~/.codex/AGENTS.md 撤掉 memoryd 自动注入区段",
    )
    p_uci.set_defaults(func=_cmd_uninstall_codex_agents_include)

    p_uall = setup_subs.add_parser(
        "uninstall-all",
        help="一键撤销所有 auto-install 装的东西（cron + hooks + codex + mcp + launchd mirror）。不删数据。",
    )
    p_uall.set_defaults(func=_cmd_uninstall_all)

    # `doctor` — single-command health check. Top-level (not under `setup`) so
    # users can find it without knowing where to look.
    p_doctor = subs.add_parser(
        "doctor",
        help="健康检查 — 一条命令告诉你系统在不在干活",
    )
    p_doctor.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p_doctor.add_argument(
        "--quiet",
        action="store_true",
        help="omit OK rows; print only WARN / FAIL / INFO",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    # `backfill` — batch-replay analyze-session for sessions captured before
    # LLM was wired (otherwise their entities stay empty until they're
    # re-touched). Pairs with `setup auto-install` for new users.
    p_backfill = subs.add_parser(
        "backfill",
        help="一次性补跑历史 session 的 DURA + KG 抽取（装好 LLM 后追溯激活）",
    )
    p_backfill.add_argument("--limit", type=int, default=50, help="最多跑多少条（默认 50）")
    p_backfill.add_argument("--dry-run", action="store_true", help="只列要跑的 slug，不实际调 LLM")
    p_backfill.set_defaults(func=_cmd_backfill)

    # `llm test` — one-shot end-to-end sanity check of the configured LLM
    # provider. Prints latency + reply so users can verify before relying on
    # weekly identity / backfill flows.
    p_llm = subs.add_parser(
        "llm",
        help="LLM 子命令（test 验证当前 provider 通路）",
    )
    p_llm_subs = p_llm.add_subparsers(dest="llm_subcmd", required=True)
    p_llm_test = p_llm_subs.add_parser(
        "test",
        help="跑一次 ping → 验证 provider 真能调通（含 latency）",
    )
    p_llm_test.set_defaults(func=_cmd_llm_test)

    p_decay = subs.add_parser("decay-sweep", help="step memories through alive→dim→soft-forgotten state machine")
    p_decay.set_defaults(func=cmd_decay_sweep)

    p_merge = subs.add_parser("merge", help="merge dup memories (keep one, drop others)")
    p_merge.add_argument("--keep", required=True)
    p_merge.add_argument("--drop", nargs="+", required=True)
    p_merge.set_defaults(func=cmd_merge)

    # Plan 9: search / list / show subcommands
    p_search = subs.add_parser(
        "search",
        help="full-text search across memories (mirrors search_memory MCP tool)",
    )
    p_search.add_argument("query")
    p_search.add_argument("--scope", default=None,
                          help="filter by scope_hash")
    p_search.add_argument("--type", default=None, dest="type_",
                          help="filter by memory type")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--json", action="store_true", dest="as_json")
    p_search.set_defaults(func=cmd_search)

    p_list = subs.add_parser(
        "list",
        help="list memories filtered by type / scope",
    )
    p_list.add_argument("--type", default=None, dest="type_")
    p_list.add_argument("--scope", default=None)
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--json", action="store_true", dest="as_json")
    p_list.set_defaults(func=cmd_list)

    p_show = subs.add_parser(
        "show",
        help="display a single memory (frontmatter + body)",
    )
    p_show.add_argument("slug")
    p_show.add_argument("--scope", default=None)
    p_show.set_defaults(func=cmd_show)

    p_delete = subs.add_parser(
        "delete",
        help="delete a memory (unlinks .md and drops SQLite row)",
    )
    p_delete.add_argument("slug")
    p_delete.add_argument("--scope", default=None,
                          help="restrict search to this scope_hash")
    p_delete.add_argument("--force", action="store_true",
                          help="skip the y/N prompt")
    p_delete.set_defaults(func=cmd_delete)

    # `promote` — single id, --all, or --auto-high
    p_promote = subs.add_parser(
        "promote",
        help="approve a pending promotion (writes final .md + flips status)",
    )
    p_promote.add_argument("promotion_id", type=int, nargs="?", default=None,
                           help="单条 promotion id（与 --all / --auto-high 互斥）")
    p_promote.add_argument("--all", action="store_true",
                           help="批准全部 pending（86 条一键全过）")
    p_promote.add_argument("--auto-high", action="store_true",
                           help="只批准 DURA 4 维平均 >= 0.85 的（高 confidence 自动过）")
    p_promote.add_argument("--dry-run", action="store_true",
                           help="搭 --all/--auto-high：只列要批的，不实际操作")
    p_promote.set_defaults(func=cmd_promote)

    p_digest = subs.add_parser("digest", help="show weekly digest (promotions / duplicates / decayed)")
    p_digest.add_argument("--json", action="store_true")
    p_digest.add_argument(
        "--notify",
        action="store_true",
        help="emit native desktop notification + optional SMTP email",
    )
    p_digest.add_argument(
        "--tui",
        action="store_true",
        help="interactive textual TUI for approve/reject/merge",
    )
    p_digest.set_defaults(func=cmd_digest)

    p_audit = subs.add_parser("audit", help="show sensitive-scope audit events")
    p_audit.add_argument("--scope", default=None, help="filter by scope_hash")
    p_audit.add_argument("--since", default=None, help="ISO timestamp; only events at/after")
    p_audit.add_argument("--event-type", default=None, help="filter by event_type")
    p_audit.add_argument("--json", action="store_true", help="output JSON instead of table")
    p_audit.add_argument(
        "--verify",
        action="store_true",
        help="walk the prev_hash chain and report any tampering",
    )
    p_audit.set_defaults(func=cmd_audit)

    p_config = subs.add_parser("config", help="show / set memoryd config")
    cfg_subs = p_config.add_subparsers(dest="config_action", required=True)
    cfg_subs.add_parser("show", help="print resolved config as JSON")
    p_set = cfg_subs.add_parser("set", help="set a dotted key (e.g. llm.provider openai)")
    p_set.add_argument("key")
    p_set.add_argument("value")
    p_config.set_defaults(func=cmd_config)

    p_mark = subs.add_parser(
        "mark-sensitive",
        help="encrypt existing memories for a scope and store key in Keychain",
    )
    p_mark.add_argument("scope_path", help="path within the scope to mark")
    p_mark.set_defaults(func=cmd_mark_sensitive)

    p_unmark = subs.add_parser(
        "unmark-sensitive",
        help="decrypt memories for a scope and remove Keychain key",
    )
    p_unmark.add_argument("scope_path", help="path within the scope to unmark")
    p_unmark.set_defaults(func=cmd_unmark_sensitive)

    p_grant = subs.add_parser("grant", help="grant sensitive scope access")
    p_grant.add_argument("scope_path")
    p_grant.add_argument("--duration", choices=["once", "session", "task"], required=True)
    p_grant.add_argument("--task", default=None)
    p_grant.set_defaults(func=cmd_grant)

    p_revoke = subs.add_parser("revoke", help="revoke sensitive scope access")
    p_revoke.add_argument("scope_path")
    p_revoke.add_argument("--task", default=None)
    p_revoke.set_defaults(func=cmd_revoke)

    # sync export / import / status (Plan 6)
    p_sync = subs.add_parser(
        "sync",
        help="multi-device sync (raw .md mirror against cfg.sync.dir)",
    )
    sync_subs = p_sync.add_subparsers(dest="sync_cmd", required=True)

    p_sex = sync_subs.add_parser("export", help="mirror local memories into sync dir")
    p_sex.add_argument("--scope", default=None, help="filter by scope_hash")
    p_sex.add_argument("--dry-run", action="store_true")
    p_sex.add_argument(
        "--auto",
        action="store_true",
        help="only run if [sync] enabled and auto_export_on_session_end (hook use)",
    )
    p_sex.set_defaults(func=_cmd_sync_export)

    p_sim = sync_subs.add_parser("import", help="pull from sync dir; conflicts go to _conflicts/")
    p_sim.add_argument("--scope", default=None, help="filter by scope_hash")
    p_sim.add_argument("--dry-run", action="store_true")
    p_sim.add_argument(
        "--auto",
        action="store_true",
        help="only run if [sync] enabled and auto_import_on_session_start (hook use)",
    )
    p_sim.set_defaults(func=_cmd_sync_import)

    p_sst = sync_subs.add_parser("status", help="show per-scope sync counts")
    p_sst.add_argument("--json", action="store_true", dest="as_json")
    p_sst.set_defaults(func=_cmd_sync_status)

    p_pp = subs.add_parser(
        "set-passphrase",
        help="set memoryd master passphrase (Plan 6 sensitive scope cross-device)",
    )
    p_pp.set_defaults(func=_cmd_set_passphrase)

    # import <kind> <path>  (Plan 8: one-shot import from older memory layouts)
    p_import = subs.add_parser(
        "import",
        help="one-shot import from older memory layouts (single direction)",
    )
    import_subs = p_import.add_subparsers(dest="import_kind", required=True)

    for _kind in ("claude-md", "auto-memory", "agents-md", "mcp-memory-service"):
        pp = import_subs.add_parser(_kind)
        pp.add_argument("path", type=Path)
        pp.add_argument(
            "--scope",
            default=None,
            help="explicit scope_hash; default = cwd-derived",
        )
        pp.add_argument("--dry-run", action="store_true")
        pp.add_argument(
            "--force",
            action="store_true",
            help="overwrite existing slugs",
        )
        pp.add_argument("--source-tag", default=None, dest="source_tag")
        pp.set_defaults(func=_cmd_import)

    p_web = subs.add_parser(
        "web",
        help="launch local browse dashboard (FastAPI on 127.0.0.1)",
    )
    p_web.add_argument("--port", type=int, default=None)
    p_web.add_argument("--no-browser", action="store_true")
    p_web.set_defaults(func=_cmd_web)

    # ------------------------------------------------------------------
    # Plan 10: knowledge_graph subcommands
    # ------------------------------------------------------------------
    p_kg = subs.add_parser(
        "kg",
        help="knowledge graph queries (entities / relations / supersedes)",
    )
    kg_subs = p_kg.add_subparsers(dest="kg_cmd", required=True)

    p_kg_ent = kg_subs.add_parser("entities", help="top entities by mention_count")
    p_kg_ent.add_argument("--scope", default=None, help="filter by scope_hash")
    p_kg_ent.add_argument("--type", default=None, dest="type_",
                          help="entity type filter (person/library/project/...)")
    p_kg_ent.add_argument("--top", type=int, default=20)
    p_kg_ent.add_argument("--window-days", type=int, default=30, dest="window_days")
    p_kg_ent.add_argument("--json", action="store_true", dest="as_json")
    p_kg_ent.set_defaults(func=_cmd_kg_entities)

    p_kg_ma = kg_subs.add_parser(
        "memories-about",
        help="list memory slugs that mention a given entity",
    )
    p_kg_ma.add_argument("entity", help="entity name (fuzzy match) or full entity:id")
    p_kg_ma.add_argument(
        "--types",
        default=None,
        help="comma-separated memory types to filter (session,decision,...)",
    )
    p_kg_ma.add_argument("--json", action="store_true", dest="as_json")
    p_kg_ma.set_defaults(func=_cmd_kg_memories_about)

    p_kg_ev = kg_subs.add_parser(
        "evolution",
        help="show the supersedes chain (old → new) anchored on an entity",
    )
    p_kg_ev.add_argument("entity")
    p_kg_ev.add_argument("--json", action="store_true", dest="as_json")
    p_kg_ev.set_defaults(func=_cmd_kg_evolution)

    p_kg_sub = kg_subs.add_parser(
        "subgraph",
        help="N-hop subgraph anchored on an entity (cytoscape or plain JSON)",
    )
    p_kg_sub.add_argument("entity")
    p_kg_sub.add_argument("--depth", type=int, default=2)
    p_kg_sub.add_argument("--out", default=None, help="write JSON to file instead of stdout")
    p_kg_sub.add_argument(
        "--format",
        choices=["cytoscape", "json"],
        default="cytoscape",
    )
    p_kg_sub.set_defaults(func=_cmd_kg_subgraph)

    p_kg_conf = kg_subs.add_parser(
        "conflicts",
        help="memory pairs that make conflicting statements about the same entity",
    )
    p_kg_conf.add_argument("--scope", default=None, help="filter by scope_hash")
    p_kg_conf.add_argument("--json", action="store_true", dest="as_json")
    p_kg_conf.set_defaults(func=_cmd_kg_conflicts)

    # ------------------------------------------------------------------
    # Plan 10: profile self-learning subcommands
    # ------------------------------------------------------------------
    p_profile = subs.add_parser(
        "profile",
        help="self-learning user profile (identity.md, monthly reports, trends)",
    )
    prof_subs = p_profile.add_subparsers(dest="profile_cmd", required=True)

    p_pf_show = prof_subs.add_parser("show", help="print current identity.md")
    p_pf_show.add_argument("--max-chars", type=int, default=2000, dest="max_chars")
    p_pf_show.set_defaults(func=_cmd_profile_show)

    p_pf_hist = prof_subs.add_parser("history", help="list profile_versions rows")
    p_pf_hist.add_argument("--limit", type=int, default=20)
    p_pf_hist.add_argument("--json", action="store_true", dest="as_json")
    p_pf_hist.set_defaults(func=_cmd_profile_history)

    p_pf_diff = prof_subs.add_parser(
        "diff",
        help="unified diff between two profile_versions rows",
    )
    p_pf_diff.add_argument("--from", type=int, required=True, dest="from_")
    p_pf_diff.add_argument("--to", type=int, required=True)
    p_pf_diff.set_defaults(func=_cmd_profile_diff)

    p_pf_rw = prof_subs.add_parser(
        "rewrite",
        help="LLM rewrite of identity.md (weekly cron + manual ad-hoc)",
    )
    p_pf_rw.add_argument("--dry-run", action="store_true")
    p_pf_rw.add_argument("--window-days", type=int, default=7, dest="window_days")
    p_pf_rw.add_argument("--max-words", type=int, default=800, dest="max_words")
    p_pf_rw.set_defaults(func=_cmd_profile_rewrite)

    p_pf_rep = prof_subs.add_parser(
        "report",
        help="generate the monthly profile evolution report",
    )
    p_pf_rep.add_argument("--month", default=None, help="YYYY-MM (defaults to last full month if --current-month)")
    p_pf_rep.add_argument(
        "--current-month",
        action="store_true",
        dest="current_month",
        help="use the current calendar month (used by the monthly cron job)",
    )
    p_pf_rep.add_argument("--dry-run", action="store_true")
    p_pf_rep.add_argument(
        "--regenerate",
        action="store_true",
        help="overwrite an existing report for this month",
    )
    p_pf_rep.set_defaults(func=_cmd_profile_report)

    p_pf_tr = prof_subs.add_parser(
        "trends",
        help="render the trends markdown section (top / rising / recall_hot)",
    )
    p_pf_tr.add_argument("--window-days", type=int, default=7, dest="window_days")
    p_pf_tr.add_argument("--json", action="store_true", dest="as_json")
    p_pf_tr.set_defaults(func=_cmd_profile_trends)

    args = parser.parse_args()
    return args.func(args)


def _cmd_web(args: argparse.Namespace) -> int:
    from .web.server import run
    return run(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
