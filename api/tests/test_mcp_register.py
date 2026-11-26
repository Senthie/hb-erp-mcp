"""apps.server：MCP 服务信息注册（用 SQLite 后端跑，不依赖 PostgreSQL）。

生产走 PostgreSQL（`ERPSRV_PG_DSN`），但两套存储共用 `normalize_registration`，
所以这里覆盖到的校验/幂等行为对 PG 同样成立。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from erp_tools.apps.server.app import create_app
from erp_tools.apps.server.config import ServerConfig
from erp_tools.apps.server.state import build_state
from erp_tools.core.config import ErpConfig

PAYLOAD = {
    "name": "HB-MCP",
    "version": "0.3.0",
    "endpoint": "https://erp-tools.lan:8443/mcp",
    "transport": "streamable-http",
    "environment": "prod",
    "owner": "陈小美",
    "description": "华博 ERP 工具网关",
    "metadata": {"region": "sz"},
}


def make_client(tmp_path: Path, *, register_secret: str = "") -> TestClient:
    erp = ErpConfig(host="http://127.0.0.1:8889", username="", password="",
                    cookie_dir=tmp_path / "cookies")
    server = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db",
                          global_concurrency=1, register_secret=register_secret)
    state = build_state(server, erp, login_impl=lambda _u, _p: None,
                        alive_impl=lambda _auth: True)
    return TestClient(create_app(state, enable_mcp=False))


@pytest.fixture()
def client(tmp_path: Path):
    with make_client(tmp_path) as c:
        yield c


# ══════════════════ 健康检查 ══════════════════
def test_healthz_reports_store_and_register_flag(client: TestClient) -> None:
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["service"] == "HB-MCP"
    assert body["store"] == "sqlite"
    assert body["register_secret_required"] is False


# ══════════════════ 注册 ══════════════════
def test_register_creates_service_without_api_key(client: TestClient) -> None:
    resp = client.post("/v1/mcp/register", json=PAYLOAD)
    assert resp.status_code == 201, resp.text

    body = resp.json()
    assert body["status"] == "registered"
    service = body["service"]
    assert service["name"] == "HB-MCP"
    assert service["version"] == "0.3.0"
    assert service["endpoint"] == "https://erp-tools.lan:8443/mcp"
    assert service["environment"] == "production", "prod 应被归一化成 production"
    assert service["owner"] == "陈小美"
    assert service["metadata"] == {"region": "sz"}
    assert service["id"] is not None


def test_register_is_idempotent_by_name(client: TestClient) -> None:
    first = client.post("/v1/mcp/register", json=PAYLOAD).json()["service"]
    second = client.post("/v1/mcp/register",
                         json={**PAYLOAD, "version": "0.4.0"}).json()["service"]

    assert second["id"] == first["id"]
    assert second["version"] == "0.4.0"

    listing = client.get("/v1/mcp/services").json()
    assert listing["count"] == 1
    assert listing["services"][0]["name"] == "HB-MCP"


def test_stdio_transport_needs_no_endpoint(client: TestClient) -> None:
    resp = client.post("/v1/mcp/register",
                       json={"name": "local-mcp", "transport": "stdio"})
    assert resp.status_code == 201
    assert resp.json()["service"]["endpoint"] is None


@pytest.mark.parametrize("payload, needle", [
    ({}, "服务名称"),
    ({"name": "HB-MCP", "endpoint": "ftp://files/mcp"}, "http"),
    ({"name": "HB-MCP", "transport": "carrier-pigeon"}, "传输方式"),
    ({"name": "HB-MCP", "environment": "moon"}, "环境"),
    ({"name": "HB-MCP", "tools": "many"}, "整数"),
    ({"name": "HB-MCP", "metadata": "text"}, "metadata"),
    ({"name": "x" * 65}, "最长"),
])
def test_register_rejects_bad_payload(client: TestClient, payload: dict,
                                      needle: str) -> None:
    resp = client.post("/v1/mcp/register", json=payload)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_service_info"
    assert needle in resp.json()["message"]


def test_register_rejects_non_json_body(client: TestClient) -> None:
    resp = client.post("/v1/mcp/register", content=b"name=HB-MCP",
                       headers={"content-type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 400


def test_register_secret_is_enforced(tmp_path: Path) -> None:
    with make_client(tmp_path, register_secret="s3cr3t") as c:
        denied = c.post("/v1/mcp/register", json={"name": "HB-MCP"})
        assert denied.status_code == 403
        assert denied.json()["error"] == "register_secret_invalid"

        allowed = c.post("/v1/mcp/register", json={"name": "HB-MCP"},
                         headers={"x-register-secret": "s3cr3t"})
        assert allowed.status_code == 201


def test_register_is_rate_limited(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        for _ in range(10):
            assert c.post("/v1/mcp/register", json={"name": "HB-MCP"}).status_code == 201

        resp = c.post("/v1/mcp/register", json={"name": "HB-MCP"})
        assert resp.status_code == 429
        assert resp.json()["error"] == "rate_limited"


# ══════════════════ 可视化界面入口 ══════════════════
def test_root_redirects_to_ui(client: TestClient) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/ui/"


def test_ui_is_public_and_serves_spa(client: TestClient) -> None:
    resp = client.get("/ui/")
    assert resp.status_code in (200, 503), resp.text
    assert "HB-MCP" in resp.text or "前端还没构建" in resp.text


def test_ui_deep_link_falls_back_to_index(client: TestClient) -> None:
    resp = client.get("/ui/register")
    assert resp.status_code in (200, 503), resp.text


# ══════════════════ 存储后端选择 ══════════════════
def test_build_store_requires_a_backend(tmp_path: Path) -> None:
    from erp_tools.apps.server.state import build_store
    from erp_tools.core.errors import ConfigError

    with pytest.raises(ConfigError, match="config.yaml"):
        build_store(ServerConfig(bind="127.0.0.1:8765"))


def test_pg_store_requires_dsn() -> None:
    from erp_tools.core.errors import ConfigError
    from erp_tools.runtime.pg_store import PgStore

    with pytest.raises(ConfigError):
        PgStore("   ")


def test_mask_dsn_hides_password() -> None:
    from erp_tools.runtime.pg_store import mask_dsn

    assert mask_dsn("postgresql://erp:s3cr3t@db:5432/erp") == \
        "postgresql://erp:***@db:5432/erp"
    assert mask_dsn("postgresql://db.example.com/erp") == \
        "postgresql://db.example.com/erp"
    # 密码里混进 @ 时也不能把尾巴漏出来
    assert mask_dsn("postgresql://erp:p@ss@db:5432/erp") == \
        "postgresql://erp:***@db:5432/erp"
