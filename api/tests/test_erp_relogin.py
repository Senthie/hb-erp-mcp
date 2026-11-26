"""ERP 会话自动续期：把密码加密存库，过期时自动重登，用户无感。

覆盖四层：
  * `SecretBox`：AES-GCM 往返、密钥文件复用、密文被篡改/换密钥时失败
  * 存储：`erp_credentials` 的写入 / 读取 / 删除（SQLite 与 PG 同构）
  * `SessionProvider`：探测失败 → 自动重登 → 会话被替换、key 不作废；失败则退避
  * 接口：绑定 ERP 即存密码；`/v1/platform/me` 报告是否已存；可主动删除；
    **密码明文不出现在任何响应 / 审计 / 调用日志里**
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from erp_tools.apps.server.app import create_app
from erp_tools.apps.server.config import ServerConfig
from erp_tools.apps.server.state import build_state
from erp_tools.core.config import ErpConfig
from erp_tools.core.errors import AuthError
from erp_tools.runtime.secrets_box import KEY_NAME, SecretBox
from erp_tools.runtime.sessions import SessionProvider, UserSession

BASE_URL = "http://127.0.0.1:8765"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
PROTOCOL_VERSION = "2025-06-18"
USER = {"username": "sora", "password": "a-good-password"}
ERP = {"username": "21146", "password": "erp-secret"}


def fake_erp_login(username: str, password: str) -> UserSession:
    if password != "erp-secret":
        raise AuthError("账号或密码错误")
    return UserSession(
        username=username, auth=object(), user_id="1234",
        pages={"BaoJia/BJ_KHBJXQ/Index.aspx": "876"},
    )


# ══════════════════ SecretBox ══════════════════
def test_secret_box_round_trip(tmp_path: Path) -> None:
    box = SecretBox.from_file(tmp_path / KEY_NAME)
    token = box.encrypt("p@ssw0rd:中文")

    assert token.startswith("v1:")
    assert "p@ssw0rd" not in token, "密文里不能出现明文"
    assert box.decrypt(token) == "p@ssw0rd:中文"


def test_secret_box_reuses_key_file(tmp_path: Path) -> None:
    first = SecretBox.from_file(tmp_path / KEY_NAME)
    token = first.encrypt("secret")

    # 第二次启动读同一个文件：仍能解开（重启不会让密文失效）
    again = SecretBox.from_file(tmp_path / KEY_NAME)
    assert again.decrypt(token) == "secret"


def test_secret_box_rejects_tampered_or_foreign_ciphertext(tmp_path: Path) -> None:
    box = SecretBox.from_file(tmp_path / KEY_NAME)
    other = SecretBox.from_file(tmp_path / "other.key")

    assert other.try_decrypt(box.encrypt("secret")) is None, "换密钥必须解不开"
    assert box.try_decrypt("v1:AAAA") is None
    assert box.try_decrypt("garbage") is None
    assert box.try_decrypt(None) is None


# ══════════════════ 存储 ══════════════════
def test_erp_credential_store_upserts_and_deletes(tmp_path: Path) -> None:
    from erp_tools.runtime.auth_store import AuthStore

    store = AuthStore(tmp_path / "auth.db").init_schema()
    try:
        assert store.get_erp_secret("21146") is None

        store.set_erp_secret("21146", "v1:first")
        assert store.get_erp_secret("21146") == "v1:first"

        store.set_erp_secret("21146", "v1:second")   # 重新绑定覆盖
        assert store.get_erp_secret("21146") == "v1:second"

        assert store.delete_erp_secret("21146") is True
        assert store.get_erp_secret("21146") is None
        assert store.delete_erp_secret("21146") is False
    finally:
        store.close()


# ══════════════════ SessionProvider：自动重登 ══════════════════
def _provider(tmp_path: Path, *, alive: bool, passwords: dict[str, str] | None = None,
              min_interval: float = 60.0):
    logins: list[tuple[str, str]] = []

    def login(username: str, password: str) -> UserSession:
        logins.append((username, password))
        if password != "erp-secret":
            raise AuthError("密码变了")
        return UserSession(username=username, auth=object(), user_id="1234")

    cfg = ErpConfig(host="http://127.0.0.1:8889", cookie_dir=tmp_path / "c")
    store = passwords if passwords is not None else {"21146": "erp-secret"}
    provider = SessionProvider(
        cfg, global_concurrency=1, login_impl=login,
        alive_impl=lambda _auth: alive,
        secret_lookup=store.get, relogin_min_interval=min_interval,
    )
    return provider, logins


def test_provider_relogins_when_probe_says_dead(tmp_path: Path) -> None:
    provider, logins = _provider(tmp_path, alive=False)

    session = provider.login("21146", "erp-secret")     # 第一次登录
    assert logins == [("21146", "erp-secret")]

    assert provider.is_alive("21146") is True, "探测失败应自动重登而不是判死"
    assert logins == [("21146", "erp-secret"), ("21146", "erp-secret")]
    assert provider.get("21146") is not session, "重登后应换成一个新会话对象"
    assert provider.relogin_state("21146").attempts == 1


def test_provider_without_stored_password_reports_expired(tmp_path: Path) -> None:
    provider, logins = _provider(tmp_path, alive=False, passwords={})

    provider.login("21146", "erp-secret")
    assert provider.is_alive("21146") is False
    assert len(logins) == 1, "没有密码就不该反复尝试"
    assert provider.get("21146") is None


def test_provider_backs_off_after_relogin_failure(tmp_path: Path) -> None:
    provider, logins = _provider(tmp_path, alive=False,
                                 passwords={"21146": "wrong-password"})
    provider.login("21146", "erp-secret")

    assert provider.is_alive("21146") is False
    assert provider.relogin_state("21146").failures == 1
    assert len(logins) == 2

    # 冷却期内不再重试（避免把 ERP 账号打到锁定）
    assert provider.is_alive("21146", refresh=True) is False
    assert len(logins) == 2


# ══════════════════ 接口：绑定即存密码 ══════════════════
def _client(tmp_path: Path, *, alive: list[bool] | None = None,
            store_erp_password: bool = True):
    flag = alive if alive is not None else [True]

    def login(username: str, password: str) -> UserSession:
        return fake_erp_login(username, password)

    erp = ErpConfig(host="http://127.0.0.1:8889", username="", password="",
                    cookie_dir=tmp_path / "cookies")
    server = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db",
                          global_concurrency=1, admin_users=["root"],
                          store_erp_password=store_erp_password)
    state = build_state(server, erp, login_impl=login,
                        alive_impl=lambda _auth: flag[0])
    return TestClient(create_app(state), base_url=BASE_URL), state, flag


def _bound(client: TestClient) -> str:
    assert client.post("/v1/platform/register", json=USER).status_code == 201
    resp = client.post("/v1/platform/erp/bind", json=ERP)
    assert resp.status_code == 200, resp.text
    token = client.post("/v1/platform/tokens", json={"label": "pytest"})
    assert token.status_code == 201, token.text
    return token.json()["token"]


def test_bind_stores_encrypted_password_not_plaintext(tmp_path: Path) -> None:
    client, state, _ = _client(tmp_path)
    with client:
        _bound(client)
        cipher = state.store.get_erp_secret("21146")

    assert cipher is not None and cipher.startswith("v1:")
    assert "erp-secret" not in cipher, "库里不能有明文密码"
    box = SecretBox.from_file(tmp_path / KEY_NAME)
    assert box.decrypt(cipher) == ERP["password"]


def test_me_reports_credential_state(tmp_path: Path) -> None:
    client, _state, _ = _client(tmp_path)
    with client:
        _bound(client)
        body = client.get("/v1/platform/me").json()
        assert body["erp_credential"] == {"store_enabled": True, "stored": True}

        removed = client.delete("/v1/platform/erp/credentials")
        assert removed.status_code == 200
        assert removed.json()["removed"] is True

        after = client.get("/v1/platform/me").json()
        assert after["erp_credential"]["stored"] is False


def test_no_api_response_leaks_the_password(tmp_path: Path) -> None:
    client, state, _ = _client(tmp_path)
    with client:
        _bound(client)
        for path in ("/v1/platform/me", "/v1/platform/tokens"):
            text = client.get(path).text
            assert ERP["password"] not in text, path
        # 审计日志（含绑定那条）里也不能出现
        audits = [entry.to_dict() for entry in state.store.list_audit(limit=50)]
        assert ERP["password"] not in json.dumps(audits, ensure_ascii=False)
        assert any(entry["action"] == "platform.erp.bind" for entry in audits)


# ══════════════════ MCP：过期后 token 依然能用 ══════════════════
def _mcp_call(client: TestClient, key: str, name: str) -> dict:
    for payload in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": name, "arguments": {}}},
    ):
        resp = client.post("/mcp", json=payload,
                           headers={**MCP_HEADERS,
                                    "Authorization": f"Bearer {key}"})
        assert resp.status_code == 200, resp.text
    return resp.json()


def test_mcp_call_survives_erp_session_expiry(tmp_path: Path) -> None:
    """ERP 会话过期 → 自动重登 → 工具照常返回、token 没被作废。"""
    flag = [True]
    client, state, _ = _client(tmp_path, alive=flag)
    with client:
        key = _bound(client)
        assert "isError" not in _mcp_call(client, key, "erp_capabilities")

        flag[0] = False                      # 模拟 ERP 会话过期
        body = _mcp_call(client, key, "erp_capabilities")
        assert "isError" not in body, body
        assert state.provider.is_alive("21146") is True
        # token 仍然有效
        assert client.post("/mcp",
                           json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                           headers={**MCP_HEADERS,
                                    "Authorization": f"Bearer {key}"}
                           ).status_code == 200
