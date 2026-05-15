import pytest
from fastapi.testclient import TestClient

from memoryd.web import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(token="test-token-1234", data_root=tmp_path)


@pytest.fixture
def client(app):
    return TestClient(app)


def test_healthz_no_auth_required(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_requires_token(client):
    r = client.get("/")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


def test_root_accepts_token_query(client, app):
    """Middleware should pass; route may 404 since not built yet."""
    r = client.get(f"/?token={app.state.token}")
    assert r.status_code != 401


def test_root_accepts_token_cookie(client, app):
    r = client.get("/", cookies={"memoryd_token": app.state.token})
    assert r.status_code != 401


def test_root_accepts_bearer_header(client, app):
    r = client.get(
        "/", headers={"Authorization": f"Bearer {app.state.token}"}
    )
    assert r.status_code != 401


def test_wrong_token_returns_401(client):
    r = client.get("/?token=wrong")
    assert r.status_code == 401


def test_static_path_does_not_require_token(client):
    """Static mount should be public so the browser can fetch CSS without re-supplying token in img/css refs."""
    # 没文件会 404，但不是 401
    r = client.get("/static/nonexistent.css")
    assert r.status_code in (404, 200)
