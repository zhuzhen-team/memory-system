"""Cross-device sync via a single self-describing ``memories.json``.

This is *path B* of the memoryd sync story.  Path A (the legacy markdown
mirror) lives in :mod:`memoryd.sync` (the package ``__init__``); this
module adds JSON export / import / diff that is wire-compatible with the
``mcp-memory-service`` v5 export format and adds memoryd extensions
(audit chain, entities/relations, frontmatter, sensitive-scope
ciphertext) that older readers simply skip.

Key behaviours:

* Memories above ``chunk_size_mb`` (default 5 MiB) are spilled into
  ``<out>.chunks/<content_hash>.bin`` with a ``large_file_manifest``
  pointer in the JSON.  This keeps the main JSON cheap to diff.
* Sensitive scopes can opt-in to passphrase encryption: the content
  becomes a ``cipher_blob`` and a top-level ``encryption`` block
  records the KDF parameters.
* The import path is **transactional from the caller's POV**: a single
  SQLite ``conn`` is used; on dry-run nothing is written.
* Conflict resolution delegates to :func:`.conflict.merge_memory_fields`.

The functions accept a ``conn`` (sqlite3 connection produced by
:func:`memoryd.index.open_index().conn`) so callers can wire this into
the existing index without forcing a particular daemon layout.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform as _platform
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from .conflict import merge_memory_fields
from .schema import (
    AuditEntry,
    EntityEntry,
    ExportMetadata,
    MemoriesExport,
    MemoryEntry,
    RelationEntry,
)

log = logging.getLogger(__name__)

EXPORTER_VERSION = "memoryd-1"
DEFAULT_CHUNK_SIZE_MB = 5
_DEFAULT_PBKDF2_ITERS = 600_000
_PBKDF2_SALT_BYTES = 16
_KEYLEN = 32


# ---------------------------------------------------------------------------
# Helpers: schema introspection over existing SQLite index
# ---------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def _row_to_dict(row: Any, cols: list[str]) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return {k: row[k] for k in row.keys()}
    return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# Content hashing + chunk spill
# ---------------------------------------------------------------------------


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _epoch(value: Any) -> float:
    """Coerce ISO-string / datetime / None into unix seconds (UTC).

    Used to normalise ``created_at`` / ``updated_at`` across markdown
    frontmatter (ISO 8601) and v5 (epoch seconds).
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    s = str(value)
    # SQLite stores datetimes as ISO strings; tolerate the trailing 'Z'.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ---------------------------------------------------------------------------
# Encryption (passphrase-mode, dedicated for cross-device transit)
# ---------------------------------------------------------------------------


def _derive_passphrase_key(passphrase: str, salt: bytes, iters: int) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEYLEN,
        salt=salt,
        iterations=iters,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _passphrase_encrypt(passphrase: str, plaintext: bytes, salt: bytes, iters: int) -> str:
    """AES-GCM with a passphrase-derived key.  Returns base64 string."""
    import secrets

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_passphrase_key(passphrase, salt, iters)
    aes = AESGCM(key)
    nonce = secrets.token_bytes(12)
    ct = aes.encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ct).decode("ascii")


def _passphrase_decrypt(passphrase: str, blob_b64: str, salt: bytes, iters: int) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_passphrase_key(passphrase, salt, iters)
    raw = base64.b64decode(blob_b64)
    nonce, ct = raw[:12], raw[12:]
    aes = AESGCM(key)
    return aes.decrypt(nonce, ct, None)


# ---------------------------------------------------------------------------
# Build MemoryEntry rows out of the SQLite index + on-disk markdown
# ---------------------------------------------------------------------------


def _load_memory_body(data_root: Path, body_rel: str, scope_hash: str) -> tuple[str, dict[str, Any]]:
    """Return (body_text, frontmatter_dict) for a memory.

    Sensitive ``.md.enc`` files are decrypted via the existing scope key.
    Plain ``.md`` files are read and split into frontmatter / body.
    """
    path = data_root / body_rel
    if not path.exists():
        return "", {}
    if path.name.endswith(".md.enc"):
        from .. import enc

        plaintext = enc.decrypt_bytes(scope_hash, path.read_bytes()).decode("utf-8")
        text = plaintext
    else:
        text = path.read_text(encoding="utf-8")

    if not text.startswith("---\n"):
        return text, {}
    try:
        _, fm_text, body = text.split("---\n", 2)
    except ValueError:
        return text, {}
    try:
        import yaml

        fm_data = yaml.safe_load(fm_text) or {}
    except Exception:
        fm_data = {}
    return body.lstrip("\n"), fm_data


def _row_to_memory_entry(
    row: dict[str, Any],
    *,
    data_root: Path | None,
    source_machine: str,
    include_frontmatter: bool = True,
) -> MemoryEntry:
    scope_hash = row.get("scope_hash") or ""
    body_path = row.get("body_path") or ""
    body_text = ""
    fm: dict[str, Any] = {}
    if data_root is not None and body_path:
        body_text, fm = _load_memory_body(data_root, body_path, scope_hash)

    title = row.get("title") or fm.get("title") or row.get("slug") or ""
    # Content = title + body (matches what users would re-read).
    content = body_text.strip() if body_text else title

    tags: list[str] = []
    if isinstance(fm.get("tags"), list):
        tags = [str(t) for t in fm["tags"]]
    elif isinstance(row.get("tags"), str) and row["tags"]:
        try:
            tags = list(json.loads(row["tags"]))
        except Exception:
            tags = []

    created_at = _epoch(row.get("created_at"))
    updated_at = _epoch(row.get("updated_at") or row.get("created_at"))

    metadata: dict[str, Any] = {
        "slug": row.get("slug"),
        "title": title,
        "scope_hash": scope_hash,
        "ttl_days": row.get("ttl_days"),
        "decay_state": row.get("decay_state"),
        "recall_count": row.get("recall_count"),
    }

    sensitive = bool(row.get("scope_sensitive"))

    return MemoryEntry(
        content=content,
        content_hash=_sha256_hex(content),
        tags=tags,
        created_at=created_at,
        updated_at=updated_at,
        memory_type=str(row.get("type") or fm.get("type") or "note"),
        metadata=metadata,
        export_source=source_machine,
        id=row.get("slug"),
        scope=scope_hash,
        source=row.get("source"),
        frontmatter=fm if include_frontmatter else None,
        supersedes=fm.get("supersedes") if isinstance(fm.get("supersedes"), list) else None,
        decay_state=row.get("decay_state"),
        sensitive=sensitive,
    )


# ---------------------------------------------------------------------------
# Public API: export
# ---------------------------------------------------------------------------


def export_to_memories_json(
    out_path: Path,
    *,
    conn: sqlite3.Connection,
    data_root: Path | None = None,
    source_machine: str | None = None,
    scope_hashes: list[str] | None = None,
    include_embeddings: bool = False,
    include_audit_chain: bool = True,
    include_entities: bool = True,
    include_profile: bool = True,
    encrypt_sensitive: bool = True,
    passphrase: str | None = None,
    chunk_size_mb: int = DEFAULT_CHUNK_SIZE_MB,
) -> dict[str, Any]:
    """Export the SQLite index + markdown bodies into ``memories.json``.

    Returns a dict ``{exported_count, encrypted_scopes, chunks}``.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    source_machine = source_machine or _platform.node() or "unknown"
    chunk_dir = out_path.with_suffix(out_path.suffix + ".chunks")
    chunk_size_bytes = max(1, chunk_size_mb) * 1024 * 1024

    encryption_block: dict[str, Any] | None = None
    salt: bytes | None = None
    iters = _DEFAULT_PBKDF2_ITERS
    if encrypt_sensitive and passphrase:
        salt = os.urandom(_PBKDF2_SALT_BYTES)
        encryption_block = {
            "scheme": "passphrase-aesgcm",
            "kdf": "pbkdf2-hmac-sha256",
            "iterations": iters,
            "salt": base64.b64encode(salt).decode("ascii"),
        }

    # ---- memories[] ----
    if not _table_exists(conn, "memories"):
        memory_rows: list[dict[str, Any]] = []
    else:
        cols = _column_names(conn, "memories")
        sql = f"SELECT {', '.join(cols)} FROM memories"
        params: list[Any] = []
        if scope_hashes:
            sql += f" WHERE scope_hash IN ({','.join('?' * len(scope_hashes))})"
            params.extend(scope_hashes)
        sql += " ORDER BY created_at"
        memory_rows = [_row_to_dict(r, cols) for r in conn.execute(sql, params).fetchall()]

    entries: list[MemoryEntry] = []
    encrypted_scopes: set[str] = set()
    chunks_written: list[str] = []
    for row in memory_rows:
        entry = _row_to_memory_entry(
            row, data_root=data_root, source_machine=source_machine
        )
        # Sensitive encryption applies only when caller supplied passphrase.
        if entry.sensitive and encrypt_sensitive and passphrase and salt is not None:
            try:
                blob = _passphrase_encrypt(
                    passphrase, entry.content.encode("utf-8"), salt, iters
                )
                entry = entry.model_copy(
                    update={
                        "content": "",
                        "encrypted": True,
                        "cipher_blob": blob,
                    }
                )
                encrypted_scopes.add(entry.scope or "")
            except Exception as e:  # pragma: no cover — encryption is best-effort
                log.warning("encrypt sensitive memory %s failed: %s", entry.id, e)

        # Chunk spill for very large content.
        content_bytes = entry.content.encode("utf-8") if entry.content else b""
        if len(content_bytes) > chunk_size_bytes:
            chunk_name = f"{entry.content_hash}.bin"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            (chunk_dir / chunk_name).write_bytes(content_bytes)
            chunks_written.append(chunk_name)
            entry = entry.model_copy(
                update={
                    "content": "",
                    "large_file_manifest": {
                        "chunk_dir": chunk_dir.name,
                        "filename": chunk_name,
                        "size_bytes": len(content_bytes),
                        "sha256": entry.content_hash,
                    },
                }
            )

        entries.append(entry)

    # ---- entities + relations (best-effort: tables may be absent) ----
    entity_entries: list[EntityEntry] = []
    relation_entries: list[RelationEntry] = []
    if include_entities and _table_exists(conn, "entities"):
        ecols = _column_names(conn, "entities")
        for r in conn.execute(f"SELECT {', '.join(ecols)} FROM entities").fetchall():
            d = _row_to_dict(r, ecols)
            aliases_raw = d.get("aliases")
            aliases: list[str] = []
            if isinstance(aliases_raw, str) and aliases_raw:
                try:
                    aliases = list(json.loads(aliases_raw))
                except Exception:
                    aliases = []
            try:
                entity_entries.append(
                    EntityEntry(
                        id=str(d["id"]),
                        name=str(d.get("name") or d["id"]),
                        type=str(d.get("type") or "concept"),
                        aliases=aliases,
                        first_seen_at=_iso_to_dt(d.get("first_seen_at")),
                        last_seen_at=_iso_to_dt(d.get("last_seen_at")),
                        mention_count=int(d.get("mention_count") or 1),
                    )
                )
            except Exception as e:  # pragma: no cover — defensive
                log.warning("skip malformed entity row: %s", e)

    if include_entities and _table_exists(conn, "relations"):
        rcols = _column_names(conn, "relations")
        for r in conn.execute(f"SELECT {', '.join(rcols)} FROM relations").fetchall():
            d = _row_to_dict(r, rcols)
            try:
                relation_entries.append(
                    RelationEntry(
                        subject_id=str(d["subject_id"]),
                        predicate=str(d["predicate"]),
                        object_id=str(d["object_id"]),
                        source_memory_id=d.get("source_memory_id"),
                        confidence=d.get("confidence"),
                        created_at=_iso_to_dt(d.get("created_at")),
                    )
                )
            except Exception as e:  # pragma: no cover
                log.warning("skip malformed relation row: %s", e)

    # ---- identity_snapshot (latest profile_versions.content_md) ----
    identity_snapshot: str | None = None
    if include_profile and _table_exists(conn, "profile_versions"):
        row = conn.execute(
            "SELECT content_md FROM profile_versions ORDER BY version_num DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            identity_snapshot = row[0] if not isinstance(row, sqlite3.Row) else row["content_md"]

    # ---- audit_chain ----
    audit_entries: list[AuditEntry] = []
    audit_head: str | None = None
    if include_audit_chain:
        audit_entries, audit_head = _load_audit_chain()

    meta = ExportMetadata(
        source_machine=source_machine,
        export_timestamp=datetime.now(timezone.utc),
        total_memories=len(entries),
        database_path=str(_safe_db_path(conn)),
        platform=_platform.system(),
        python_version=sys.version.split()[0],
        exporter_version=EXPORTER_VERSION,
        include_embeddings=include_embeddings,
        include_audit_chain=include_audit_chain,
        include_entities=include_entities,
        include_profile=include_profile,
        audit_chain_head=audit_head,
        encryption=encryption_block,
    )

    bundle = MemoriesExport(
        export_metadata=meta,
        memories=entries,
        entities=entity_entries,
        relations=relation_entries,
        identity_snapshot=identity_snapshot,
        audit_chain=audit_entries,
    )

    # Pydantic serialises datetimes to ISO strings via mode='json'.
    out_path.write_text(
        json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "exported_count": len(entries),
        "encrypted_scopes": sorted(encrypted_scopes),
        "chunks": chunks_written,
    }


def _safe_db_path(conn: sqlite3.Connection) -> str:
    """Best-effort recovery of the underlying SQLite file path."""
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        if row is not None:
            return str(row[2]) if not isinstance(row, sqlite3.Row) else str(row["file"])
    except Exception:
        pass
    return ""


def _iso_to_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None:
        return datetime.now(timezone.utc)
    s = str(value)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_audit_chain() -> tuple[list[AuditEntry], str | None]:
    """Pull the audit.jsonl rows; tolerate missing file."""
    try:
        from ..governance import audit as audit_mod
    except Exception:
        return [], None
    p = audit_mod.audit_log_path()
    if not p.exists():
        return [], None
    entries: list[AuditEntry] = []
    last_hash: str | None = None
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        this_hash = audit_mod._hash_for_chain(ev)
        try:
            entries.append(
                AuditEntry(
                    seq=ev.get("seq", i),
                    ts=_iso_to_dt(ev.get("ts")),
                    action=str(ev.get("event_type") or ev.get("action") or "unknown"),
                    target_id=ev.get("target_id") or ev.get("slug"),
                    details=ev.get("details") or {
                        k: v
                        for k, v in ev.items()
                        if k not in ("ts", "prev_hash", "event_type", "action", "target_id")
                    },
                    prev_hash=ev.get("prev_hash"),
                    this_hash=this_hash,
                )
            )
            last_hash = this_hash
        except Exception:  # pragma: no cover
            continue
    return entries, last_hash


# ---------------------------------------------------------------------------
# Public API: import
# ---------------------------------------------------------------------------


def _load_export(in_path: Path) -> MemoriesExport:
    raw = json.loads(Path(in_path).read_text(encoding="utf-8"))
    if "export_metadata" not in raw:
        raise ValueError(f"{in_path}: missing export_metadata block")
    # If memories[] is absent, default to empty so v5 minimum bundles work.
    raw.setdefault("memories", [])
    return MemoriesExport.model_validate(raw)


def _resolve_chunk(entry: MemoryEntry, in_path: Path) -> str:
    """If entry was chunk-spilled, read the chunk back into memory."""
    if not entry.large_file_manifest:
        return entry.content
    manifest = entry.large_file_manifest
    chunk_dir_name = manifest.get("chunk_dir") or (in_path.name + ".chunks")
    filename = manifest.get("filename")
    if not filename:
        return entry.content
    p = in_path.parent / chunk_dir_name / filename
    if not p.exists():
        log.warning("missing chunk file %s", p)
        return entry.content
    return p.read_bytes().decode("utf-8")


def _decrypt_entry_if_needed(
    entry: MemoryEntry,
    encryption: dict[str, Any] | None,
    passphrase: str | None,
) -> MemoryEntry:
    if not entry.encrypted or not entry.cipher_blob:
        return entry
    if not encryption or not passphrase:
        # Can't decrypt — keep ciphertext, leave content empty.
        return entry
    salt_b64 = encryption.get("salt")
    iters = int(encryption.get("iterations") or _DEFAULT_PBKDF2_ITERS)
    if not salt_b64:
        return entry
    salt = base64.b64decode(salt_b64)
    try:
        plain = _passphrase_decrypt(passphrase, entry.cipher_blob, salt, iters).decode("utf-8")
    except Exception as e:
        log.warning("decrypt %s failed: %s", entry.id, e)
        return entry
    return entry.model_copy(update={"content": plain, "encrypted": False, "cipher_blob": None})


def _ensure_import_table(conn: sqlite3.Connection) -> None:
    """Lazy-create a tiny side table to track which JSON memories landed.

    We don't try to back-fill the full markdown-on-disk story here — we
    just record the incoming entry so callers (or a follow-up
    `rebuild-index`) can materialise files.  The index's primary
    ``memories`` table is updated when possible (slug + scope hash known).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_imported_memories (
            id          TEXT PRIMARY KEY,
            scope_hash  TEXT,
            content     TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            updated_at  REAL NOT NULL,
            tags_json   TEXT,
            metadata_json TEXT,
            merge_notes TEXT,
            device_id   TEXT,
            imported_at TEXT NOT NULL
        )
        """
    )


def _upsert_imported(
    conn: sqlite3.Connection, entry: MemoryEntry, *, device_id: str
) -> None:
    conn.execute(
        """
        INSERT INTO sync_imported_memories
            (id, scope_hash, content, content_hash, updated_at, tags_json,
             metadata_json, merge_notes, device_id, imported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            scope_hash=excluded.scope_hash,
            content=excluded.content,
            content_hash=excluded.content_hash,
            updated_at=excluded.updated_at,
            tags_json=excluded.tags_json,
            metadata_json=excluded.metadata_json,
            merge_notes=excluded.merge_notes,
            device_id=excluded.device_id,
            imported_at=excluded.imported_at
        """,
        (
            entry.id or entry.content_hash,
            entry.scope or "",
            entry.content,
            entry.content_hash,
            float(entry.updated_at or 0.0),
            json.dumps(entry.tags, ensure_ascii=False),
            json.dumps(entry.metadata, ensure_ascii=False),
            "\n".join(entry.merge_notes or []),
            device_id,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _local_entry_for(conn: sqlite3.Connection, entry: MemoryEntry) -> MemoryEntry | None:
    """Return a MemoryEntry built from the local index for the same id."""
    if not _table_exists(conn, "memories"):
        return None
    cols = _column_names(conn, "memories")
    sel = f"SELECT {', '.join(cols)} FROM memories WHERE slug = ?"
    row = conn.execute(sel, (entry.id,)).fetchone()
    if row is None:
        # Try sync_imported_memories side table.
        if _table_exists(conn, "sync_imported_memories"):
            r = conn.execute(
                "SELECT id, scope_hash, content, content_hash, updated_at, tags_json, "
                "metadata_json, merge_notes FROM sync_imported_memories WHERE id = ?",
                (entry.id,),
            ).fetchone()
            if r is not None:
                rid, scope_h, content, ch, upd, tags_j, meta_j, notes = r
                return MemoryEntry(
                    content=content,
                    content_hash=ch,
                    tags=json.loads(tags_j or "[]"),
                    created_at=float(upd or 0.0),
                    updated_at=float(upd or 0.0),
                    memory_type="note",
                    metadata=json.loads(meta_j or "{}"),
                    export_source="local",
                    id=rid,
                    scope=scope_h,
                    merge_notes=[n for n in (notes or "").split("\n") if n] or None,
                )
        return None
    d = _row_to_dict(row, cols)
    return MemoryEntry(
        content=d.get("title") or "",
        content_hash=str(d.get("fingerprint") or ""),
        tags=[],
        created_at=_epoch(d.get("created_at")),
        updated_at=_epoch(d.get("updated_at") or d.get("created_at")),
        memory_type=str(d.get("type") or "note"),
        metadata={
            "slug": d.get("slug"),
            "title": d.get("title"),
            "scope_hash": d.get("scope_hash"),
            "decay_state": d.get("decay_state"),
        },
        export_source="local",
        id=d.get("slug"),
        scope=d.get("scope_hash"),
        source=d.get("source"),
        decay_state=d.get("decay_state"),
        sensitive=bool(d.get("scope_sensitive")),
    )


def import_from_memories_json(
    in_path: Path,
    *,
    conn: sqlite3.Connection,
    device_id: str | None = None,
    passphrase: str | None = None,
    conflict_strategy: Literal["merge", "prefer-local", "prefer-remote", "ask"] = "merge",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import a ``memories.json`` bundle.

    Returns ``{imported, conflicts_resolved, merged, skipped}``.
    """
    in_path = Path(in_path)
    bundle = _load_export(in_path)
    device_id = device_id or str(uuid.getnode())

    encryption = bundle.export_metadata.encryption

    audit_chain = list(bundle.audit_chain)

    imported = 0
    skipped = 0
    merged = 0
    conflicts_resolved = 0

    if not dry_run:
        _ensure_import_table(conn)

    for entry in bundle.memories:
        # Restore chunk-spilled content if needed.
        if entry.large_file_manifest:
            full = _resolve_chunk(entry, in_path)
            entry = entry.model_copy(update={"content": full})

        # Decrypt sensitive entries when caller supplied the passphrase.
        if entry.encrypted:
            entry = _decrypt_entry_if_needed(entry, encryption, passphrase)

        local = _local_entry_for(conn, entry) if entry.id else None

        if local is None:
            # Brand new on this device.
            imported += 1
            if not dry_run:
                _upsert_imported(conn, entry, device_id=device_id)
            continue

        # We have a local twin → decide.
        if conflict_strategy == "prefer-local":
            skipped += 1
            continue
        if conflict_strategy == "prefer-remote":
            conflicts_resolved += 1
            if not dry_run:
                _upsert_imported(conn, entry, device_id=device_id)
            continue
        if conflict_strategy == "ask":
            # Not interactive in this layer — record as merged with a note.
            note_entry = entry.model_copy(
                update={"merge_notes": (entry.merge_notes or []) + ["pending-user-decision"]}
            )
            conflicts_resolved += 1
            if not dry_run:
                _upsert_imported(conn, note_entry, device_id=device_id)
            continue

        # Default: three-way merge.
        merged_entry = merge_memory_fields(local, entry, audit_chain)
        if merged_entry.content == local.content and merged_entry.tags == local.tags:
            skipped += 1
            continue
        merged += 1
        conflicts_resolved += 1
        if not dry_run:
            _upsert_imported(conn, merged_entry, device_id=device_id)

    if not dry_run:
        conn.commit()

    return {
        "imported": imported,
        "conflicts_resolved": conflicts_resolved,
        "merged": merged,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Public API: diff
# ---------------------------------------------------------------------------


def diff_with_remote(
    remote_path: Path,
    *,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Preview what an import would do without touching the database."""
    bundle = _load_export(Path(remote_path))
    new_ids: list[str] = []
    updates: list[str] = []
    conflicts: list[str] = []
    unchanged: list[str] = []

    for entry in bundle.memories:
        local = _local_entry_for(conn, entry) if entry.id else None
        eid = entry.id or entry.content_hash
        if local is None:
            new_ids.append(eid)
            continue
        if local.content_hash == entry.content_hash:
            unchanged.append(eid)
            continue
        # Differ — is it a conflict (both sides moved)?
        if local.updated_at > entry.updated_at:
            conflicts.append(eid)
        elif entry.updated_at > local.updated_at:
            updates.append(eid)
        else:
            conflicts.append(eid)

    return {
        "new": new_ids,
        "updates": updates,
        "conflicts": conflicts,
        "unchanged": unchanged,
        "remote_total": len(bundle.memories),
        "exporter_version": bundle.export_metadata.exporter_version,
        "schema_compat": list(bundle.export_metadata.schema_compat),
    }


__all__ = [
    "export_to_memories_json",
    "import_from_memories_json",
    "diff_with_remote",
    "EXPORTER_VERSION",
]
