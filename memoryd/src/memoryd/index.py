"""SQLite index over Markdown memory files.

Markdown is source of truth; this index is rebuildable via `memoryd
rebuild-index` (Task 4). The index makes type filters / decay queries /
promotions list / fingerprint dedup cheap. `open_index` runs all
migrations under `migrations/` in numeric order on first open.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import SessionMemory


DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "memoryd" / "index.db"


def _db_path() -> Path:
    override = os.environ.get("MEMORYD_INDEX_DB")
    if override:
        return Path(override)
    root = os.environ.get("MEMORYD_DATA_ROOT")
    if root:
        return Path(root) / "index.db"
    return DEFAULT_DB_PATH


_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def fingerprint_body(body: str) -> str:
    """sha1 over first 500 chars; used for cross-path dedup heuristic."""
    return hashlib.sha1(body[:500].encode("utf-8")).hexdigest()


class Index:
    """Wrapper around a sqlite3.Connection with helper methods."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    # -- write side ---------------------------------------------------------

    def index_memory(self, mem: SessionMemory, *, body_path: str) -> None:
        """Insert-or-update the memory row + replace its triggers."""
        fm = mem.frontmatter
        fp = fingerprint_body(mem.body)
        self.conn.execute(
            """
            INSERT INTO memories
                (slug, type, scope_hash, title, source, created_at, updated_at,
                 ttl_days, decay_state, last_recalled_at, recall_count,
                 fingerprint, body_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                type=excluded.type,
                scope_hash=excluded.scope_hash,
                title=excluded.title,
                source=excluded.source,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                ttl_days=excluded.ttl_days,
                decay_state=excluded.decay_state,
                last_recalled_at=excluded.last_recalled_at,
                recall_count=excluded.recall_count,
                fingerprint=excluded.fingerprint,
                body_path=excluded.body_path
            """,
            (
                fm.slug,
                fm.type,
                fm.scope_hash,
                fm.title,
                fm.source,
                fm.created_at.isoformat(),
                fm.updated_at.isoformat() if fm.updated_at else None,
                fm.ttl_days,
                fm.decay_state,
                fm.last_recalled_at.isoformat() if fm.last_recalled_at else None,
                fm.recall_count,
                fp,
                body_path,
            ),
        )
        self.conn.execute("DELETE FROM triggers WHERE slug = ?", (fm.slug,))
        if fm.triggers:
            self.conn.executemany(
                "INSERT INTO triggers (slug, trigger) VALUES (?, ?)",
                [(fm.slug, t) for t in fm.triggers],
            )
        self.conn.commit()

    def record_recall(self, slug: str) -> None:
        """Bump recall_count + set last_recalled_at to now (UTC)."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE memories SET recall_count = recall_count + 1, "
            "last_recalled_at = ? WHERE slug = ?",
            (now, slug),
        )
        self.conn.commit()

    def update_decay_state(self, slug: str, state: str) -> None:
        self.conn.execute("UPDATE memories SET decay_state = ? WHERE slug = ?", (state, slug))
        self.conn.commit()

    def delete_memory(self, slug: str) -> None:
        """Cascades to triggers + promotions via FK."""
        self.conn.execute("DELETE FROM memories WHERE slug = ?", (slug,))
        self.conn.commit()

    # -- read side ----------------------------------------------------------

    def get_memory(self, slug: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM memories WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def list_by_type(
        self,
        type_: str,
        *,
        scope_hash: str | None = None,
        include_decayed: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories WHERE type = ?"
        args: list[Any] = [type_]
        if scope_hash is not None:
            sql += " AND scope_hash = ?"
            args.append(scope_hash)
        if not include_decayed:
            sql += " AND decay_state != 'soft-forgotten'"
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def find_by_fingerprint(self, fingerprint: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- sensitive_scopes helpers -------------------------------------------

    def is_scope_sensitive(self, scope_hash: str) -> bool:
        """Return True if scope_hash is registered in sensitive_scopes table."""
        row = self.conn.execute(
            "SELECT 1 FROM sensitive_scopes WHERE scope_hash = ?", (scope_hash,)
        ).fetchone()
        return row is not None

    def register_sensitive_scope(self, scope_hash: str, scope_root: str) -> None:
        """Insert or replace a sensitive scope registration."""
        self.conn.execute(
            "INSERT OR REPLACE INTO sensitive_scopes (scope_hash, scope_root, marked_at) "
            "VALUES (?, ?, datetime('now'))",
            (scope_hash, scope_root),
        )
        self.conn.commit()

    def unregister_sensitive_scope(self, scope_hash: str) -> None:
        """Remove a sensitive scope registration (no-op if missing)."""
        self.conn.execute(
            "DELETE FROM sensitive_scopes WHERE scope_hash = ?", (scope_hash,)
        )
        self.conn.commit()

    def list_sensitive_scopes(self) -> list[dict]:
        """Return all registered sensitive scopes as a list of dicts."""
        rows = self.conn.execute(
            "SELECT scope_hash, scope_root, marked_at FROM sensitive_scopes ORDER BY marked_at"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        self.conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _schema_migrations "
        "(filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {r[0] for r in conn.execute("SELECT filename FROM _schema_migrations").fetchall()}
    for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name in applied:
            continue
        sql = sql_file.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO _schema_migrations (filename, applied_at) VALUES (?, datetime('now'))",
            (sql_file.name,),
        )
    conn.commit()


def open_index(path: Path | None = None) -> Index:
    """Open (creating if needed) the SQLite index and run pending migrations."""
    p = path or _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA foreign_keys = ON")
    _run_migrations(conn)
    return Index(conn)


def rebuild_index(data_root: Path) -> dict:
    """Re-scan all .md files under data_root/scopes/ and refresh SQLite.

    Returns {"indexed": n, "errors": m} dict for visibility. Wipes index.db
    first so stale rows are dropped. The .md walk matches the historical
    behaviour of `memoryd rebuild-index`; sensitive scope .md.enc files are
    left to mark-sensitive / load_session paths, not re-imported here.
    """
    from .storage import load_session  # local import to avoid cycle

    db_path = data_root / "index.db"
    if db_path.exists():
        db_path.unlink()
    idx = open_index(db_path)
    scopes_dir = data_root / "scopes"
    if not scopes_dir.exists():
        idx.close()
        return {"indexed": 0, "errors": 0}
    indexed = 0
    errors = 0
    for md in scopes_dir.rglob("*.md"):
        try:
            mem = load_session(md)
        except Exception:
            errors += 1
            continue
        body_rel = str(md.relative_to(data_root))
        try:
            idx.index_memory(mem, body_path=body_rel)
            indexed += 1
        except Exception:
            errors += 1
    idx.close()
    return {"indexed": indexed, "errors": errors}
