"""MCP 调用日志：每个用户通过 token 调了哪个工具、成功与否、耗时多少。

日志落 `mcp_call_logs`（生产 PostgreSQL / 测试 SQLite），管理员后台
`GET /v1/admin/calls` 可查。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from erp_tools.apps.server.app import build_state, create_app
from erp_tools.apps.server.config import ServerConfig
from erp_tools.core.config import ErpConfig
from erp_tools.core.errors import AuthError
from erp_tools.runtime.sessions import UserSession

BASE_URL = "http://127.0.0.1:8765"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "User-Agent": "pytest-mcp-client/1.0",
}
PROTOCOL_VERSION = "2025-06-18"
ADMIN = {"username": "root", "password": "root-password"}


def fake_erp_login(username: str, password: str) -> UserSession:
    if password != "erp-secret":
        raise AuthError("账号或密码错误")
    return UserSession(
        username=username,
        auth=object(),
        user_id="1234",
        pages={"BaoJia/BJ_KHBJXQ/Index.aspx": "876"},
    )


@pytest.fixture()
def client(tmp_path: Path):
    erp = ErpConfig(host="http://127.0.0.1:8889", username="", password="",
                    cookie_dir=tmp_path / "cookies")
    server = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db",
                          global_concurrency=1, admin_users=["root"])
    state = build_state(server, erp, login_impl=fake_erp_login,
                        alive_impl=lambda _auth: True)
    with TestClient(create_app(state), base_url=BASE_URL) as c:
        yield c


def setup_admin_token(client: TestClient) -> tuple[dict, str]:
    """注册管理员 → 绑定 ERP → 申请 token，返回 (user, token)。"""
    resp = client.post("/v1/platform/register", json=ADMIN)
    assert resp.status_code == 201, resp.text
    user = resp.json()["user"]
    assert user["role"] == "admin"

    bind = client.post("/v1/platform/erp/bind",
                       json={"username": "21146", "password": "erp-secret"})
    assert bind.status_code == 200, bind.text

    token = client.post("/v1/platform/tokens", json={"label": "pytest"})
    assert token.status_code == 201, token.text
    return user, token.json()["token"]


def rpc(client: TestClient, key: str, payload: dict):
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {key}"}
    return client.post("/mcp", json=payload, headers=headers)


def initialize(client: TestClient, key: str) -> None:
    resp = rpc(client, key, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    })
    assert resp.status_code == 200, resp.text


def call_tool(client: TestClient, key: str, name: str,
              arguments: dict | None = None) -> dict:
    resp = rpc(client, key, {
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


# ══════════════════ 记录成功调用 ══════════════════
def test_successful_tool_call_is_logged(client: TestClient) -> None:
    user, key = setup_admin_token(client)
    initialize(client, key)
    call_tool(client, key, "erp_capabilities")

    body = client.get("/v1/admin/calls").json()
    assert body["items"], body
    item = body["items"][0]
    assert item["tool"] == "erp_capabilities"
    assert item["status"] == "success"
    assert item["duration_ms"] >= 0
    assert item["actor_id"] == user["id"]
    assert item["owner_name"] == "root"
    assert item["erp_username"] == "21146"
    assert item["token_id"]
    assert item["ip"] == "testclient"
    assert item["user_agent"] == "pytest-mcp-client/1.0"
    assert item["error"] is None


def test_failed_tool_call_is_logged_with_error(client: TestClient) -> None:
    _, key = setup_admin_token(client)
    initialize(client, key)
    # 假 ERP 会话（auth=object()）会在服务内部抛 AttributeError → 记为失败
    result = call_tool(client, key, "erp_quote_details", {"record_id": "no-such-record"})
    assert result.get("result", {}).get("isError") is True or "error" in result

    body = client.get("/v1/admin/calls", params={"status": "failure"}).json()
    assert body["items"], body
    item = body["items"][0]
    assert item["tool"] == "erp_quote_details"
    assert item["status"] == "failure"
    assert item["error"]
    assert item["arguments"] == {"record_id": "no-such-record"}


# ══════════════════ 敏感参数不落库 ══════════════════
def test_arguments_are_sanitized(client: TestClient) -> None:
    _, key = setup_admin_token(client)
    initialize(client, key)
    call_tool(client, key, "erp_quote_details", {
        "record_id": "abc", "password": "should-not-be-stored",
    })

    item = client.get("/v1/admin/calls").json()["items"][0]
    assert item["arguments"]["record_id"] == "abc"
    # 工具签名不接受 password：SDK 校验会丢掉它，落库的自然也没有
    assert "should-not-be-stored" not in str(item)


def test_sanitize_arguments_masks_sensitive_keys() -> None:
    from erp_tools.runtime.call_log import sanitize_arguments

    out = sanitize_arguments({
        "record_id": "abc",
        "password": "p@ssw0rd",
        "api_key": "erp_xxx",
        "note": "x" * 500,
    })
    assert out["record_id"] == "abc"
    assert out["password"] == "***"
    assert out["api_key"] == "***"
    assert len(out["note"]) <= 161


# ══════════════════ 过滤与分页 ══════════════════
def test_call_filters_and_pagination(client: TestClient) -> None:
    user, key = setup_admin_token(client)
    initialize(client, key)
    call_tool(client, key, "erp_capabilities")
    call_tool(client, key, "erp_whoami")

    by_tool = client.get("/v1/admin/calls", params={"tool": "whoami"}).json()
    assert {item["tool"] for item in by_tool["items"]} == {"erp_whoami"}

    by_actor = client.get("/v1/admin/calls", params={"actor": user["id"]}).json()
    assert len(by_actor["items"]) == 2

    first = client.get("/v1/admin/calls", params={"limit": 1}).json()
    assert first["has_more"] is True
    assert len(first["items"]) == 1
    second = client.get("/v1/admin/calls",
                        params={"limit": 1, "offset": 1}).json()
    assert second["items"][0]["id"] != first["items"][0]["id"]


def test_overview_and_user_detail_include_call_stats(client: TestClient) -> None:
    user, key = setup_admin_token(client)
    initialize(client, key)
    call_tool(client, key, "erp_capabilities")

    overview = client.get("/v1/admin/overview").json()
    assert overview["calls"]["total"] == 1

    detail = client.get(f"/v1/admin/users/{user['id']}").json()
    assert detail["call_count"] == 1
    assert detail["recent_calls"][0]["tool"] == "erp_capabilities"
