"""Append-only audit log with prev_hash chain.

Format: ~/.local/share/memoryd/audit/audit.jsonl, one JSON object per
line. Each event has a `prev_hash` field = sha256 of the previous line
(without its prev_hash). First line uses 64 zero chars. Tampering with
a line breaks the chain at that line and all subsequent.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


_ZERO_PREV = "0" * 64


def audit_log_path() -> Path:
    root = os.environ.get("MEMORYD_DATA_ROOT")
    base = Path(root) if root else (Path.home() / ".local" / "share" / "memoryd")
    return base / "audit" / "audit.jsonl"


def _last_line(p: Path) -> str | None:
    if not p.exists():
        return None
    with p.open("rb") as f:
        try:
            f.seek(-2, os.SEEK_END)
            while f.read(1) != b"\n":
                f.seek(-2, os.SEEK_CUR)
        except OSError:
            f.seek(0)
        return f.readline().decode("utf-8").strip() or None


def _hash_for_chain(event: dict) -> str:
    """Hash everything except prev_hash itself (deterministic ordering)."""
    payload = {k: v for k, v in event.items() if k != "prev_hash"}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def append_event(event: dict) -> dict:
    """Append event to audit.jsonl with prev_hash linking to previous line.

    The read-prev → compute-hash → write-new sequence is serialized with an
    exclusive ``fcntl.flock`` so concurrent writers (e.g. a cron job firing
    while ``memoryd capture`` runs from a CC SessionEnd hook) can't interleave
    and break the chain.
    """
    import fcntl  # POSIX-only. On Windows the lock degrades to best-effort
    # (no locking) — see the bare ``except (OSError, AttributeError): pass``
    # below. A future Windows implementation can drop in ``msvcrt.locking``
    # or ``portalocker`` but right now we document the platform limit
    # honestly rather than silently break the chain.

    p = audit_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Open in append mode and grab an exclusive lock for the entire critical
    # section. Using `with p.open(...)` lets us hold the file handle across
    # the previous-line read and the new-line write, ensuring atomicity.
    with p.open("a+", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except (OSError, AttributeError):
            # Non-POSIX or filesystem refuses flock — degrade gracefully.
            pass
        # Re-read last line under the lock (another writer may have appended
        # between path probe and lock acquisition).
        f.seek(0)
        last = ""
        for line in f:
            line = line.strip()
            if line:
                last = line
        if last:
            try:
                prev = json.loads(last)
                prev_hash = _hash_for_chain(prev)
            except Exception:  # noqa: BLE001 — corrupt prior line → reset chain
                prev_hash = _ZERO_PREV
        else:
            prev_hash = _ZERO_PREV
        event = dict(event)
        if "ts" not in event:
            event["ts"] = datetime.now(timezone.utc).isoformat()
        event["prev_hash"] = prev_hash
        # Move to end and append.
        f.seek(0, 2)
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
        f.flush()
    return event


def query_events(
    *,
    scope_hash: str | None = None,
    since: datetime | None = None,
    event_type: str | None = None,
) -> list[dict]:
    p = audit_log_path()
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if scope_hash is not None and event.get("scope_hash") != scope_hash:
            continue
        if event_type is not None and event.get("event_type") != event_type:
            continue
        if since is not None:
            try:
                ts = datetime.fromisoformat(event["ts"])
                if ts < since:
                    continue
            except Exception:
                continue
        out.append(event)
    return out


def verify_chain() -> tuple[bool, int]:
    """Return (is_valid, first_broken_line_1_indexed_or_minus_1)."""
    p = audit_log_path()
    if not p.exists():
        return True, -1
    expected_prev = _ZERO_PREV
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return False, i
        if event.get("prev_hash") != expected_prev:
            return False, i
        expected_prev = _hash_for_chain(event)
    return True, -1
