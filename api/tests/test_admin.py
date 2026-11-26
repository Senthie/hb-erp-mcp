"""管理员后台：账号管理 + 审计日志（SQLite 后端，离线）。

生产环境这两张表同样落在 PostgreSQL（`users.role/disabled`、`audit_logs`）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from erp_tools.apps.server.app import create_app, build_state
from erp_tools.apps.server.config import ServerConfig
from erp_tools.core.config import ErpConfig
from erp_tools.core.errors import AuthError
from erp_tools.runtime.sessions import UserSession

ADMIN = {"username": "root", "password": "root-password"}
ALICE = {"username": "alice", "password": "alice-password"}
ERP = {"username": "21146", "password": "erp-secret"}


def fake_erp_login(username: str, password: str) -> UserSession:
    if password != "erp-secret":
        raise AuthError("账号或密码错误")
    return UserSession(username=username, auth=object(), user_id="1234", pages={})


def make_app(tmp_path: Path):
    erp = ErpConfig(host="http://127.0.0.1:8889", username="", password="",
                    cookie_dir=tmp_path / "cookies")
    server = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db",
                          global_concurrency=1, admin_users=["root"])
    state = build_state(server, erp, login_impl=fake_erp_login,
                        alive_impl=lambda _auth: True)
    return create_app(state, enable_mcp=False)


@pytest.fixture()
def clients(tmp_path: Path):
    """同一个应用、两个独立 cookie 会话：管理员 + 普通用户。"""
    app = make_app(tmp_path)
    with TestClient(app) as admin, TestClient(app) as user:
        yield admin, user


def register(client: TestClient, account: dict) -> dict:
    resp = client.post("/v1/platform/register", json=account)
    assert resp.status_code == 201, resp.text
    return resp.json()["user"]


def user_id(client: TestClient) -> str:
    resp = client.get("/v1/platform/me")
    assert resp.status_code == 200, resp.text
    return resp.json()["user"]["id"]


def bind_and_issue(client: TestClient) -> dict:
    assert client.post("/v1/platform/erp/bind", json=ERP).status_code == 200
    resp = client.post("/v1/platform/tokens", json={"label": "laptop"})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ══════════════════ 权限 ══════════════════
def test_admin_routes_require_admin(clients) -> None:
    admin, user = clients
    register(admin, ADMIN)
    register(user, ALICE)

    for path in ("/v1/admin/overview", "/v1/admin/users", "/v1/admin/audit"):
        resp = user.get(path)
        assert resp.status_code == 403, path
        assert resp.json()["error"] == "admin_required"


def test_admin_routes_require_login(clients) -> None:
    admin, _ = clients
    assert admin.get("/v1/admin/users").status_code == 401


def test_whitelisted_username_registers_as_admin(clients) -> None:
    admin, user = clients
    assert register(admin, ADMIN)["role"] == "admin"
    assert register(user, ALICE)["role"] == "user"


# ══════════════════ 用户列表 ══════════════════
def test_list_users_with_counts(clients) -> None:
    admin, user = clients
    register(admin, ADMIN)
    register(user, ALICE)
    bind_and_issue(user)

    body = admin.get("/v1/admin/users").json()
    assert body["total"] == 2
    by_name = {item["username"]: item for item in body["items"]}
    assert by_name["root"]["role"] == "admin"
    assert by_name["alice"]["erp"]["username"] == "21146"
    assert by_name["alice"]["token_count"] == 1
    assert by_name["alice"]["session_count"] == 1

    assert admin.get("/v1/admin/users", params={"query": "ali"}).json()["total"] == 1


def test_overview_counts(clients) -> None:
    admin, user = clients
    register(admin, ADMIN)
    register(user, ALICE)
    bind_and_issue(user)

    body = admin.get("/v1/admin/overview").json()
    assert body["users"]["total"] == 2
    assert body["users"]["erp_bound"] == 1
    assert body["tokens"]["active"] == 1
    assert any(item["value"] == "platform.token.create" for item in body["actions"])


# ══════════════════ 改角色 / 禁用 ══════════════════
def test_disable_user_kills_sessions_and_blocks_login(clients) -> None:
    admin, user = clients
    register(admin, ADMIN)
    register(user, ALICE)
    issued = bind_and_issue(user)
    target = user_id(user)

    resp = admin.post(f"/v1/admin/users/{target}/update", json={"disabled": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["disabled"] is True
    assert resp.json()["killed_sessions"] == 1
    assert resp.json()["revoked_tokens"] == 1, "禁用应同时撤销该账号的 token"

    assert user.get("/v1/platform/me").status_code == 401
    assert user.get("/v1/me", headers={
        "Authorization": f"Bearer {issued['token']}"
    }).status_code == 401, "禁用后 MCP token 也应失效"
    relogin = user.post("/v1/platform/login", json=ALICE)
    assert relogin.status_code == 403
    assert relogin.json()["error"] == "account_disabled"

    assert admin.post(f"/v1/admin/users/{target}/update",
                      json={"disabled": False}).status_code == 200
    assert user.post("/v1/platform/login", json=ALICE).status_code == 200


def test_promote_and_demote_role(clients) -> None:
    admin, user = clients
    register(admin, ADMIN)
    register(user, ALICE)
    target = user_id(user)

    assert user.get("/v1/admin/users").status_code == 403
    promoted = admin.post(f"/v1/admin/users/{target}/update", json={"role": "admin"})
    assert promoted.status_code == 200
    assert promoted.json()["user"]["role"] == "admin"
    assert user.get("/v1/admin/users").status_code == 200

    demoted = admin.post(f"/v1/admin/users/{target}/update", json={"role": "user"})
    assert demoted.json()["user"]["role"] == "user"


def test_cannot_modify_or_delete_self(clients) -> None:
    admin, _ = clients
    register(admin, ADMIN)
    me = user_id(admin)

    assert admin.post(f"/v1/admin/users/{me}/update",
                      json={"role": "user"}).status_code == 409
    assert admin.delete(f"/v1/admin/users/{me}").status_code == 409


# ══════════════════ 解绑 / 撤销 / 删除 ══════════════════
def test_admin_unbind_erp_revokes_tokens(clients) -> None:
    admin, user = clients
    register(admin, ADMIN)
    register(user, ALICE)
    issued = bind_and_issue(user)
    target = user_id(user)

    resp = admin.post(f"/v1/admin/users/{target}/unbind-erp")
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["erp"]["bound"] is False
    assert resp.json()["revoked_tokens"] == 1

    assert user.get("/v1/me", headers={
        "Authorization": f"Bearer {issued['token']}"
    }).status_code == 401


def test_admin_revoke_tokens_and_delete_user(clients) -> None:
    admin, user = clients
    register(admin, ADMIN)
    register(user, ALICE)
    issued = bind_and_issue(user)
    target = user_id(user)

    revoked = admin.post(f"/v1/admin/users/{target}/revoke-tokens")
    assert revoked.status_code == 200
    assert revoked.json()["revoked_tokens"] == 1
    assert user.get("/v1/me", headers={
        "Authorization": f"Bearer {issued['token']}"
    }).status_code == 401

    assert admin.delete(f"/v1/admin/users/{target}").status_code == 200
    assert admin.get(f"/v1/admin/users/{target}").status_code == 404
    assert user.get("/v1/platform/me").status_code == 401


# ══════════════════ 审计日志 ══════════════════
def test_audit_records_platform_actions(clients) -> None:
    admin, user = clients
    register(admin, ADMIN)
    register(user, ALICE)
    assert user.post("/v1/platform/login", json=ALICE).status_code == 200
    bind_and_issue(user)

    body = admin.get("/v1/admin/audit", params={"limit": 50}).json()
    actions = [item["action"] for item in body["items"]]
    for expected in ("platform.register", "platform.login",
                     "platform.erp.bind", "platform.token.create"):
        assert expected in actions, actions

    created = [item for item in body["items"] if item["action"] == "platform.token.create"]
    assert created[0]["actor_name"] == "alice"
    assert created[0]["result"] == "success"
    assert created[0]["action_label"] == "申请 Token"
    assert created[0]["detail"]["erp_username"] == "21146"


def test_audit_records_failures_and_filters(clients) -> None:
    admin, user = clients
    register(admin, ADMIN)
    register(user, ALICE)
    user.post("/v1/platform/login", json={**ALICE, "password": "wrong-password"})
    bind_and_issue(user)

    failed = admin.get("/v1/admin/audit", params={"result": "failure"}).json()
    assert failed["items"], "登录失败应留下审计记录"
    assert failed["items"][0]["action"] == "platform.login"
    assert failed["items"][0]["status_code"] == 401

    by_actor = admin.get("/v1/admin/audit", params={"actor": "alice"}).json()
    assert by_actor["items"]
    assert all("alice" in (item["actor_name"] or "") for item in by_actor["items"])

    by_action = admin.get("/v1/admin/audit",
                          params={"action": "platform.erp.bind"}).json()
    assert by_action["items"]
    assert {item["action"] for item in by_action["items"]} == {"platform.erp.bind"}


def test_admin_actions_are_audited(clients) -> None:
    admin, user = clients
    register(admin, ADMIN)
    register(user, ALICE)
    target = user_id(user)

    admin.post(f"/v1/admin/users/{target}/update", json={"disabled": True})
    admin.post(f"/v1/admin/users/{target}/revoke-tokens")

    logs = admin.get("/v1/admin/audit", params={"action": "admin.user.update"}).json()
    item = logs["items"][0]
    assert item["actor_name"] == "root"
    assert item["target_id"] == target
    assert item["detail"]["disabled"] is True
    assert item["detail"]["username"] == "alice"


def test_audit_pagination_has_more(clients) -> None:
    admin, _ = clients
    register(admin, ADMIN)
    # 再补一条记录（登录），保证至少有 2 行可翻页
    assert admin.post("/v1/platform/login", json=ADMIN).status_code == 200
    first = admin.get("/v1/admin/audit", params={"limit": 1}).json()
    assert first["has_more"] is True
    assert len(first["items"]) == 1

    page2 = admin.get("/v1/admin/audit", params={"limit": 1, "offset": 1}).json()
    assert page2["items"][0]["id"] != first["items"][0]["id"]


def test_audit_never_contains_secrets(clients) -> None:
    admin, user = clients
    register(admin, ADMIN)
    register(user, ALICE)
    issued = bind_and_issue(user)

    text = admin.get("/v1/admin/audit", params={"limit": 100}).text
    assert ALICE["password"] not in text
    assert ERP["password"] not in text
    assert issued["token"] not in text
