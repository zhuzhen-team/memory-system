"""Run DURA 4-criteria extraction on a single session.

Strategy:
- load session.md
- query existing long-term titles in same scope (for U criterion)
- call LLM with bundled prompt template
- parse JSON candidates; filter by DURA >= 0.6 all four
- write each as a row in promotions table (status=pending)

Never raises into caller — best-effort daemon. On LLM failure logs +
skips. Session capture path keeps working regardless.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..config import load_config
from ..index import open_index
from ..schema import Frontmatter, SessionMemory


_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "dura_extract.txt"


def build_dura_prompt(
    session: SessionMemory,
    *,
    scope_root: str,
    existing_titles: list[str],
) -> str:
    cfg = load_config()
    override = cfg.get("prompts", {}).get("dura_extract", "")
    if override and Path(override).exists():
        template = Path(override).read_text(encoding="utf-8")
    else:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
    body_clip = session.body[:8000]
    return (
        template
        .replace("{{session_body}}", body_clip)
        .replace("{{scope_root}}", scope_root)
        .replace("{{existing_titles}}", "\n".join(f"- {t}" for t in existing_titles) or "(none)")
    )


def parse_candidates(raw: str) -> list[dict]:
    """Robust JSON parse: strip fences, accept array."""
    stripped = raw.strip()
    if stripped.startswith("```"):
        # strip leading fence (with optional 'json' tag) and trailing fence
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        # try to find an array somewhere
        m = re.search(r"\[.*\]", stripped, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        dura = item.get("dura") or {}
        if not all(k in dura for k in ("D", "U", "R", "A")):
            continue
        if not all(isinstance(dura[k], (int, float)) and dura[k] >= 0.6 for k in ("D", "U", "R", "A")):
            continue
        out.append(item)
    return out


def analyze_session(
    memory_root: Path,
    *,
    session_slug: str,
    provider,
) -> None:
    """Best-effort: read session, ask LLM, write promotions. Never raises."""
    try:
        idx = open_index(memory_root / "index.db")
    except Exception:
        return
    try:
        row = idx.get_memory(session_slug)
        if row is None:
            return
        sess_path = memory_root / row["body_path"]
        if not sess_path.exists():
            return
        from ..storage import load_session
        session = load_session(sess_path)
        scope_hash = row["scope_hash"]

        # existing titles in same scope (long-term only — exclude sessions)
        existing_titles = []
        for t in ("decision", "preference", "fact", "playbook", "warning"):
            for r in idx.list_by_type(t, scope_hash=scope_hash):
                existing_titles.append(r["title"])

        prompt = build_dura_prompt(session, scope_root=scope_hash, existing_titles=existing_titles)
        try:
            raw = provider.complete(system="Extract durable insights.", user=prompt)
        except Exception:
            return
        candidates = parse_candidates(raw)

        now = datetime.now(timezone.utc).isoformat()
        for c in candidates:
            idx.conn.execute(
                """
                INSERT INTO promotions (
                  source_session_slug, proposed_type, proposed_title,
                  proposed_body, proposed_triggers, dura_score, reasoning,
                  proposed_supersedes, scope_hash, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    session_slug,
                    c["type"],
                    c["title"][:200],
                    c["body"][:5000],
                    json.dumps(c.get("triggers", [])),
                    json.dumps(c["dura"]),
                    c.get("reasoning", "")[:500],
                    json.dumps(c.get("supersedes", [])),
                    scope_hash,
                    now,
                ),
            )
        idx.conn.commit()
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# Module-level promotion-row helpers (used by TUI / future CLI commands).
#
# These deliberately only flip the SQLite status field; writing the actual
# long-term-memory .md file is the job of the MCP `promote_to_long_term`
# tool (which runs the full digest pipeline). After approving here the
# user can re-run `memoryd digest` or invoke the MCP tool to realize the
# change. Future tickets may bundle file emission into approve_promotion.
# ---------------------------------------------------------------------------


def list_pending_promotions(data_root: Path) -> list[dict]:
    """List rows from promotions table with status='pending'.

    Returns empty list if index.db does not yet exist.
    """
    db = data_root / "index.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT id, source_session_slug, proposed_type, proposed_title, "
            "       proposed_body, proposed_triggers, reasoning, status "
            "FROM promotions WHERE status = 'pending' ORDER BY id DESC"
        ).fetchall()
    finally:
        conn.close()
    cols = [
        "id",
        "source_session_slug",
        "proposed_type",
        "proposed_title",
        "proposed_body",
        "proposed_triggers",
        "reasoning",
        "status",
    ]
    return [dict(zip(cols, r)) for r in rows]


def _promotions_columns(conn: sqlite3.Connection) -> set[str]:
    """Return the set of column names on the promotions table.

    Older test fixtures (test_tui_digest._init_db) create a stripped schema
    without `scope_hash` / `dura_score` / `created_at`; we degrade gracefully
    in that case.
    """
    return {r[1] for r in conn.execute("PRAGMA table_info(promotions)")}


def approve_promotion(data_root: Path, promotion_id: int) -> Path | None:
    """Approve a promotion: mark status=approved and write the .md file.

    Plan 9 task 2: the function now realizes the promotion by saving a long-term
    memory `.md` to ``<data_root>/scopes/<scope_hash>/<type_dir>/promoted-<id>-<slug>.md``
    via :func:`memoryd.storage.save_memory`. The Frontmatter's ``promoted_from``
    field links back to the source session slug.

    Returns the written :class:`pathlib.Path`, or ``None`` if there is no
    ``proposed_body`` to materialize (e.g. legacy / minimal promotion row).

    Compatible with old callers (TUI) that ignore the return value.
    """
    db = data_root / "index.db"
    if not db.exists():
        raise FileNotFoundError(f"no index.db at {db}")
    conn = sqlite3.connect(str(db))
    try:
        cols = _promotions_columns(conn)
        select_cols = [
            "source_session_slug",
            "proposed_type",
            "proposed_title",
            "proposed_body",
            "proposed_triggers",
        ]
        if "scope_hash" in cols:
            select_cols.append("scope_hash")
        row = conn.execute(
            f"SELECT {', '.join(select_cols)} FROM promotions WHERE id = ?",
            (promotion_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"no promotion #{promotion_id}")
        data = dict(zip(select_cols, row))

        cur = conn.execute(
            "UPDATE promotions SET status='approved' WHERE id = ?",
            (promotion_id,),
        )
        if cur.rowcount == 0:
            raise ValueError(f"no promotion #{promotion_id}")
        conn.commit()
    finally:
        conn.close()

    body = data.get("proposed_body")
    if not body:
        # Legacy/minimal row → just flip status; nothing to write.
        return None

    sh = data.get("scope_hash")
    if not sh:
        from ..scope import resolve_scope_root, scope_hash as _scope_hash
        sh = _scope_hash(resolve_scope_root(Path.cwd()))

    try:
        triggers = json.loads(data.get("proposed_triggers") or "[]")
        if not isinstance(triggers, list):
            triggers = []
    except (json.JSONDecodeError, TypeError):
        triggers = []

    now = datetime.now(timezone.utc)
    source_slug = data.get("source_session_slug") or "unknown"
    slug = f"promoted-{promotion_id}-{source_slug}"
    fm_kwargs = dict(
        title=data.get("proposed_title") or slug,
        slug=slug,
        type=data.get("proposed_type") or "decision",
        scope_hash=sh,
        triggers=[str(t) for t in triggers],
        source="promotion",
        created_at=now,
        promoted_from=source_slug,
    )
    # Drop promoted_from if Frontmatter doesn't accept it (defense in depth;
    # current Plan 3 schema has it but tests may pin older snapshots).
    try:
        fm = Frontmatter(**fm_kwargs)
    except Exception:
        fm_kwargs.pop("promoted_from", None)
        fm = Frontmatter(**fm_kwargs)

    mem = SessionMemory(frontmatter=fm, body=body)
    from ..storage import save_memory  # local import to avoid cycle
    return save_memory(data_root, mem)


def reject_promotion(data_root: Path, promotion_id: int) -> None:
    """Reject a promotion: mark status=rejected."""
    db = data_root / "index.db"
    if not db.exists():
        raise FileNotFoundError(f"no index.db at {db}")
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "UPDATE promotions SET status='rejected' WHERE id = ?",
            (promotion_id,),
        )
        if cur.rowcount == 0:
            raise ValueError(f"no promotion #{promotion_id}")
        conn.commit()
    finally:
        conn.close()
