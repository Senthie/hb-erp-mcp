"""附件下载代理：签名、路径白名单、`/v1/files` 端点（上游用桩）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from erp_tools.apps.server.app import create_app
from erp_tools.apps.server.config import ServerConfig
from erp_tools.apps.server.state import build_state
from erp_tools.core.config import ErpConfig
from erp_tools.core.errors import AuthError
from erp_tools.runtime import file_proxy
from erp_tools.runtime.sessions import UserSession

FJ_PATH = "/Admin/FuJian/BJ_KHBJXQ/Picture/4f8c1e2a-0000-4000-8000-abcdef012345.zip"
FJ_URL = "http://192.168.1.199:8889" + FJ_PATH
BLOB = b"PK\x03\x04" + b"x" * 100


@pytest.fixture(autouse=True)
def _secret():
    file_proxy.configure("unit-test-secret")
    yield
    file_proxy.reset()


# ══════════════════ 路径与白名单 ══════════════════
def test_path_of_strips_host_and_query() -> None:
    assert file_proxy.path_of(FJ_URL) == FJ_PATH
    assert file_proxy.path_of(FJ_PATH + "?time=1") == FJ_PATH
    assert file_proxy.path_of("/Admin/FuJian/a/b.pdf") == "/Admin/FuJian/a/b.pdf"


def test_is_proxyable_only_attachment_dir() -> None:
    assert file_proxy.is_proxyable(FJ_URL)
    assert file_proxy.is_proxyable("/admin/fujian/x.pdf")          # 大小写不敏感
    assert file_proxy.is_proxyable("http://evil/Admin/FuJian/")     # 主机名不参与判断，
    # 真正的目标主机由网关按 ERP_HOST 决定，不会听调用方的
    assert file_proxy.is_proxyable("http://192.168.1.199:8889/Admin/index.html") is False
    assert file_proxy.is_proxyable("http://169.254.169.254/latest/meta-data/") is False
    assert file_proxy.is_proxyable("") is False


def test_is_proxyable_case_insensitive() -> None:
    assert file_proxy.is_proxyable("/ADMIN/FUJIAN/A/B.PDF")


# ══════════════════ 签名 ══════════════════
def test_sign_and_verify_roundtrip() -> None:
    token = file_proxy.sign(FJ_PATH)
    assert file_proxy.verify(FJ_PATH, token)
    # 路径被改 → 签名失效
    assert file_proxy.verify("/Admin/FuJian/other.zip", token) is False
    # 令牌被改 → 失效
    assert file_proxy.verify(FJ_PATH, token[:-1] + ("0" if token[-1] != "0" else "1")) is False


def test_verify_rejects_expired_and_garbage() -> None:
    expired = file_proxy.sign(FJ_PATH, ttl=-10)
    assert file_proxy.verify(FJ_PATH, expired) is False
    for bad in ("", "abc", "123", "9999999999", ".", "x.y"):
        assert file_proxy.verify(FJ_PATH, bad) is False


def test_verify_uses_same_path_even_with_query() -> None:
    token = file_proxy.sign(FJ_URL)
    assert file_proxy.verify(FJ_PATH + "?time=99", token)


def test_build_url_shape() -> None:
    url = file_proxy.build_url(FJ_URL, base="http://10.0.0.5:8765")
    assert url.startswith("http://10.0.0.5:8765/v1/files?path=")
    assert "t=" in url
    path_part = url.split("path=", 1)[1].split("&", 1)[0]
    token = url.rsplit("t=", 1)[1]
    assert file_proxy.verify(path_part.replace("%2F", "/"), token)


def test_build_url_empty_for_non_attachment() -> None:
    assert file_proxy.build_url("http://192.168.1.199:8889/Admin/index.html") == ""


def test_build_url_carries_display_name() -> None:
    url = file_proxy.build_url(FJ_URL, base="http://gw:8765", name="询价原始件（BJXQ1）.zip")
    assert "&n=%E8%AF%A2" in url
    # n 不参与签名：去掉它仍然能验
    path_part = url.split("path=", 1)[1].split("&", 1)[0]
    token = url.rsplit("t=", 1)[1].split("&", 1)[0]
    assert file_proxy.verify(path_part, token)


def test_download_name_sanitizes() -> None:
    assert file_proxy.download_name(FJ_PATH, "报价.zip") == "报价.zip"
    assert file_proxy.download_name(FJ_PATH, "") == FJ_PATH.rsplit("/", 1)[-1]
    assert file_proxy.download_name(FJ_PATH, "../../etc/passwd") == "passwd"
    assert file_proxy.download_name(FJ_PATH, 'a\r\nb"c.zip') == "abc.zip"
    assert file_proxy.download_name(FJ_PATH, "x" * 200).endswith(".zip")


def test_filename_of_keeps_unicode() -> None:
    assert file_proxy.filename_of("/Admin/FuJian/a/%E6%8A%A5%E4%BB%B7.zip") == "报价.zip"


def test_load_secret_persists(tmp_path: Path) -> None:
    file_proxy.reset()
    db = tmp_path / "auth.db"
    first = file_proxy.load_secret(db)
    assert (tmp_path / "proxy.secret").is_file()
    file_proxy.reset()
    assert file_proxy.load_secret(db) == first


# ══════════════════ /v1/files 端点 ══════════════════
def _fake_session() -> UserSession:
    return UserSession(username="21146", auth=object(), user_id="1234",
                       pages={"BaoJia/BJ_KHBJXQ/Index.aspx": "876"})


def _ok_login(_username: str, _password: str) -> UserSession:
    raise AuthError("不需要登录")


def _make_client(tmp_path: Path) -> TestClient:
    erp = ErpConfig(host="http://127.0.0.1:8889", username="21146", password="",
                    cookie_dir=tmp_path / "cookies")
    server = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db",
                          global_concurrency=2)
    state = build_state(server, erp, login_impl=_ok_login,
                        alive_impl=lambda _auth: True)
    return TestClient(create_app(state, enable_mcp=False))


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    """上游用桩：只认 8889 端口的那个候选地址。"""
    import requests

    calls: list[str] = []

    class _Resp:
        def __init__(self, content: bytes) -> None:
            self.content = content
            self.status_code = 200
            self.headers = {"Content-Type": "application/x-zip-compressed"}

    def fake_get(url, **_kw):
        calls.append(url)
        if "192.168.1.199" in url or "127.0.0.1:8889" in url:
            return _Resp(BLOB)
        raise requests.RequestException("unreachable")

    monkeypatch.setattr(requests, "get", fake_get)
    c = _make_client(tmp_path)
    c.calls = calls            # type: ignore[attr-defined]
    yield c
    c.close()
    file_proxy.reset()


def test_files_endpoint_serves_bytes(client) -> None:
    token = file_proxy.sign(FJ_PATH)
    resp = client.get("/v1/files", params={"path": FJ_PATH, "t": token})
    assert resp.status_code == 200
    assert resp.content == BLOB
    assert "zip" in resp.headers["content-type"]
    assert resp.headers["content-disposition"].startswith("inline;")
    assert resp.headers["x-erp-source"].endswith(FJ_PATH)


def test_files_endpoint_needs_no_api_key(client) -> None:
    """链接里没有 api-key，浏览器直接打开也能下。"""
    token = file_proxy.sign(FJ_PATH)
    resp = client.get("/v1/files", params={"path": FJ_PATH, "t": token})
    assert resp.status_code == 200


def test_files_endpoint_head(client) -> None:
    token = file_proxy.sign(FJ_PATH)
    resp = client.head("/v1/files", params={"path": FJ_PATH, "t": token})
    assert resp.status_code == 200
    assert resp.headers["content-length"] == str(len(BLOB))


def test_files_endpoint_uses_display_name(client) -> None:
    from urllib.parse import quote

    token = file_proxy.sign(FJ_PATH)
    resp = client.get("/v1/files", params={"path": FJ_PATH, "t": token,
                                           "n": "询价原始件（BJXQ1）.zip"})
    assert resp.status_code == 200
    disp = resp.headers["content-disposition"]
    assert "filename*=UTF-8''" + quote("询价原始件（BJXQ1）.zip", safe="") in disp
    # 纯中文时 filename 兜底退回物理文件名，不能是一串问号
    assert "?" not in disp
    assert FJ_PATH.rsplit("/", 1)[-1] in disp


def test_files_endpoint_rejects_bad_signature(client) -> None:
    resp = client.get("/v1/files", params={"path": FJ_PATH, "t": "1.bad"})
    assert resp.status_code == 403
    assert resp.json()["error"] == "bad_signature"


def test_files_endpoint_rejects_other_paths(client) -> None:
    for path in ("/Admin/Login.aspx", "http://169.254.169.254/latest/meta-data/", ""):
        resp = client.get("/v1/files", params={"path": path, "t": file_proxy.sign(path or "/")})
        assert resp.status_code in (400, 403)
    assert client.calls == []          # 一个请求都没发出去


def test_files_endpoint_tampered_path_is_denied(client) -> None:
    token = file_proxy.sign(FJ_PATH)
    other = "/Admin/FuJian/BJ_KHBJXQ/Picture/other.zip"
    resp = client.get("/v1/files", params={"path": other, "t": token})
    assert resp.status_code == 403


def test_files_endpoint_expired(client) -> None:
    token = file_proxy.sign(FJ_PATH, ttl=-1)
    assert client.get("/v1/files", params={"path": FJ_PATH, "t": token}).status_code == 403


def test_files_endpoint_upstream_failure(client, monkeypatch) -> None:
    import requests

    def boom(url, **_kw):
        raise requests.RequestException("down")

    monkeypatch.setattr(requests, "get", boom)
    token = file_proxy.sign(FJ_PATH)
    resp = client.get("/v1/files", params={"path": FJ_PATH, "t": token})
    assert resp.status_code == 502
    assert resp.json()["error"] == "upstream_failed"


def test_public_base_prefers_explicit(monkeypatch) -> None:
    monkeypatch.setenv("ERP_PUBLIC_BASE", "https://erp-tools.lan")
    assert file_proxy.public_base() == "https://erp-tools.lan"
    assert file_proxy.public_base("http://10.1.1.1:8765") == "http://10.1.1.1:8765"


def test_middleware_puts_request_base_into_context(client) -> None:
    """工具里生成的代理链接要跟客户端实际访问的地址一致。"""
    from erp_tools.runtime.context import base_url

    seen: list[str] = []
    from erp_tools.apps.server.middleware import BearerAuthMiddleware

    original = BearerAuthMiddleware._dispatch

    async def spy(self, scope, receive, send, method, path):
        seen.append(base_url())
        await original(self, scope, receive, send, method, path)

    BearerAuthMiddleware._dispatch = spy        # type: ignore[method-assign]
    try:
        client.get("/healthz")
    finally:
        BearerAuthMiddleware._dispatch = original  # type: ignore[method-assign]
    assert seen and seen[0].startswith("http://testserver")


def test_ttl_env_is_honoured(monkeypatch) -> None:
    monkeypatch.setenv("ERP_FILE_PROXY_TTL", "120")
    assert file_proxy.ttl_default() == 120
    token = file_proxy.sign(FJ_PATH)
    exp = int(token.split(".", 1)[0])
    assert 60 <= exp - int(time.time()) <= 130
