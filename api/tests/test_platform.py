"""平台账号：注册 / 登录 / 会话 / ERP 绑定 / MCP token 管理（SQLite 后端，离线）。

产品流程就是这里的顺序：
`注册或登录平台账号 → 绑定 ERP 账号 → 申请 token → 拿到 AI 客户端配置`。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from erp_tools.apps.server.app import create_app
from erp_tools.apps.server.config import ServerConfig
from erp_tools.apps.server.platform_routes import SESSION_COOKIE
from erp_tools.apps.server.state import build_state
from erp_tools.core.config import ErpConfig
from erp_tools.core.errors import AuthError
from erp_tools.runtime.sessions import UserSession

USER = {"username": "sora", "password": "a-good-password"}
ERP = {"username": "21146", "password": "erp-secret"}


def fake_erp_login(username: str, password: str) -> UserSession:
    if password != "erp-secret":
        raise AuthError("账号或密码错误")
    return UserSession(
        username=username,
        auth=object(),
        user_id="1234",
        pages={"BaoJia/BJ_KHBJXQ/Index.aspx": "876"},
    )


def make_client(tmp_path: Path, *, allow_signup: bool = True) -> TestClient:
    erp = ErpConfig(host="http://127.0.0.1:8889", username="", password="",
                    cookie_dir=tmp_path / "cookies")
    server = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db",
                          global_concurrency=1, allow_signup=allow_signup)
    state = build_state(server, erp, login_impl=fake_erp_login,
                        alive_impl=lambda _auth: True)
    return TestClient(create_app(state, enable_mcp=False))


@pytest.fixture()
def client(tmp_path: Path):
    with make_client(tmp_path) as c:
        yield c


def register(client: TestClient, **overrides) -> dict:
    resp = client.post("/v1/platform/register", json={**USER, **overrides})
    assert resp.status_code == 201, resp.text
    return resp.json()


def bound_client(client: TestClient) -> TestClient:
    register(client)
    resp = client.post("/v1/platform/erp/bind", json=ERP)
    assert resp.status_code == 200, resp.text
    return client


# ══════════════════ 注册 ══════════════════
def test_register_creates_account_and_logs_in(client: TestClient) -> None:
    body = register(client)

    assert body["user"]["username"] == "sora"
    assert body["user"]["erp"] == {
        "bound": False, "username": None, "user_id": None, "bound_at": None,
    }
    assert "password" not in str(body).replace("username", "")
    cookie = client.cookies.get(SESSION_COOKIE)
    assert cookie and cookie.startswith("hbs_")

    set_cookie = client.post("/v1/platform/login", json=USER).headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower() or "SameSite=lax" in set_cookie

    me = client.get("/v1/platform/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "sora"
    assert me.json()["tokens"] == []


def test_register_rejects_duplicate_username(client: TestClient) -> None:
    register(client)
    resp = client.post("/v1/platform/register", json=USER)
    assert resp.status_code == 409
    assert resp.json()["error"] == "username_taken"


@pytest.mark.parametrize("payload, needle", [
    ({"username": "ab", "password": "a-good-password"}, "用户名长度"),
    ({"username": "sora!", "password": "a-good-password"}, "只能包含"),
    ({"username": "sora", "password": "short"}, "密码至少"),
])
def test_register_validates_input(client: TestClient, payload: dict,
                                  needle: str) -> None:
    resp = client.post("/v1/platform/register", json=payload)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_account"
    assert needle in resp.json()["message"]


def test_register_can_be_disabled(tmp_path: Path) -> None:
    with make_client(tmp_path, allow_signup=False) as c:
        resp = c.post("/v1/platform/register", json=USER)
        assert resp.status_code == 403
        assert resp.json()["error"] == "signup_disabled"


# ══════════════════ 登录 / 登出 ══════════════════
def test_login_and_logout(client: TestClient) -> None:
    register(client)
    client.post("/v1/platform/logout")
    assert client.get("/v1/platform/me").status_code == 401

    ok = client.post("/v1/platform/login", json=USER)
    assert ok.status_code == 200
    assert ok.json()["user"]["username"] == "sora"

    wrong = client.post("/v1/platform/login",
                        json={**USER, "password": "wrong-password"})
    assert wrong.status_code == 401
    assert wrong.json()["error"] == "invalid_credentials"


def test_me_requires_cookie(client: TestClient) -> None:
    resp = client.get("/v1/platform/me")
    assert resp.status_code == 401
    assert resp.json()["error"] == "login_required"


def test_stale_cookie_is_rejected(client: TestClient) -> None:
    register(client)
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, "hbs_" + "x" * 40)
    resp = client.get("/v1/platform/me")
    assert resp.status_code == 401
    assert resp.json()["error"] == "session_expired"


# ══════════════════ 绑定 ERP ══════════════════
def test_bind_erp_account(client: TestClient) -> None:
    register(client)
    resp = client.post("/v1/platform/erp/bind", json=ERP)
    assert resp.status_code == 200, resp.text
    erp = resp.json()["user"]["erp"]
    assert erp["bound"] is True
    assert erp["username"] == "21146"
    assert erp["user_id"] == "1234"
    assert erp["bound_at"]


def test_bind_erp_rejects_wrong_password(client: TestClient) -> None:
    register(client)
    resp = client.post("/v1/platform/erp/bind",
                       json={**ERP, "password": "nope"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_erp_credentials"


def test_bind_erp_requires_login(client: TestClient) -> None:
    assert client.post("/v1/platform/erp/bind", json=ERP).status_code == 401


# ══════════════════ token ══════════════════
def test_token_requires_erp_binding(client: TestClient) -> None:
    register(client)
    resp = client.post("/v1/platform/tokens", json={"label": "笔记本"})
    assert resp.status_code == 409
    assert resp.json()["error"] == "erp_not_bound"


def test_apply_token_returns_ai_snippet(client: TestClient) -> None:
    bound_client(client)
    resp = client.post("/v1/platform/tokens", json={"label": "笔记本"})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["token"].startswith("erp_")
    assert len(body["id"]) == 16
    assert body["label"] == "笔记本"
    assert body["erp_username"] == "21146"

    mcp = body["mcp"]
    assert mcp["url"].endswith("/mcp")
    assert mcp["config"]["servers"]["hb-mcp"]["headers"] == {
        "Authorization": f"Bearer {body['token']}"
    }
    assert body["token"] in mcp["config_json"]
    assert body["token"] in mcp["prompt"]
    assert "Authorization: Bearer " in mcp["prompt"]

    listing = client.get("/v1/platform/tokens").json()
    assert listing["erp"]["bound"] is True
    assert len(listing["tokens"]) == 1
    assert listing["tokens"][0]["id"] == body["id"]
    assert listing["tokens"][0]["label"] == "笔记本"
    assert body["token"] not in str(listing), "明文 token 不能再次出现在列表里"


def test_issued_token_works_for_mcp(client: TestClient) -> None:
    bound_client(client)
    token = client.post("/v1/platform/tokens", json={}).json()["token"]

    resp = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["username"] == "21146"


def test_revoke_token(client: TestClient) -> None:
    bound_client(client)
    token = client.post("/v1/platform/tokens", json={}).json()
    path = f"/v1/platform/tokens/{token['id']}/revoke"

    assert client.post(path).status_code == 200
    assert client.get("/v1/platform/tokens").json()["tokens"] == []
    assert client.post(path).status_code == 404
    assert client.get("/v1/me", headers={
        "Authorization": f"Bearer {token['token']}"
    }).status_code == 401


def test_rebinding_another_erp_account_revokes_tokens(client: TestClient) -> None:
    bound_client(client)
    client.post("/v1/platform/tokens", json={"label": "旧"})

    other = {"username": "30001", "password": "erp-secret"}
    resp = client.post("/v1/platform/erp/bind", json=other)
    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked_tokens"] == 1
    assert resp.json()["user"]["erp"]["username"] == "30001"
    assert resp.json()["tokens"] == []


def test_tokens_are_scoped_per_account(tmp_path: Path) -> None:
    with make_client(tmp_path) as first:
        bound_client(first)
        first.post("/v1/platform/tokens", json={"label": "first"})
        first_token_id = first.get("/v1/platform/tokens").json()["tokens"][0]["id"]

    with make_client(tmp_path) as second:
        register(second, username="other")
        second.post("/v1/platform/erp/bind", json={"username": "30001",
                                                   "password": "erp-secret"})
        assert second.get("/v1/platform/tokens").json()["tokens"] == []
        # 别人的 token id 撤不掉
        assert second.post(
            f"/v1/platform/tokens/{first_token_id}/revoke"
        ).status_code == 404
