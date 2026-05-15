"""TTL + decay + soft-forget state machine.

State transitions on `memoryd decay-sweep`:
  alive  → dim          : ttl_days set + age_since_recall_or_create > ttl_days
  dim    → soft-forgot  : 30 days since last touch
  soft-f → forgotten/   : 90 more days since last touch (physical move)
  any    ← alive        : record_recall resets via search hits

`age_since_recall_or_create` = (now - max(last_recalled_at, created_at))
"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..index import open_index


DIM_AFTER_TTL = 0           # days after ttl_days → enter dim
SOFT_FORGET_AFTER_DIM = 30  # days dim with no recall → soft-forgotten
FORGOTTEN_AFTER_SF = 90     # days soft-forgotten with no recall → physical move


def _parse_iso(s: str | None) -> datetime | None:
    """Parse ISO timestamp; assume UTC if naive (Plan 1 capture wrote naive)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _age_days(now: datetime, ref_iso: str | None) -> float:
    ref = _parse_iso(ref_iso)
    if ref is None:
        return float("inf")
    return (now - ref).total_seconds() / 86400.0


def sweep_decay(memory_root: Path, *, now: datetime | None = None) -> dict[str, int]:
    """Walk SQLite index, transition states. Returns counts of each transition."""
    if now is None:
        now = datetime.now(timezone.utc)
    idx = open_index(memory_root / "index.db")
    counts = {"to_dim": 0, "to_soft_forgotten": 0, "to_forgotten_dir": 0}
    try:
        rows = idx.conn.execute(
            "SELECT slug, decay_state, ttl_days, created_at, last_recalled_at, body_path, scope_hash "
            "FROM memories"
        ).fetchall()
        for r in rows:
            row = dict(r)
            ttl = row["ttl_days"]
            state = row["decay_state"]
            # Effective "age since last touch": max of created_at / last_recalled_at gone.
            last_iso = row["last_recalled_at"] or row["created_at"]
            age = _age_days(now, last_iso)

            if state == "alive":
                if ttl is None:
                    continue  # long-term never auto-decays
                if age > ttl + DIM_AFTER_TTL:
                    idx.update_decay_state(row["slug"], "dim")
                    counts["to_dim"] += 1
            elif state == "dim":
                # 30 days since last touch (independent of original ttl)
                if age > SOFT_FORGET_AFTER_DIM:
                    idx.update_decay_state(row["slug"], "soft-forgotten")
                    counts["to_soft_forgotten"] += 1
            elif state == "soft-forgotten":
                # 90 days since last touch (independent of original ttl)
                if age > FORGOTTEN_AFTER_SF:
                    # Physical move to scopes/<scope_hash>/forgotten/<slug>.md
                    src = memory_root / row["body_path"]
                    if not src.exists():
                        continue
                    dest_dir = memory_root / "scopes" / row["scope_hash"] / "forgotten"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest = dest_dir / src.name
                    shutil.move(str(src), str(dest))
                    # update body_path in index
                    new_body_path = str(dest.relative_to(memory_root))
                    idx.conn.execute(
                        "UPDATE memories SET body_path = ? WHERE slug = ?",
                        (new_body_path, row["slug"]),
                    )
                    idx.conn.commit()
                    counts["to_forgotten_dir"] += 1
    finally:
        idx.close()
    return counts
