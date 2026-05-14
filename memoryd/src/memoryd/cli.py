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
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .mirror import MirrorRouter
from .mirror_codex import CodexRolloutHandler
from .mirror_openclaw import OpenClawSessionHandler
from .schema import Frontmatter, SessionMemory
from .scope import resolve_scope_root, scope_hash
from .storage import save_session
from . import setup as setup_mod


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
    return save_session(memory_root, session)


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

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
