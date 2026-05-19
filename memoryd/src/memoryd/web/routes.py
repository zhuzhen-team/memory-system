"""Browse-only routes for the memoryd web dashboard.

历史路由（保持向后兼容）：

- ``/`` 仪表板首页（已改写为"最近记忆 + 统计入口"）
- ``/memories`` / ``/memories/<slug>`` 列表 / 详情
- ``/search`` 全文搜索（HTMX 局部刷新）
- ``/audit`` 审计日志
- ``/digest`` 待审促进队列

Plan 11 新增：

- ``/relations`` / ``/relations/entity/<id>`` 知识图谱页（Cytoscape.js）
- ``/api/graph/global`` / ``/api/graph/<entity_id>`` 子图 JSON
- ``/trends`` 趋势页 + ``/api/trends/...`` JSON
- ``/identity`` 用户画像 + ``/identity/version/<n>`` / ``/identity/diff``
- ``/api/identity/report/<period>`` 月度报告 markdown
- ``/htmx/memory-list`` HTMX 局部刷新片段

所有 API 路由如果上游模块未就绪（KG / Profile 缺表 / 缺包），返回友好降级
而非 500——保持 web 端的"可观察性优先"。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

router = APIRouter()


# ===========================================================================
# 1. 首页（仪表板）
# ===========================================================================


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """仪表板首页 —— 最近记忆 + 统计入口 + 各页面跳转。"""
    templates = request.app.state.templates
    data_root: Path = request.app.state.data_root
    recent = _recent(data_root, limit=20)
    stats = _gather_stats(data_root)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "recent": recent,
            "stats": stats,
            "token": request.app.state.token,
        },
    )


def _gather_stats(data_root: Path) -> dict[str, Any]:
    """汇总 SQLite 里的几个轻量计数 + 文件系统统计，供首页展示。

    任何子查询失败都降级为 0，确保首页永远能渲染。
    """
    stats: dict[str, Any] = {
        "scope_count": 0,
        "memory_count": 0,
        "entity_count": 0,
        "relation_count": 0,
        "profile_versions": 0,
        "pending_promotions": 0,
        "has_identity": False,
    }
    scopes = data_root / "scopes"
    if scopes.exists():
        stats["scope_count"] = sum(
            1
            for p in scopes.iterdir()
            if p.is_dir() and not p.name.startswith("_")
        )
        stats["memory_count"] = sum(1 for _ in scopes.rglob("*.md"))

    db = data_root / "index.db"
    if db.exists():
        try:
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            for table, key in (
                ("entities", "entity_count"),
                ("relations", "relation_count"),
                ("profile_versions", "profile_versions"),
            ):
                try:
                    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                    if row:
                        stats[key] = int(row[0] or 0)
                except sqlite3.Error:
                    continue
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM promotions WHERE status='pending'"
                ).fetchone()
                if row:
                    stats["pending_promotions"] = int(row[0] or 0)
            except sqlite3.Error:
                pass
            conn.close()
        except sqlite3.Error:
            pass

    # 检查 identity.md 是否存在（profile/identity.md，按 evolution.py 约定）
    identity_file = data_root / "profile" / "identity.md"
    stats["has_identity"] = identity_file.exists()
    return stats


# ===========================================================================
# 2. 列表 / 详情 / 搜索（继承自 plan7，保持稳定）
# ===========================================================================


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


@router.get("/htmx/memory-list", response_class=HTMLResponse)
async def htmx_memory_list(
    request: Request,
    type: str | None = None,
    scope: str | None = None,
    page: int = 1,
):
    """HTMX 局部刷新：仅返回 <ul> 片段。给 list / index 页过滤器复用。"""
    data_root = request.app.state.data_root
    items = _list_memories(data_root, type=type, scope=scope, page=page)
    return request.app.state.templates.TemplateResponse(
        request,
        "fragments/memory_list.html",
        {
            "items": items,
            "token": request.app.state.token,
            "page": page,
        },
    )


# ===========================================================================
# 3. 审计 / 摘要（继承自 plan7）
# ===========================================================================


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


# ===========================================================================
# 4. 关系图页（Plan 11 新增）
# ===========================================================================


@router.get("/relations", response_class=HTMLResponse)
async def relations(
    request: Request,
    scope: str | None = None,
    depth: int = 2,
    window_days: int = 30,
):
    """关系图页 —— 默认显示全局 top 实体子图。"""
    templates = request.app.state.templates
    data_root = request.app.state.data_root
    scopes = _list_known_scopes(data_root)
    available = _kg_available(data_root)
    return templates.TemplateResponse(
        request,
        "relations.html",
        {
            "scopes": scopes,
            "scope": scope or "",
            "depth": max(1, min(int(depth or 2), 3)),
            "window_days": int(window_days or 30),
            "available": available,
            "focus_entity": None,
            "token": request.app.state.token,
        },
    )


@router.get("/relations/entity/{entity_id:path}", response_class=HTMLResponse)
async def relations_entity(
    request: Request,
    entity_id: str,
    depth: int = 2,
):
    """聚焦到某个 entity 的关系图视图。"""
    templates = request.app.state.templates
    data_root = request.app.state.data_root
    scopes = _list_known_scopes(data_root)
    available = _kg_available(data_root)
    return templates.TemplateResponse(
        request,
        "relations.html",
        {
            "scopes": scopes,
            "scope": "",
            "depth": max(1, min(int(depth or 2), 3)),
            "window_days": 30,
            "available": available,
            "focus_entity": entity_id,
            "token": request.app.state.token,
        },
    )


@router.get("/api/graph/global")
async def api_graph_global(
    request: Request,
    scope: str | None = None,
    depth: int = 2,
    window_days: int = 30,
    top_k: int = 15,
    type: str | None = None,  # noqa: A002
):
    """返回全局 top 实体的合并子图（cytoscape elements）。"""
    data_root = request.app.state.data_root
    if not _kg_available(data_root):
        return JSONResponse({"elements": [], "available": False})
    try:
        from memoryd.knowledge_graph import (
            KnowledgeGraphStore,
            n_hop_subgraph,
            to_cytoscape_elements,
        )
        import networkx as nx
    except ImportError:
        return JSONResponse({"elements": [], "available": False})

    elements: list[dict] = []
    conn = _open_kg_conn(data_root)
    if conn is None:
        return JSONResponse({"elements": [], "available": False})
    try:
        store = KnowledgeGraphStore(conn)
        # 按 mention_count 取 top_k 个 entity，然后合并它们的 N-hop 子图
        top_entities = store.top_entities(
            scope_hash=scope or None,
            window_days=window_days,
            top_k=top_k,
        )
        if type:
            top_entities = [e for e in top_entities if e.type == type]
        if not top_entities:
            return JSONResponse({"elements": [], "available": True})
        merged: nx.DiGraph = nx.DiGraph()
        for ent in top_entities:
            sub = n_hop_subgraph(store, ent.id, depth=max(1, min(depth, 3)))
            for n, attrs in sub.nodes(data=True):
                if n not in merged.nodes:
                    merged.add_node(n, **attrs)
            for s, d, attrs in sub.edges(data=True):
                merged.add_edge(s, d, **attrs)
        elements = to_cytoscape_elements(merged)
    finally:
        conn.close()
    return JSONResponse({"elements": elements, "available": True})


@router.get("/api/graph/{entity_id:path}")
async def api_graph_entity(
    request: Request,
    entity_id: str,
    depth: int = 1,
):
    """单个实体周围 N 跳子图。"""
    data_root = request.app.state.data_root
    if not _kg_available(data_root):
        return JSONResponse({"elements": [], "available": False})
    try:
        from memoryd.knowledge_graph import (
            KnowledgeGraphStore,
            n_hop_subgraph,
            to_cytoscape_elements,
        )
    except ImportError:
        return JSONResponse({"elements": [], "available": False})

    conn = _open_kg_conn(data_root)
    if conn is None:
        return JSONResponse({"elements": [], "available": False})
    try:
        store = KnowledgeGraphStore(conn)
        ent = store.get_entity(entity_id)
        if ent is None:
            return JSONResponse({"elements": [], "available": True, "entity": None})
        g = n_hop_subgraph(store, entity_id, depth=max(1, min(depth, 3)))
        elements = to_cytoscape_elements(g)
        return JSONResponse(
            {
                "elements": elements,
                "available": True,
                "entity": {
                    "id": ent.id,
                    "name": ent.name,
                    "type": ent.type,
                    "mention_count": ent.mention_count,
                    "decay_state": ent.decay_state,
                },
            }
        )
    finally:
        conn.close()


# ===========================================================================
# 5. 趋势页（Plan 11 新增）
# ===========================================================================


@router.get("/trends", response_class=HTMLResponse)
async def trends(
    request: Request,
    window: int = 7,
):
    """趋势页 —— top triggers / rising / recall hot。"""
    templates = request.app.state.templates
    data_root = request.app.state.data_root
    available = _profile_available(data_root)
    data = _gather_trends(data_root, window=int(window or 7))
    return templates.TemplateResponse(
        request,
        "trends.html",
        {
            "available": available,
            "window": int(window or 7),
            "top_triggers": data["top_triggers"],
            "top_triggers_max": data["top_triggers_max"],
            "rising": data["rising"],
            "recall_hot": data["recall_hot"],
            "top_entities": data["top_entities"],
            "token": request.app.state.token,
        },
    )


def _gather_trends(data_root: Path, *, window: int) -> dict[str, Any]:
    """读 trigger_stats / memories / entities 表组装趋势页数据。"""
    out: dict[str, Any] = {
        "top_triggers": [],
        "top_triggers_max": 1,
        "rising": [],
        "recall_hot": [],
        "top_entities": [],
    }
    db = data_root / "index.db"
    if not db.exists():
        return out
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return out
    try:
        try:
            from memoryd.profile.trends import (
                top_triggers,
                rising_triggers,
                recall_hot,
            )
        except ImportError:
            return out
        try:
            tops = top_triggers(conn, window_days=window, limit=10)
            out["top_triggers"] = tops
            if tops:
                out["top_triggers_max"] = max(h for _, h in tops) or 1
        except sqlite3.Error:
            pass
        try:
            out["rising"] = rising_triggers(
                conn, recent_days=window, baseline_days=window * 2, limit=8
            )
        except sqlite3.Error:
            pass
        try:
            out["recall_hot"] = recall_hot(conn, limit=8)
        except sqlite3.Error:
            pass
        # 高频实体（最近 window_days）
        try:
            cutoff = (
                datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            )
            rows = conn.execute(
                "SELECT id, name, type, mention_count, decay_state "
                "FROM entities "
                "WHERE last_seen_at >= datetime('now', ?) "
                "ORDER BY mention_count DESC LIMIT 10",
                (f"-{window * 4} days",),
            ).fetchall()
            out["top_entities"] = [dict(r) for r in rows]
        except sqlite3.Error:
            pass
    finally:
        conn.close()
    return out


@router.get("/api/trends/triggers")
async def api_trends_triggers(
    request: Request,
    window: int = 7,
    scope: str | None = None,
):
    data_root = request.app.state.data_root
    db = data_root / "index.db"
    if not db.exists():
        return JSONResponse({"triggers": [], "available": False})
    try:
        from memoryd.profile.trends import top_triggers
    except ImportError:
        return JSONResponse({"triggers": [], "available": False})
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = top_triggers(
            conn,
            window_days=int(window or 7),
            scope_hash=scope,
            limit=20,
        )
        conn.close()
    except sqlite3.Error:
        return JSONResponse({"triggers": [], "available": False})
    return JSONResponse(
        {
            "triggers": [{"trigger": t, "hits": h} for t, h in rows],
            "available": True,
        }
    )


@router.get("/api/trends/entities")
async def api_trends_entities(
    request: Request,
    window: int = 30,
    scope: str | None = None,
):
    data_root = request.app.state.data_root
    if not _kg_available(data_root):
        return JSONResponse({"entities": [], "available": False})
    try:
        from memoryd.knowledge_graph import KnowledgeGraphStore
    except ImportError:
        return JSONResponse({"entities": [], "available": False})
    conn = _open_kg_conn(data_root)
    if conn is None:
        return JSONResponse({"entities": [], "available": False})
    try:
        store = KnowledgeGraphStore(conn)
        ents = store.top_entities(
            scope_hash=scope or None,
            window_days=int(window or 30),
            top_k=20,
        )
        return JSONResponse(
            {
                "entities": [
                    {
                        "id": e.id,
                        "name": e.name,
                        "type": e.type,
                        "mention_count": e.mention_count,
                        "decay_state": e.decay_state,
                    }
                    for e in ents
                ],
                "available": True,
            }
        )
    finally:
        conn.close()


# ===========================================================================
# 6. 用户画像页（Plan 11 新增）
# ===========================================================================


@router.get("/identity", response_class=HTMLResponse)
async def identity(request: Request):
    """画像首页：最新 identity.md + 历次快照 + 月度报告列表。"""
    templates = request.app.state.templates
    data_root = request.app.state.data_root
    info = _gather_identity(data_root)
    return templates.TemplateResponse(
        request,
        "identity.html",
        {
            "available": info["available"],
            "current": info["current"],
            "versions": info["versions"],
            "reports": info["reports"],
            "selected_version": None,
            "diff_lines": None,
            "diff_from": None,
            "diff_to": None,
            "token": request.app.state.token,
        },
    )


@router.get("/identity/version/{n:int}", response_class=HTMLResponse)
async def identity_version(request: Request, n: int):
    """查看特定版本的 identity 内容。"""
    templates = request.app.state.templates
    data_root = request.app.state.data_root
    info = _gather_identity(data_root)
    selected = next((v for v in info["versions"] if v["version_num"] == n), None)
    return templates.TemplateResponse(
        request,
        "identity.html",
        {
            "available": info["available"],
            "current": info["current"],
            "versions": info["versions"],
            "reports": info["reports"],
            "selected_version": selected,
            "diff_lines": None,
            "diff_from": None,
            "diff_to": None,
            "token": request.app.state.token,
        },
    )


@router.get("/identity/diff", response_class=HTMLResponse)
async def identity_diff(request: Request, from_: int | None = None, to: int | None = None):
    """两版 diff（带语义着色）。"""
    # FastAPI 不允许 query param 名为 from（关键字），用 alias 也有点折腾，
    # 这里支持 ``from`` / ``from_`` 两种写法。
    qp = request.query_params
    from_val = qp.get("from") or qp.get("from_")
    to_val = qp.get("to")
    try:
        f = int(from_val) if from_val else None
        t = int(to_val) if to_val else None
    except ValueError:
        f, t = None, None

    templates = request.app.state.templates
    data_root = request.app.state.data_root
    info = _gather_identity(data_root)
    diff_lines = _identity_diff(info["versions"], f, t) if (f and t) else None
    return templates.TemplateResponse(
        request,
        "identity.html",
        {
            "available": info["available"],
            "current": info["current"],
            "versions": info["versions"],
            "reports": info["reports"],
            "selected_version": None,
            "diff_lines": diff_lines,
            "diff_from": f,
            "diff_to": t,
            "token": request.app.state.token,
        },
    )


@router.get("/api/identity/report/{period}", response_class=PlainTextResponse)
async def api_identity_report(request: Request, period: str):
    """返回月度报告 markdown 文本（period = YYYY-MM）。"""
    data_root = request.app.state.data_root
    # 先看 SQLite 表
    db = data_root / "index.db"
    if db.exists():
        try:
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT content_md FROM profile_change_reports WHERE period = ?",
                (period,),
            ).fetchone()
            conn.close()
            if row and row["content_md"]:
                return PlainTextResponse(row["content_md"])
        except sqlite3.Error:
            pass
    # 回落到磁盘
    f = data_root / "profile" / "change-reports" / f"{period}.md"
    if f.exists():
        return PlainTextResponse(f.read_text(encoding="utf-8"))
    raise HTTPException(404, detail=f"no report for {period}")


# ===========================================================================
# 内部 helpers
# ===========================================================================


def _kg_available(data_root: Path) -> bool:
    """KG 表是否就绪。"""
    db = data_root / "index.db"
    if not db.exists():
        return False
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entities'"
        ).fetchone()
        conn.close()
        return row is not None
    except sqlite3.Error:
        return False


def _profile_available(data_root: Path) -> bool:
    """Profile 表是否就绪。"""
    db = data_root / "index.db"
    if not db.exists():
        return False
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='profile_versions'"
        ).fetchone()
        conn.close()
        return row is not None
    except sqlite3.Error:
        return False


def _open_kg_conn(data_root: Path) -> sqlite3.Connection | None:
    """打开主 DB 并设置 row_factory。失败返回 None。"""
    db = data_root / "index.db"
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _list_known_scopes(data_root: Path) -> list[dict[str, str]]:
    """枚举 scopes/ 下的非 _ 开头目录。"""
    from memoryd.scope_meta import is_path_sensitive
    out: list[dict[str, str]] = []
    scopes = data_root / "scopes"
    if not scopes.exists():
        return out
    for d in sorted(scopes.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        out.append(
            {
                "hash": d.name,
                "sensitive": "1" if is_path_sensitive(d) else "0",
            }
        )
    return out


def _gather_identity(data_root: Path) -> dict[str, Any]:
    """收集 identity 页所需信息。"""
    out: dict[str, Any] = {
        "available": False,
        "current": None,
        "versions": [],
        "reports": [],
    }
    # 当前 identity.md（磁盘）
    identity_file = data_root / "profile" / "identity.md"
    if identity_file.exists():
        try:
            out["current"] = {
                "path": str(identity_file),
                "body": identity_file.read_text(encoding="utf-8"),
                "mtime": datetime.fromtimestamp(
                    identity_file.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        except OSError:
            out["current"] = None
    # SQLite 版本 / 月度
    db = data_root / "index.db"
    if not db.exists():
        return out
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return out
    try:
        try:
            rows = conn.execute(
                "SELECT version_num, written_at, trigger, content_md, "
                "       diff_from_prev, change_summary, sources_count "
                "  FROM profile_versions ORDER BY version_num DESC LIMIT 50"
            ).fetchall()
            out["versions"] = [dict(r) for r in rows]
            out["available"] = True
        except sqlite3.Error:
            out["versions"] = []
        try:
            rows = conn.execute(
                "SELECT period, generated_at, versions_count, supersedes_count, "
                "       entities_added, entities_dropped "
                "  FROM profile_change_reports ORDER BY period DESC LIMIT 24"
            ).fetchall()
            out["reports"] = [dict(r) for r in rows]
        except sqlite3.Error:
            out["reports"] = []
    finally:
        conn.close()
    return out


def _identity_diff(versions: list[dict], a: int | None, b: int | None) -> list[dict]:
    """将 versions[a] 和 versions[b] 的 content_md 跑 unified_diff，
    返回带语义类别的行（add / remove / context / header）。
    """
    import difflib
    by_num = {v["version_num"]: v for v in versions}
    va = by_num.get(a)
    vb = by_num.get(b)
    if not va or not vb:
        return []
    lines = list(
        difflib.unified_diff(
            (va["content_md"] or "").splitlines(keepends=False),
            (vb["content_md"] or "").splitlines(keepends=False),
            fromfile=f"v{va['version_num']}",
            tofile=f"v{vb['version_num']}",
            n=3,
        )
    )
    out: list[dict] = []
    for line in lines:
        if line.startswith("+++") or line.startswith("---"):
            kind = "header"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "remove"
        else:
            kind = "context"
        out.append({"text": line, "kind": kind})
    return out


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
        parts = md.relative_to(scopes).parts
        if not parts or parts[0].startswith("_"):
            continue
        scope_hash = parts[0]
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
