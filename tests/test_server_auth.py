"""apps.server：认证网关端到端（不连真实 ERP）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from erp_tools.apps.server.app import create_app
from erp_tools.apps.server.config import ServerConfig
from erp_tools.apps.server.state import build_state
from erp_tools.core.config import ErpConfig
from erp_tools.core.errors import AuthError
from erp_tools.runtime.sessions import UserSession


def fake_session(username: str = "21146") -> UserSession:
    return UserSession(
        username=username,
        auth=object(),
        user_id="1234",
        pages={"BaoJia/BJ_KHBJXQ/Index.aspx": "876"},
    )


def ok_login(username: str, password: str) -> UserSession:
    if password != "secret":
        raise AuthError("账号或密码错误")
    return fake_session(username)


def make_state(tmp_path: Path, *, login_impl=ok_login, alive_impl=lambda _auth: True):
    erp = ErpConfig(
        host="http://127.0.0.1:8889", username="21146", password="",
        cookie_dir=tmp_path / "cookies",
    )
    server = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db",
                          global_concurrency=2)
    return build_state(server, erp, login_impl=login_impl, alive_impl=alive_impl)


@pytest.fixture()
def state(tmp_path: Path):
    st = make_state(tmp_path)
    yield st
    st.close()


@pytest.fixture()
def client(state):
    app = create_app(state)
    with TestClient(app) as c:
        yield c


def login(client: TestClient, password: str = "secret"):
    return client.post("/v1/auth/login", json={"username": "21146", "password": password})


def auth_header(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# ══════════════════ 公开端点 ══════════════════
def test_healthz_is_public(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_login_returns_api_key(client: TestClient) -> None:
    resp = login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_key"].startswith("erp_")
    assert body["username"] == "21146"
    assert body["user_id"] == "1234"
    assert body["erp_pages"]["quote_index"] == "876"
    assert "不设过期时间" in body["note"]


def test_login_bad_password_is_uniform_401(client: TestClient) -> None:
    resp = login(client, "wrong")
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_credentials"
    assert "password" not in resp.text.lower() or "password" in "username/password"  # 不含回显


def test_login_missing_fields_is_400(client: TestClient) -> None:
    resp = client.post("/v1/auth/login", json={"username": "21146"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"

    resp = client.post("/v1/auth/login", content=b"not-json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400


def test_login_rate_limited(client: TestClient) -> None:
    for _ in range(5):
        assert login(client, "wrong").status_code == 401

    resp = login(client, "wrong")
    assert resp.status_code == 429
    assert resp.json()["error"] == "rate_limited"
    assert int(resp.headers["retry-after"]) >= 1


def test_login_locked_after_failures(state, client: TestClient) -> None:
    for _ in range(10):
        state.guard.fail("user:21146")
    resp = login(client)
    assert resp.status_code == 423
    assert resp.json()["error"] == "locked"
    assert "retry-after" in {k.lower() for k in resp.headers}


# ══════════════════ 需要 api-key 的端点 ══════════════════
def test_me_requires_bearer(client: TestClient) -> None:
    resp = client.get("/v1/me")
    assert resp.status_code == 401
    assert resp.json()["error"] == "missing_bearer"
    assert resp.headers["www-authenticate"] == "Bearer"


def test_me_rejects_malformed_key(client: TestClient) -> None:
    resp = client.get("/v1/me", headers=auth_header("not-a-key"))
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_api_key"


def test_me_rejects_unknown_key(client: TestClient) -> None:
    resp = client.get("/v1/me", headers=auth_header("erp_" + "x" * 40))
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_api_key"


def test_me_with_valid_key(client: TestClient) -> None:
    key = login(client).json()["api_key"]
    resp = client.get("/v1/me", headers=auth_header(key))
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "21146"
    assert body["erp_alive"] is True


def test_tools_requires_auth_and_lists_tools(client: TestClient) -> None:
    assert client.get("/v1/tools").status_code == 401

    key = login(client).json()["api_key"]
    resp = client.get("/v1/tools", headers=auth_header(key))
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["tools"]}
    assert {"query", "quote", "attendance"} <= names


def test_logout_revokes_only_current_key(client: TestClient) -> None:
    first = login(client).json()["api_key"]
    second = login(client).json()["api_key"]

    assert client.post("/v1/auth/logout", headers=auth_header(first)).status_code == 204

    resp = client.get("/v1/me", headers=auth_header(first))
    assert resp.status_code == 401
    assert resp.json()["error"] == "api_key_revoked"

    assert client.get("/v1/me", headers=auth_header(second)).status_code == 200


def test_session_expired_revokes_key(tmp_path: Path) -> None:
    st = make_state(tmp_path, alive_impl=lambda _auth: False)
    try:
        app = create_app(st)
        with TestClient(app) as c:
            key = login(c).json()["api_key"]
            resp = c.get("/v1/me", headers=auth_header(key))
            assert resp.status_code == 401
            assert resp.json()["error"] == "erp_session_expired"

            # api-key 一并作废
            again = c.get("/v1/me", headers=auth_header(key))
            assert again.status_code == 401
            assert again.json()["error"] == "api_key_revoked"
    finally:
        st.close()


def test_upstream_error_maps_to_502(tmp_path: Path) -> None:
    from erp_tools.core.errors import NetworkError

    def failing(_u: str, _p: str):
        raise NetworkError("connection refused")

    st = make_state(tmp_path, login_impl=failing)
    try:
        with TestClient(create_app(st)) as c:
            resp = c.post("/v1/auth/login",
                          json={"username": "21146", "password": "secret"})
            assert resp.status_code == 502
            assert resp.json()["error"] == "erp_unreachable"
    finally:
        st.close()
