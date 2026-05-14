"""Build the weekly digest payload (no TUI yet -- Plan 7).

Three sections:
- promotions (status=pending in promotions table)
- duplicates (memories sharing fingerprint)
- decayed (decay_state in {dim, soft-forgotten})
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..index import open_index


def build_digest(memory_root: Path) -> dict[str, Any]:
    idx = open_index(memory_root / "index.db")
    try:
        promos = [dict(r) for r in idx.conn.execute(
            "SELECT * FROM promotions WHERE status = 'pending' ORDER BY created_at DESC"
        ).fetchall()]

        # duplicates: group by fingerprint where count >= 2
        fp_rows = idx.conn.execute(
            "SELECT fingerprint, GROUP_CONCAT(slug, '||') AS slugs, COUNT(*) AS n "
            "FROM memories GROUP BY fingerprint HAVING n >= 2"
        ).fetchall()
        duplicates = [r["slugs"].split("||") for r in fp_rows]

        decayed = [dict(r) for r in idx.conn.execute(
            "SELECT slug, type, title, decay_state, last_recalled_at "
            "FROM memories WHERE decay_state IN ('dim', 'soft-forgotten') "
            "ORDER BY last_recalled_at"
        ).fetchall()]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "promotions": promos,
            "duplicates": duplicates,
            "decayed": decayed,
        }
    finally:
        idx.close()


def render_digest_text(digest: dict[str, Any]) -> str:
    """Plain-text rendering. TUI lives in Plan 7."""
    lines: list[str] = []
    lines.append(f"=== memoryd weekly digest @ {digest['generated_at']} ===")
    lines.append("")
    lines.append(f"候选提升 promotions ({len(digest['promotions'])} 待审):")
    for p in digest["promotions"][:30]:
        try:
            dura = json.loads(p["dura_score"])
        except Exception:
            dura = {}
        dura_str = " ".join(f"{k}={v:.2f}" for k, v in dura.items())
        lines.append(f"  [{p['proposed_type']}] {p['proposed_title']}  ({dura_str})")
        lines.append(f"    source: {p['source_session_slug']}  scope: {p['scope_hash']}")
    lines.append("")
    lines.append(f"重复合并 duplicates ({len(digest['duplicates'])} 对):")
    for pair in digest["duplicates"][:30]:
        lines.append(f"  ~ {' / '.join(pair)}")
    lines.append("")
    lines.append(f"TTL / decay 提醒 ({len(digest['decayed'])} 条):")
    for d in digest["decayed"][:30]:
        lines.append(f"  [{d['decay_state']}] {d['type']} {d['slug']} -- {d['title']}")
    return "\n".join(lines) + "\n"
