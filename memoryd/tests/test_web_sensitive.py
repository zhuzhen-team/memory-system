import pytest
from fastapi.testclient import TestClient

from memoryd.web import create_app


def _write_md(root, scope, type_, slug, body="x", sensitive=False):
    p = root / "scopes" / scope / type_ / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    if sensitive:
        (root / "scopes" / scope / ".memoryd-sensitive").write_text(
            "scope_root: /x"
        )
    return p


def test_list_shows_lock_for_sensitive(tmp_path):
    _write_md(tmp_path, "h1", "sessions", "open")
    _write_md(tmp_path, "h2", "sessions", "secret", sensitive=True)
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/?token=t")
    assert "\U0001f512" in r.text


def test_detail_403_for_sensitive(tmp_path):
    _write_md(tmp_path, "h2", "sessions", "secret",
              body="secret data confidential", sensitive=True)
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/memories/secret?token=t")
    assert r.status_code == 403
    assert "secret data" not in r.text


def test_open_scope_detail_returns_body(tmp_path):
    _write_md(tmp_path, "h1", "sessions", "open", body="public body 123")
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/memories/open?token=t")
    assert r.status_code == 200
    assert "public body 123" in r.text


def test_sensitive_inherits_to_child_dirs(tmp_path):
    """Plan 4 marker inheritance: child paths under sensitive root are sensitive."""
    # marker at h2 root；session under h2/sessions/<file>
    _write_md(tmp_path, "h2", "sessions", "deep", body="deep secret",
              sensitive=True)
    client = TestClient(create_app(token="t", data_root=tmp_path))
    r = client.get("/memories/deep?token=t")
    assert r.status_code == 403
