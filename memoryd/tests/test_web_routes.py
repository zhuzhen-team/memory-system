import json

import pytest
from fastapi.testclient import TestClient

from memoryd.web import create_app


def _write_md(root, scope, type_, slug, body="x"):
    p = root / "scopes" / scope / type_ / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_index_renders_with_token(tmp_path):
    _write_md(tmp_path, "h1", "sessions", "2026-05-15-hello")
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/?token=t")
    assert r.status_code == 200
    assert "2026-05-15-hello" in r.text


def test_list_filters_by_type(tmp_path):
    _write_md(tmp_path, "h1", "sessions", "session-a")
    _write_md(tmp_path, "h1", "decisions", "decision-b")
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/memories?type=sessions&token=t")
    assert r.status_code == 200
    assert "session-a" in r.text
    assert "decision-b" not in r.text


def test_list_filters_by_scope(tmp_path):
    _write_md(tmp_path, "h1", "sessions", "a-h1")
    _write_md(tmp_path, "h2", "sessions", "b-h2")
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/memories?scope=h2&token=t")
    assert "a-h1" not in r.text
    assert "b-h2" in r.text


def test_detail_returns_body(tmp_path):
    _write_md(tmp_path, "h1", "sessions", "detail-z", body="my body content here")
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/memories/detail-z?token=t")
    assert r.status_code == 200
    assert "my body content here" in r.text


def test_detail_404_unknown_slug(tmp_path):
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/memories/missing?token=t")
    assert r.status_code == 404


def test_index_empty_when_no_scopes(tmp_path):
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/?token=t")
    assert r.status_code == 200


def test_list_ignores_conflicts_dir(tmp_path):
    """_conflicts/ is Plan 6 backup; should not appear in web list."""
    _write_md(tmp_path, "h1", "sessions", "good")
    conf = tmp_path / "scopes" / "_conflicts"
    conf.mkdir(parents=True)
    (conf / "leaked.md").write_text("should not be visible")
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/memories?token=t")
    assert "good" in r.text
    assert "leaked" not in r.text


def test_search_returns_fragment(tmp_path):
    _write_md(tmp_path, "h1", "sessions", "abc",
              body="body that mentions foo word")
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/search?q=foo&token=t")
    assert r.status_code == 200
    assert "abc" in r.text


def test_search_empty_query(tmp_path):
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/search?q=&token=t")
    assert r.status_code == 200


def test_search_skips_sensitive(tmp_path):
    """Search must NOT leak sensitive scope body."""
    _write_md(tmp_path, "h1", "sessions", "secret",
              body="finance accounts foo")
    (tmp_path / "scopes" / "h1" / ".memoryd-sensitive").write_text(
        "scope_root: /x"
    )
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/search?q=foo&token=t")
    assert "secret" not in r.text
    # no body leak
    assert "finance accounts" not in r.text


def test_audit_empty(tmp_path):
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/audit?token=t")
    assert r.status_code == 200


def test_audit_filters_scope(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "audit.jsonl").write_text(
        '{"ts":"2026-05-01T00:00:00+00:00","scope_hash":"a",'
        '"event_type":"access_granted","tool":"search_memory","result":"ok"}\n'
        '{"ts":"2026-05-02T00:00:00+00:00","scope_hash":"b",'
        '"event_type":"access_granted","tool":"search_memory","result":"ok"}\n'
    )
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/audit?scope=a&token=t")
    assert r.status_code == 200
    assert "access_granted" in r.text
    # contains "a" entry; "b" not in text body (scope column)
    # We can't easily check "b not present" because HTML might have other 'b' chars.
    # Check 2026-05-02 timestamp instead:
    assert "2026-05-02" not in r.text


def test_digest_empty_no_db(tmp_path):
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/digest?token=t")
    assert r.status_code == 200
    assert "no pending promotions" in r.text or "pending" in r.text


def test_digest_lists_pending_from_db(tmp_path):
    import sqlite3
    db = tmp_path / "index.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE promotions (id INTEGER PRIMARY KEY, "
        "source_session_slug TEXT, proposed_type TEXT, proposed_title TEXT, "
        "reasoning TEXT, status TEXT)"
    )
    conn.execute(
        "INSERT INTO promotions (source_session_slug, proposed_type, "
        "proposed_title, reasoning, status) VALUES "
        "('sess-1', 'decision', 'logo direction', 'high D U R A', 'pending')"
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/digest?token=t")
    assert r.status_code == 200
    assert "logo direction" in r.text
    assert "sess-1" in r.text
