"""MCP Streamable HTTP 端点集成测试（不连真实 ERP）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from erp_tools.apps.server.app import create_app
from erp_tools.apps.server.config import ServerConfig
from erp_tools.apps.server.state import build_state
from erp_tools.core.config import ErpConfig
from erp_tools.runtime.sessions import UserSession

PROTOCOL_VERSION = "2025-06-18"
#: MCP 的 Streamable HTTP 会校验 Host（防 DNS rebinding），
#: 所以测试也要用一个「看起来像真实监听地址」的 Host。
BASE_URL = "http://127.0.0.1:8765"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def fake_session(username: str = "21146") -> UserSession:
    return UserSession(
        username=username,
        auth=object(),
        user_id="1234",
        pages={
            "BaoJia/BJ_KHBJXQ/Index.aspx": "876",
            "BaoJia/BJ_KHBJXQ/Add.aspx": "873",
        },
    )


def make_client(tmp_path: Path) -> tuple[TestClient, object]:
    erp = ErpConfig(
        host="http://127.0.0.1:8889", username="21146", password="",
        cookie_dir=tmp_path / "cookies",
    )
    server = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db",
                          global_concurrency=2)
    state = build_state(server, erp, login_impl=lambda u, p: fake_session(u),
                        alive_impl=lambda _auth: True)
    return TestClient(create_app(state), base_url=BASE_URL), state


@pytest.fixture()
def mcp_client(tmp_path: Path):
    client, state = make_client(tmp_path)
    with client as c:
        yield c, state
    state.close()


def get_key(client: TestClient) -> str:
    resp = client.post("/v1/auth/login", json={"username": "21146", "password": "secret"})
    assert resp.status_code == 200, resp.text
    return resp.json()["api_key"]


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


def list_tools(client: TestClient, key: str) -> list[dict]:
    resp = rpc(client, key, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "error" not in body, body
    return body["result"]["tools"]


def call_tool(client: TestClient, key: str, name: str, arguments: dict | None = None):
    resp = rpc(client, key, {
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


# ══════════════════ 认证 ══════════════════
def test_mcp_requires_bearer(mcp_client) -> None:
    client, _state = mcp_client
    resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                       headers=MCP_HEADERS)
    assert resp.status_code == 401
    assert resp.json()["error"] == "missing_bearer"


# ══════════════════ 协议 ══════════════════
def test_initialize_and_list_tools(mcp_client) -> None:
    client, _state = mcp_client
    key = get_key(client)
    initialize(client, key)

    tools = list_tools(client, key)
    names = {t["name"] for t in tools}
    assert {"erp_whoami", "erp_capabilities", "erp_quote_list",
            "erp_quote_detail", "erp_attendance",
            "erp_quote_new_form", "erp_quote_product_form", "erp_quote_options",
            "erp_quote_customer_search",
            "erp_quote_create", "erp_quote_delete",
            "erp_quote_details", "erp_quote_update", "erp_quote_update_product"} <= names
    # prepare 已并入 create（confirm=false 就是预览），不应再单独暴露
    assert "erp_quote_prepare" not in names

    by_name = {t["name"]: t for t in tools}
    props = by_name["erp_quote_list"]["inputSchema"]["properties"]
    assert {"from_date", "to_date", "where", "customer", "limit"} <= set(props)
    # 参数要带说明与示例，否则模型只能猜
    assert props["where"].get("description") and props["where"].get("examples")
    assert by_name["erp_quote_detail"]["inputSchema"]["properties"].keys() >= {"record_id", "bdbh"}
    assert by_name["erp_attendance"]["inputSchema"]["required"] == ["month"]


def test_create_tool_declares_product_structure(mcp_client) -> None:
    """`products[]` 必须是结构化的，否则模型看不到明细字段该填什么。"""
    client, _state = mcp_client
    key = get_key(client)
    initialize(client, key)
    by_name = {t["name"]: t for t in list_tools(client, key)}
    schema = by_name["erp_quote_create"]["inputSchema"]

    assert schema["required"] == ["products"]
    items = schema["properties"]["products"]["items"]
    assert items.get("$ref", "").endswith("QuoteProductInput")
    defs = schema.get("$defs") or {}
    assert set(defs["QuoteProductFields"]["properties"]) >= {"binding", "packing", "pallet"}
    assert len(defs["QuoteProductFields"]["properties"]) == 51
    assert defs["QuoteProductFields"]["properties"]["no_pallet"]["anyOf"][0]["type"] == "boolean"


def test_tools_list_schema_budget(mcp_client) -> None:
    """tools/list 每次对话都会全量下发，体积涨上去就是常驻成本。"""
    client, _state = mcp_client
    key = get_key(client)
    initialize(client, key)
    total = sum(
        len(json.dumps(t["inputSchema"], ensure_ascii=False))
        for t in list_tools(client, key)
    )
    assert total <= 20_000, f"inputSchema 合计 {total} 字符，超预算 20000"


# ══════════════════ 工具调用 ══════════════════
def test_whoami_uses_request_identity(mcp_client) -> None:
    """身份必须来自 Authorization 头（contextvar），不是工具参数。"""
    client, _state = mcp_client
    key = get_key(client)
    initialize(client, key)

    body = call_tool(client, key, "erp_whoami")
    result = body["result"]
    payload = result.get("structuredContent") or result["content"]
    assert "21146" in str(payload)
    assert result.get("isError") in (None, False)


def test_capabilities_reports_missing_pages(mcp_client) -> None:
    client, _state = mcp_client
    key = get_key(client)
    initialize(client, key)

    body = call_tool(client, key, "erp_capabilities")
    text = str(body["result"])
    assert "quote_index" in text
    assert "attendance" in text          # fake session 没有考勤页 → 出现在 missing
    assert "missing_pages" in text


def test_quote_detail_requires_an_argument(mcp_client) -> None:
    client, _state = mcp_client
    key = get_key(client)
    initialize(client, key)

    body = call_tool(client, key, "erp_quote_detail", {})
    result = body["result"]
    assert result.get("isError") is True
    assert "record_id" in str(result["content"])


def test_revoked_key_cannot_use_mcp(mcp_client) -> None:
    client, _state = mcp_client
    key = get_key(client)
    initialize(client, key)

    assert client.post("/v1/auth/logout",
                       headers={"Authorization": f"Bearer {key}"}).status_code == 204

    resp = rpc(client, key, {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
    assert resp.status_code == 401
    assert resp.json()["error"] == "api_key_revoked"
