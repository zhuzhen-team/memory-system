"""Pydantic schema for ``memories.json`` cross-device sync format.

Path B of the sync story: a single self-describing JSON document that
travels between devices (USB stick, Syncthing, manual export).  The
schema is wire-compatible with `mcp-memory-service` v5 (subset of
``memories[]`` fields) and adds memoryd-specific extension fields that
older tools simply ignore.

The schema is intentionally permissive on input — unknown extra keys
are allowed so future memoryd versions can grow new fields without
breaking older readers.  Output is strict and canonical.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


_SCHEMA_COMPAT = ["mcp-memory-v5", "memoryd-1"]


class ExportMetadata(BaseModel):
    """Top-level provenance for a single export bundle."""

    model_config = ConfigDict(extra="allow")

    source_machine: str
    export_timestamp: datetime
    total_memories: int
    database_path: str
    platform: str
    python_version: str
    exporter_version: str = "memoryd-1"
    schema_compat: list[str] = Field(default_factory=lambda: list(_SCHEMA_COMPAT))
    include_embeddings: bool = False
    include_audit_chain: bool = True
    include_entities: bool = True
    include_profile: bool = True
    audit_chain_head: str | None = None
    encryption: dict[str, Any] | None = None
    # mcp-memory-v5 also writes filter_tags; preserve when round-tripping.
    filter_tags: list[str] | None = None


class MemoryEntry(BaseModel):
    """One memory row.  Fields above the divider are v5-compatible."""

    model_config = ConfigDict(extra="allow")

    # ---- v5 compatibility surface (mcp-memory-service readers see these) -
    content: str
    content_hash: str
    tags: list[str] = Field(default_factory=list)
    created_at: float
    updated_at: float
    memory_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    export_source: str

    # ---- memoryd extensions (older readers will silently skip) ----------
    id: str | None = None
    scope: str | None = None
    source: str | None = None
    frontmatter: dict[str, Any] | None = None
    entities: list[str] | None = None
    relations: list[dict[str, Any]] | None = None
    supersedes: list[str] | None = None
    superseded_by: list[str] | None = None
    decay_state: str | None = None
    sensitive: bool = False
    encrypted: bool = False
    cipher_blob: str | None = None
    # Chunked spill-over manifest for memories > chunk_size_mb.
    large_file_manifest: dict[str, Any] | None = None
    # Free-form merge notes from conflict resolution.
    merge_notes: list[str] | None = None


class EntityEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    type: str
    aliases: list[str] = Field(default_factory=list)
    first_seen_at: datetime
    last_seen_at: datetime
    mention_count: int


class RelationEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    subject_id: str
    predicate: str
    object_id: str
    source_memory_id: str | None = None
    confidence: float | None = None
    created_at: datetime


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    seq: int
    ts: datetime
    action: str
    target_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str | None = None
    this_hash: str


class MemoriesExport(BaseModel):
    """Root document.  Matches v5 at the top level (``export_metadata`` +
    ``memories``) and grows extra arrays that older tools ignore."""

    model_config = ConfigDict(extra="allow")

    export_metadata: ExportMetadata
    memories: list[MemoryEntry] = Field(default_factory=list)
    entities: list[EntityEntry] = Field(default_factory=list)
    relations: list[RelationEntry] = Field(default_factory=list)
    identity_snapshot: str | None = None
    audit_chain: list[AuditEntry] = Field(default_factory=list)


__all__ = [
    "ExportMetadata",
    "MemoryEntry",
    "EntityEntry",
    "RelationEntry",
    "AuditEntry",
    "MemoriesExport",
]
