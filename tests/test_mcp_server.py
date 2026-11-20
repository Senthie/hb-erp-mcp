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
            "erp_quote_detail", "erp_attendance", "erp_price_list", "erp_price_detail",
            "erp_quote_new_form", "erp_quote_product_form", "erp_quote_options",
            "erp_quote_customer_search",
            "erp_quote_create", "erp_quote_delete",
            "erp_quote_details", "erp_quote_update", "erp_quote_update_product"} <= names
    # 四个新模块（列表-产品核价 / 生产施工单 / 采购订单 / 客户订单）
    assert {"erp_costing_list", "erp_production_list", "erp_production_detail",
            "erp_production_node", "erp_purchase_list", "erp_purchase_detail",
            "erp_purchase_node", "erp_sales_order_list", "erp_sales_order_detail",
            "erp_sales_order_node", "erp_page_catalog"} <= names
    # prepare 已并入 create（confirm=false 就是预览），不应再单独暴露
    assert "erp_quote_prepare" not in names

    by_name = {t["name"]: t for t in tools}
    props = by_name["erp_quote_list"]["inputSchema"]["properties"]
    assert {"from_date", "to_date", "where", "customer", "limit"} <= set(props)
    # 参数要带说明与示例，否则模型只能猜
    assert props["where"].get("description") and props["where"].get("examples")
    price_props = by_name["erp_price_list"]["inputSchema"]["properties"]
    assert {"from_date", "to_date", "date_type", "filters", "where"} <= set(price_props)
    assert price_props["date_type"]["default"] == "制单日期"
    detail_props = by_name["erp_price_detail"]["inputSchema"]["properties"]
    assert {"record_id", "bdbh", "row", "include_description"} <= set(detail_props)
    assert by_name["erp_quote_detail"]["inputSchema"]["properties"].keys() >= {"record_id", "bdbh"}
    assert by_name["erp_attendance"]["inputSchema"]["required"] == ["month"]


def test_page_catalog_lists_all_three_modules(mcp_client) -> None:
    """数据页面清单必须能离线返回（不访问 ERP），并列出全部子页。"""
    client, _state = mcp_client
    key = get_key(client)
    initialize(client, key)

    body = call_tool(client, key, "erp_page_catalog")
    assert "error" not in body, body
    payload = json.loads(body["result"]["content"][0]["text"])
    modules = {m["module"]: m for m in payload["modules"]}
    assert set(modules) == {"production", "purchase", "costing", "sales_order"}
    assert modules["production"]["pages"]["list"]["qxid"] == "740"
    assert modules["purchase"]["pages"]["list"]["qxid"] == "266"
    assert modules["costing"]["pages"]["list"]["qxid"] == "1281"
    assert modules["sales_order"]["pages"]["list"]["qxid"] == "687"
    production_nodes = {n["node"] for n in modules["production"]["nodes"]}
    assert {"part_materials", "part_process", "product_check", "sgkb"} <= production_nodes
    # 采购订单没有弹窗数据子页
    assert [n["node"] for n in modules["purchase"]["nodes"]] == ["supplier"]
    # 客户订单 20 个子页，且都标到 erp_sales_order_node
    sales_nodes = {n["node"]: n for n in modules["sales_order"]["nodes"]}
    assert len(sales_nodes) == 20
    assert sales_nodes["quantities"]["page"] == "Add_CPSL.aspx"
    assert sales_nodes["quantities"]["mcp"] == "erp_sales_order_node"

    only = call_tool(client, key, "erp_page_catalog", {"module": "生产施工单"})
    assert "error" not in only, only
    single = json.loads(only["result"]["content"][0]["text"])
    assert single["count"] == 1


def test_create_tool_declares_product_structure(mcp_client) -> None:
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
    """tools/list 每次对话都会全量下发，体积涨上去就是常驻成本。

    预算 31000（2026-11 从 22000 → 30000 → 31000）：28 个工具里 `erp_quote_create` 一个就占
    11209 字符（51 个产品字段的结构化 schema，是它存在的理由）；四个模块的列表/详情/子页
    （`erp_costing_list` / `erp_production_*` / `erp_purchase_*` / `erp_sales_order_*` /
    `erp_page_catalog`）都是「参数即文档」的扁平工具，合计约 8K。
    再加工具前先看 `tmp/schema-size.py` 的分布，别让列表类工具把余量吃掉。
    """
    client, _state = mcp_client
    key = get_key(client)
    initialize(client, key)
    total = sum(
        len(json.dumps(t["inputSchema"], ensure_ascii=False))
        for t in list_tools(client, key)
    )
    assert total <= 31_000, f"inputSchema 合计 {total} 字符，超预算 31000"


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


def test_capabilities_uses_verified_module_qxids(mcp_client) -> None:
    """四个新模块的 QXID 必须用实测值，不能用菜单探测值。

    fake session 里有 `BaoJia/BJ_KHBJXQ/Index.aspx` 但没有 `BJ_KHBJ/Index.aspx`，
    所以模块应为不可用；关键是**返回里不能出现 860/983** 这类「同路径第一个菜单」的 QXID。
    """
    client, _state = mcp_client
    key = get_key(client)
    initialize(client, key)

    payload = json.loads(call_tool(client, key, "erp_capabilities")["result"]["content"][0]["text"])
    modules = payload["modules"]
    assert modules["costing"]["list_qxid"] == "1281"
    assert modules["production"]["list_qxid"] == "740"
    assert modules["purchase"]["list_qxid"] == "266"
    assert modules["sales_order"]["list_qxid"] == "687"
    assert modules["costing"]["list_available"] is False
    assert set(payload["module_missing"]) == {"costing", "production", "purchase",
                                              "sales_order"}


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
