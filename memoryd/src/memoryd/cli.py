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
import subprocess
import sys
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
    if args.all or args.decay:
        keys.append("decay")
    if args.all or args.digest:
        keys.append("digest")
    if not keys:
        print("install-cron: pass --decay, --digest, or --all", file=sys.stderr)
        return 2
    for k in keys:
        out = setup_mod.install_cron(k)
        print(f"installed {k}: {out}", file=sys.stderr)
    return 0


def _cmd_uninstall_cron(args: argparse.Namespace) -> int:
    keys: list[str] = []
    if args.all or args.decay:
        keys.append("decay")
    if args.all or args.digest:
        keys.append("digest")
    if not keys:
        print("uninstall-cron: pass --decay, --digest, or --all", file=sys.stderr)
        return 2
    for k in keys:
        setup_mod.uninstall_cron(k)
        print(f"uninstalled {k}", file=sys.stderr)
    return 0


def _cmd_install_cc_hook(args: argparse.Namespace) -> int:
    out = setup_mod.install_cc_hook()
    print(f"wired CC SessionEnd hook in {out}", file=sys.stderr)
    return 0


def _cmd_auto_install(args: argparse.Namespace) -> int:
    import json as _json
    res = setup_mod.auto_install()
    print(_json.dumps(res, indent=2))
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


def cmd_digest(args: argparse.Namespace) -> int:
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
    from .governance.audit import query_events
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
    if not cfg.sync.dir:
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
    if not cfg.sync.dir:
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
    p_swap.add_argument("--probe-path", default="/Users/abble/project-management-personal/scripts/codex-notify-probe.sh")
    p_swap.add_argument("--wrapper-path", default="/Users/abble/project-management-personal/scripts/codex-notify-wrapper.sh")
    p_swap.set_defaults(func=_cmd_swap_notify)

    # remove-codex-stop-hook
    p_rm = setup_subs.add_parser("remove-codex-stop-hook", help="drop the dead Stop entry from ~/.codex/hooks.json")
    p_rm.add_argument("--codex-dir", default=str(Path.home() / ".codex"))
    p_rm.add_argument("--backup-dir", default=str(Path.home() / ".claude" / "backups"))
    p_rm.set_defaults(func=_cmd_remove_stop_hook)

    # install-launchd-mirror
    p_inst = setup_subs.add_parser("install-launchd-mirror", help="render and install LaunchAgent plist")
    p_inst.add_argument("--template", default="/Users/abble/project-management-personal/scripts/launchd/com.memoryd.mirror.plist")
    p_inst.add_argument("--launch-dir", default=str(Path.home() / "Library" / "LaunchAgents"))
    p_inst.add_argument("--memoryd-bin", default="/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd")
    p_inst.add_argument("--data-root", default=str(Path.home() / ".local" / "share" / "memoryd"))
    p_inst.set_defaults(func=_cmd_install_launchd)

    # uninstall-launchd-mirror
    p_un = setup_subs.add_parser("uninstall-launchd-mirror")
    p_un.add_argument("--launch-dir", default=str(Path.home() / "Library" / "LaunchAgents"))
    p_un.set_defaults(func=_cmd_uninstall_launchd)

    # install-cron --decay/--digest/--all (Plan 5)
    p_inst_cron = setup_subs.add_parser(
        "install-cron",
        help="install daily decay / weekly digest cron (cross-platform)",
    )
    p_inst_cron.add_argument("--decay", action="store_true")
    p_inst_cron.add_argument("--digest", action="store_true")
    p_inst_cron.add_argument("--all", action="store_true")
    p_inst_cron.set_defaults(func=_cmd_install_cron)

    # uninstall-cron --decay/--digest/--all
    p_un_cron = setup_subs.add_parser("uninstall-cron", help="uninstall cron jobs")
    p_un_cron.add_argument("--decay", action="store_true")
    p_un_cron.add_argument("--digest", action="store_true")
    p_un_cron.add_argument("--all", action="store_true")
    p_un_cron.set_defaults(func=_cmd_uninstall_cron)

    # install-cc-hook
    p_inst_cc = setup_subs.add_parser(
        "install-cc-hook",
        help="wire CC SessionEnd hook (cross-platform Python wrapper)",
    )
    p_inst_cc.set_defaults(func=_cmd_install_cc_hook)

    # auto-install
    p_auto = setup_subs.add_parser(
        "auto-install",
        help="detect platform and install cron + cc-hook in one shot",
    )
    p_auto.set_defaults(func=_cmd_auto_install)

    p_decay = subs.add_parser("decay-sweep", help="step memories through alive→dim→soft-forgotten state machine")
    p_decay.set_defaults(func=cmd_decay_sweep)

    p_merge = subs.add_parser("merge", help="merge dup memories (keep one, drop others)")
    p_merge.add_argument("--keep", required=True)
    p_merge.add_argument("--drop", nargs="+", required=True)
    p_merge.set_defaults(func=cmd_merge)

    p_digest = subs.add_parser("digest", help="show weekly digest (promotions / duplicates / decayed)")
    p_digest.add_argument("--json", action="store_true")
    p_digest.add_argument(
        "--notify",
        action="store_true",
        help="emit native desktop notification + optional SMTP email",
    )
    p_digest.set_defaults(func=cmd_digest)

    p_audit = subs.add_parser("audit", help="show sensitive-scope audit events")
    p_audit.add_argument("--scope", default=None, help="filter by scope_hash")
    p_audit.add_argument("--since", default=None, help="ISO timestamp; only events at/after")
    p_audit.add_argument("--event-type", default=None, help="filter by event_type")
    p_audit.add_argument("--json", action="store_true", help="output JSON instead of table")
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
    p_sex.set_defaults(func=_cmd_sync_export)

    p_sim = sync_subs.add_parser("import", help="pull from sync dir; conflicts go to _conflicts/")
    p_sim.add_argument("--scope", default=None, help="filter by scope_hash")
    p_sim.add_argument("--dry-run", action="store_true")
    p_sim.set_defaults(func=_cmd_sync_import)

    p_sst = sync_subs.add_parser("status", help="show per-scope sync counts")
    p_sst.add_argument("--json", action="store_true", dest="as_json")
    p_sst.set_defaults(func=_cmd_sync_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
