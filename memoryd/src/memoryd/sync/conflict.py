"""LWW field-level merge with audit-chain replay.

The strategy:

1. Walk the supplied ``audit_chain`` in ascending ``seq`` order and treat
   each ``update`` / ``create`` event whose ``target_id`` matches one of
   the two candidates as a "write".  The last write per field wins
   (Last-Write-Wins).
2. After the audit replay, fall back to the two candidate ``MemoryEntry``
   objects directly using ``updated_at`` as the LWW tie-breaker.
3. Special-case three fields:
   - ``content``: if both sides changed it to different values *and* the
     audit chain cannot decide a winner, keep the side with the larger
     ``updated_at`` and append a human-readable note to ``merge_notes``.
   - ``tags``: set union (order-preserving, lower-case key match).
   - ``relations``: list union keyed by
     ``(subject_id, predicate, object_id)`` tuple.

The result is a brand new ``MemoryEntry`` — neither input is mutated.
"""

from __future__ import annotations

from typing import Any

from .schema import AuditEntry, MemoryEntry


def _dedup_preserve_order(items: list[str]) -> list[str]:
    """Stable de-dup while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _merge_tags(local: list[str], remote: list[str]) -> list[str]:
    return _dedup_preserve_order(list(local) + list(remote))


def _relation_key(rel: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (rel.get("subject_id"), rel.get("predicate"), rel.get("object_id"))


def _merge_relations(
    local: list[dict[str, Any]] | None,
    remote: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if not local and not remote:
        return local if local is not None else remote
    seen: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for rel in (local or []) + (remote or []):
        key = _relation_key(rel)
        # Later occurrence wins (matches LWW intent for individual rel rows).
        seen[key] = rel
    return list(seen.values())


def _audit_writes_for(
    target_id: str | None, chain: list[AuditEntry]
) -> list[AuditEntry]:
    """Return audit entries that wrote to ``target_id``, in seq order."""
    if not target_id:
        return []
    rows = [
        e
        for e in chain
        if e.target_id == target_id and e.action in ("create", "update", "merge")
    ]
    rows.sort(key=lambda e: (e.seq, e.ts))
    return rows


def _replay_field_winners(
    target_id: str | None, chain: list[AuditEntry]
) -> dict[str, AuditEntry]:
    """For each field touched by an audit event, find the latest writer."""
    winners: dict[str, AuditEntry] = {}
    for ev in _audit_writes_for(target_id, chain):
        changed = ev.details.get("changed_fields") or list(
            (ev.details.get("after") or {}).keys()
        )
        for f in changed:
            winners[f] = ev
    return winners


def merge_memory_fields(
    local: MemoryEntry,
    remote: MemoryEntry,
    audit_chain: list[AuditEntry] | None = None,
) -> MemoryEntry:
    """Three-way merge of two ``MemoryEntry`` instances.

    Args:
        local:   The local-side memory (kept on this device).
        remote:  The remote-side memory (just imported).
        audit_chain: Optional combined audit chain across devices, used
            for field-level LWW.  When absent or silent on a field,
            ``updated_at`` is the tie-breaker.

    Returns:
        A fresh ``MemoryEntry`` representing the merged state.
    """
    audit_chain = audit_chain or []
    target_id = local.id or remote.id
    winners = _replay_field_winners(target_id, audit_chain)

    out_dict: dict[str, Any] = local.model_dump()
    notes: list[str] = list(local.merge_notes or [])

    # Plain LWW for scalar / blob fields.
    scalar_fields = (
        "content",
        "content_hash",
        "memory_type",
        "scope",
        "source",
        "decay_state",
        "sensitive",
        "encrypted",
        "cipher_blob",
        "frontmatter",
        "metadata",
        "id",
    )
    for f in scalar_fields:
        local_val = getattr(local, f, None)
        remote_val = getattr(remote, f, None)
        if local_val == remote_val:
            out_dict[f] = local_val
            continue
        win = winners.get(f)
        if win is not None:
            # Audit decides: pick whichever side matches the latest writer.
            after = win.details.get("after") or {}
            if f in after:
                out_dict[f] = after[f]
            else:
                # Audit named the field but didn't carry the value — fall
                # back to updated_at LWW.
                out_dict[f] = (
                    remote_val if remote.updated_at >= local.updated_at else local_val
                )
        else:
            out_dict[f] = (
                remote_val if remote.updated_at >= local.updated_at else local_val
            )

    # updated_at = max of both sides.
    out_dict["updated_at"] = max(local.updated_at, remote.updated_at)
    # created_at = min (earlier creation wins).
    out_dict["created_at"] = min(local.created_at, remote.created_at)

    # Set-union merges.
    out_dict["tags"] = _merge_tags(local.tags, remote.tags)
    merged_rels = _merge_relations(local.relations, remote.relations)
    if merged_rels is not None:
        out_dict["relations"] = merged_rels

    # Entity list: simple ordered union.
    if local.entities is not None or remote.entities is not None:
        out_dict["entities"] = _dedup_preserve_order(
            (local.entities or []) + (remote.entities or [])
        )

    # supersedes / superseded_by: ordered union.
    for f in ("supersedes", "superseded_by"):
        lv = getattr(local, f) or []
        rv = getattr(remote, f) or []
        if lv or rv:
            out_dict[f] = _dedup_preserve_order(list(lv) + list(rv))

    # Content conflict note when both diverge from the older version.
    if local.content != remote.content:
        win = winners.get("content")
        if win is None:
            notes.append(
                "content-conflict: kept "
                f"{'remote' if remote.updated_at >= local.updated_at else 'local'} "
                f"(local_updated_at={local.updated_at}, remote_updated_at={remote.updated_at})"
            )
        else:
            notes.append(f"content-conflict: audit seq={win.seq} decided")

    if notes:
        out_dict["merge_notes"] = notes

    return MemoryEntry(**out_dict)


__all__ = ["merge_memory_fields"]
