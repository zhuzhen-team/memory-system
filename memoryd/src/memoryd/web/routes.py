"""Browse-only routes for the memoryd web dashboard."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    data_root = request.app.state.data_root
    recent = _recent(data_root, limit=20)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "recent": recent,
            "token": request.app.state.token,
        },
    )


@router.get("/memories", response_class=HTMLResponse)
async def list_memories(
    request: Request,
    type: str | None = None,
    scope: str | None = None,
    page: int = 1,
):
    data_root = request.app.state.data_root
    items = _list_memories(data_root, type=type, scope=scope, page=page)
    return request.app.state.templates.TemplateResponse(
        request,
        "list.html",
        {
            "items": items,
            "type": type,
            "scope": scope,
            "page": page,
            "token": request.app.state.token,
        },
    )


@router.get("/memories/{slug}", response_class=HTMLResponse)
async def detail(request: Request, slug: str):
    data_root = request.app.state.data_root
    info = _resolve_memory(data_root, slug)
    if info is None:
        raise HTTPException(404, detail="not found")
    if info["sensitive"]:
        raise HTTPException(403, detail="sensitive scope; use CLI")
    return request.app.state.templates.TemplateResponse(
        request,
        "detail.html",
        {
            "memory": info,
            "token": request.app.state.token,
        },
    )


# --- helpers ---

def _recent(data_root: Path, limit: int):
    """List recent memories across all scopes (raw .md only)."""
    from memoryd.scope_meta import is_path_sensitive
    items: list[dict] = []
    scopes = data_root / "scopes"
    if not scopes.exists():
        return items
    for md in scopes.rglob("*.md"):
        if md.name.startswith("."):
            continue
        # ignore _conflicts/ entries (Plan 6)
        parts = md.relative_to(scopes).parts
        if not parts or parts[0].startswith("_"):
            continue
        scope_hash = parts[0]
        # type inferred from parent dir: scopes/<hash>/<type>/<slug>.md
        # for free-form files directly under <hash>, type = "memory"
        type_ = parts[1] if len(parts) >= 3 else "memory"
        slug = md.stem
        sensitive = is_path_sensitive(md.parent)
        items.append({
            "slug": slug,
            "type": type_,
            "scope_hash": scope_hash,
            "title": slug,
            "sensitive": sensitive,
            "path": str(md),
        })
    items.sort(key=lambda x: x["slug"], reverse=True)
    return items[:limit]


def _list_memories(data_root: Path, *, type=None, scope=None,
                   page=1, per_page=50):
    all_ = _recent(data_root, limit=10_000)
    if type:
        all_ = [x for x in all_ if x["type"] == type]
    if scope:
        all_ = [x for x in all_ if x["scope_hash"] == scope]
    start = (page - 1) * per_page
    return all_[start : start + per_page]


def _resolve_memory(data_root: Path, slug: str) -> dict | None:
    """Find a .md by slug across all scopes/types."""
    from memoryd.scope_meta import is_path_sensitive
    scopes = data_root / "scopes"
    if not scopes.exists():
        return None
    for md in scopes.rglob(f"{slug}.md"):
        parts = md.relative_to(scopes).parts
        if not parts or parts[0].startswith("_"):
            continue
        sensitive = is_path_sensitive(md.parent)
        if sensitive:
            return {"slug": slug, "sensitive": True, "path": str(md)}
        text = md.read_text(encoding="utf-8")
        return {"slug": slug, "sensitive": False, "path": str(md), "body": text}
    return None


@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = "", type: str | None = None):
    data_root = request.app.state.data_root
    templates = request.app.state.templates
    if not q.strip():
        return templates.TemplateResponse(
            request,
            "search_fragment.html",
            {"hits": [], "token": request.app.state.token},
        )
    hits = _search_local(data_root, q, type=type, limit=50)
    return templates.TemplateResponse(
        request,
        "search_fragment.html",
        {"hits": hits, "token": request.app.state.token},
    )


@router.get("/audit", response_class=HTMLResponse)
async def audit(
    request: Request,
    scope: str | None = None,
    since: str | None = None,
    event_type: str | None = None,
):
    data_root = request.app.state.data_root
    entries = _read_audit(
        data_root, scope=scope, since=since, event_type=event_type, limit=200
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "audit.html",
        {
            "entries": entries,
            "scope": scope,
            "since": since,
            "event_type": event_type,
            "token": request.app.state.token,
        },
    )


@router.get("/digest", response_class=HTMLResponse)
async def digest(request: Request):
    data_root = request.app.state.data_root
    items = _list_pending_promotions(data_root)
    return request.app.state.templates.TemplateResponse(
        request,
        "digest.html",
        {"items": items, "token": request.app.state.token},
    )


def _search_local(data_root: Path, q: str, *, type=None, limit=50):
    """Best-effort search; grep .md body, skip sensitive scopes."""
    from memoryd.scope_meta import is_path_sensitive
    hits: list[dict] = []
    scopes = data_root / "scopes"
    if not scopes.exists():
        return hits
    q_lower = q.lower()
    for md in scopes.rglob("*.md"):
        parts = md.relative_to(scopes).parts
        if not parts or parts[0].startswith("_"):
            continue
        if type and (len(parts) < 3 or parts[1] != type):
            continue
        if is_path_sensitive(md.parent):
            # don't leak sensitive body in search
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue
        if q_lower in text.lower():
            idx = text.lower().find(q_lower)
            start = max(0, idx - 40)
            end = min(len(text), idx + len(q) + 40)
            excerpt = text[start:end].replace("\n", " ")
            hits.append({
                "slug": md.stem,
                "scope_hash": parts[0],
                "excerpt": excerpt,
            })
            if len(hits) >= limit:
                break
    return hits


def _read_audit(
    data_root: Path,
    *,
    scope=None,
    since=None,
    event_type=None,
    limit=200,
):
    import json
    f = data_root / "audit" / "audit.jsonl"
    if not f.exists():
        return []
    entries: list[dict] = []
    for line in reversed(f.read_text("utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if scope and d.get("scope_hash") != scope:
            continue
        if since and d.get("ts", "") < since:
            continue
        if event_type and d.get("event_type") != event_type:
            continue
        entries.append(d)
        if len(entries) >= limit:
            break
    return entries


def _list_pending_promotions(data_root: Path):
    """Best-effort SQLite query; return [] if db absent or schema mismatched."""
    db = data_root / "index.db"
    if not db.exists():
        return []
    import sqlite3
    try:
        conn = sqlite3.connect(str(db))
    except Exception:
        return []
    try:
        rows = conn.execute(
            "SELECT id, source_session_slug, proposed_type, proposed_title, "
            "       reasoning, status FROM promotions WHERE status = 'pending' "
            "ORDER BY id DESC LIMIT 200"
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    cols = [
        "id",
        "source_session_slug",
        "proposed_type",
        "proposed_title",
        "reasoning",
        "status",
    ]
    return [dict(zip(cols, r)) for r in rows]
