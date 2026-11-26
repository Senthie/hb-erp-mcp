"""`erp init` 的配置生成：CLI / MCP 两种模式的最小配置 + 自动派生项。

覆盖「生成物不用回填」的几条：
  * PostgreSQL DSN 由 host/port/user/password/db 拼好（密码做 percent-encoding）
  * Host 白名单由 bind / public_host 派生
  * 注册密钥 `auto` 首次生成、之后复用
  * TLS `auto` 在非回环监听时自签证书，回环时跳过
  * MCP 客户端配置模板 / 写入真实 token
"""

from __future__ import annotations

import json

from erp_tools.apps.server.config import ServerConfig
from erp_tools.core import provision as prov
from erp_tools.core.provision import (
    assemble_pg_dsn,
    cli_config,
    derive_allow_hosts,
    mcp_config,
    server_base_url,
)


# ══════════════════ CLI 模式：只留必要项 ══════════════════
def test_cli_config_is_minimal() -> None:
    data = cli_config("http://10.0.0.5:8889", username="21146")

    assert data == {
        "mode": "cli",
        "erp": {"host": "http://10.0.0.5:8889", "username": "21146"},
    }
    assert "server" not in data, "CLI 模式不该出现网关参数"


def test_cli_config_keeps_cookie_dir_when_given() -> None:
    data = cli_config("http://10.0.0.5:8889", cookie_dir="./cookies")
    assert data["runtime"] == {"cookie_dir": "./cookies"}


# ══════════════════ MCP 模式：生成物全部 auto ══════════════════
def test_mcp_config_marks_generated_values_auto() -> None:
    data = mcp_config(erp_host="http://10.0.0.5:8889", bind="0.0.0.0:8765",
                      admin_users=["Root"])

    server = data["server"]
    assert data["mode"] == "mcp"
    assert server["tls"] == prov.AUTO
    assert server["allow_hosts"] == prov.AUTO
    assert server["register_secret"] == prov.AUTO
    assert server["storage"] == "sqlite"
    assert server["admin_users"] == ["root"]
    assert "tls_cert" not in server and "tls_key" not in server, "证书不再手填路径"


def test_pg_dsn_is_assembled_and_escaped() -> None:
    data = mcp_config(
        erp_host="http://10.0.0.5:8889",
        bind="127.0.0.1:8765",
        storage="postgres",
        pg={"host": "db", "port": 5433, "user": "erp",
            "password": "p@ss/w:rd", "db": "erp_db"},
    )

    assert data["server"]["pg_dsn"] == "postgresql://erp:p%40ss%2Fw%3Ard@db:5433/erp_db"
    assert "sqlite_path" not in data["server"]


def test_assemble_pg_dsn_without_password() -> None:
    assert assemble_pg_dsn(host="127.0.0.1", port=5432, user="erp",
                           password="", db="erp") == "postgresql://erp:@127.0.0.1:5432/erp"


def test_derive_allow_hosts_from_bind_and_public_host() -> None:
    # 回环监听不需要白名单
    assert derive_allow_hosts("127.0.0.1:8765") == []
    # 通配监听：换成客户端真实会用的地址，并把对外域名加进去
    hosts = derive_allow_hosts("0.0.0.0:8765", "erp.lan:8443")
    assert "erp.lan:8443" in hosts
    assert "127.0.0.1:8765" in hosts
    assert all(not h.startswith("0.0.0.0") for h in hosts)
    # 指定网卡地址：自己 + 对外域名
    assert derive_allow_hosts("192.168.1.10:8765", "erp.lan:8443") == [
        "192.168.1.10:8765", "erp.lan:8443",
    ]


def test_server_base_url_prefers_public_host() -> None:
    assert server_base_url({"bind": "0.0.0.0:8765", "public_host": "erp.lan:8443"}) \
        == "https://erp.lan:8443"
    assert server_base_url({"bind": "127.0.0.1:8765"}) == "http://127.0.0.1:8765"


# ══════════════════ 解析回 ServerConfig ══════════════════
def test_generated_yaml_round_trips_to_server_config(tmp_path) -> None:
    data = mcp_config(erp_host="http://10.0.0.5:8889", bind="127.0.0.1:8765",
                      storage="sqlite", sqlite_path="auth.db", admin_users=["root"])
    path = prov.write_config(tmp_path / "config.yaml", data)

    cfg = ServerConfig.from_config_file(path)

    assert cfg.bind == "127.0.0.1:8765"
    assert cfg.db_path is not None and cfg.db_path.name == "auth.db"
    assert cfg.admin_users == ["root"]
    assert cfg.tls_mode == prov.AUTO
    assert cfg.validate() == [], cfg.validate()


def test_public_host_implies_proxy_mode(tmp_path, monkeypatch) -> None:
    """docker + nginx 形态：给了对外地址就不再自己签 TLS，并信任 XFF。"""
    monkeypatch.setenv("ERP_DATA_DIR", str(tmp_path))
    data = mcp_config(erp_host="http://10.0.0.5:8889", bind="0.0.0.0:8765",
                      storage="postgres", pg={"host": "db", "password": "p"},
                      public_host="erp-tools.lan:8443")

    server = data["server"]
    assert server["tls"] == "off"
    assert server["allow_plaintext"] is True
    assert server["trust_proxy"] is True
    assert server["pg_dsn"] == "postgresql://erp:p@db:5432/erp"

    cfg = ServerConfig.from_settings(data).resolve()
    assert cfg.tls_enabled is False
    assert "erp-tools.lan:8443" in cfg.allow_hosts
    assert cfg.validate() == [], cfg.validate()
    assert (tmp_path / "register.secret").is_file(), "注册密钥应自动生成"


def test_write_config_merges_existing_sections(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("qxid:\n  quote: '876'\n", encoding="utf-8")

    prov.write_config(path, cli_config("http://10.0.0.5:8889"))
    text = path.read_text(encoding="utf-8")

    assert "quote" in text, "手工调过的段落不能被 init 抹掉"
    assert "http://10.0.0.5:8889" in text


# ══════════════════ TLS 自动签发 ══════════════════
def test_tls_auto_on_loopback_stays_off(tmp_path) -> None:
    cfg = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db",
                       tls_mode=prov.AUTO).resolve()

    assert cfg.tls_enabled is False
    assert cfg.tls_mode == "off"


def test_tls_auto_generates_self_signed_cert(tmp_path) -> None:
    cfg = ServerConfig(bind="0.0.0.0:8765", db_path=tmp_path / "auth.db",
                       tls_mode=prov.AUTO, allow_hosts=["erp-tools.lan:8765"]).resolve()

    assert cfg.tls_enabled is True, cfg.notes
    assert (tmp_path / "tls" / "erp-tools.crt").is_file()
    assert (tmp_path / "tls" / "erp-tools.key").is_file()

    # 第二次启动复用同一张证书，不重新签发
    again = ServerConfig(bind="0.0.0.0:8765", db_path=tmp_path / "auth.db",
                         tls_mode=prov.AUTO, allow_hosts=["erp-tools.lan:8765"]).resolve()
    assert again.tls_cert == cfg.tls_cert


# ══════════════════ MCP 客户端配置 ══════════════════
def test_mcp_json_template_has_placeholder(tmp_path) -> None:
    path = prov.write_mcp_json(tmp_path / "mcp.json", "http://127.0.0.1:8765")

    config = json.loads(path.read_text(encoding="utf-8"))
    server = config["servers"]["hb-mcp"]
    assert server["url"] == "http://127.0.0.1:8765/mcp"
    assert "Bearer" in server["headers"]["Authorization"]
    assert "<你的 MCP token>" in server["headers"]["Authorization"]


def test_mcp_json_with_real_key(tmp_path) -> None:
    path = prov.write_mcp_json(tmp_path / "mcp.json", "https://erp.lan:8443", "erp_abc123")

    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["servers"]["hb-mcp"]["headers"]["Authorization"] == "Bearer erp_abc123"


# ══════════════════ CLI 接线（erp init --yes）══════════════════
def test_cli_init_cli_mode(tmp_path, monkeypatch, capsys) -> None:
    from erp_tools.cli import main

    monkeypatch.setenv("ERP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "config.yaml"

    code = main(["init", "--mode", "cli", "--yes", "--config", str(target),
                 "--host", "http://10.0.0.5:8889", "--user", "21146"])

    assert code == 0, capsys.readouterr().err
    text = target.read_text(encoding="utf-8")
    assert "mode: cli" in text
    assert "http://10.0.0.5:8889" in text
    assert "21146" in text


def test_cli_init_mcp_mode_writes_config_and_mcp_json(tmp_path, monkeypatch, capsys) -> None:
    from erp_tools.cli import main

    monkeypatch.setenv("ERP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "config.yaml"
    snippet = tmp_path / "mcp.json"

    code = main([
        "init", "--mode", "mcp", "--yes",
        "--config", str(target),
        "--host", "http://10.0.0.5:8889",
        "--bind", "127.0.0.1:8765",
        "--admin-user", "root",
        "--mcp-json", str(snippet),
    ])

    assert code == 0, capsys.readouterr().err
    text = target.read_text(encoding="utf-8")
    assert "mode: mcp" in text
    assert "tls: auto" in text
    assert "register_secret: auto" in text
    assert json.loads(snippet.read_text(encoding="utf-8"))["servers"]["hb-mcp"]
