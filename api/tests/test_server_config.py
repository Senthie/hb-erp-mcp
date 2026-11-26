"""`ServerConfig` 的监听/白名单/TLS 校验（离线，纯配置逻辑）。

重点：非回环地址默认**拒绝**明文，只有显式 `allow_plaintext`
（`--allow-plaintext` / `ERPSRV_ALLOW_PLAINTEXT=1`）才放行 —— docker + nginx
那种「TLS 由上游终止」的部署就靠它。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from erp_tools.apps.server.app import _transport_security
from erp_tools.apps.server.config import ServerConfig
from erp_tools.core.config import ErpConfig
from erp_tools.runtime.auth_store import AuthStore


def _state(server: ServerConfig, tmp_path):
    """只构造 `_transport_security` 需要的最小 state（不启动会话池）。"""
    return SimpleNamespace(
        server=server,
        store=AuthStore(server.db_path),
        erp=ErpConfig(host="http://127.0.0.1:8889", cookie_dir=tmp_path / "c"),
    )


# ══════════════════ 校验 ══════════════════
def test_loopback_needs_nothing(tmp_path) -> None:
    cfg = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db")
    assert cfg.validate() == []
    assert cfg.is_loopback is True


def test_team_mode_rejects_plaintext_by_default(tmp_path) -> None:
    cfg = ServerConfig(bind="0.0.0.0:8765", db_path=tmp_path / "auth.db",
                       allow_hosts=["erp-tools.lan:8765"])
    problems = cfg.validate()

    assert any("--tls-cert" in p for p in problems), problems
    assert all("--allow-host" not in p for p in problems), "白名单已给，不该再报"


def test_team_mode_with_tls_is_ok(tmp_path) -> None:
    cfg = ServerConfig(bind="0.0.0.0:8765", db_path=tmp_path / "auth.db",
                       tls_cert="c.pem", tls_key="k.pem",
                       allow_hosts=["erp-tools.lan:8765"])
    assert cfg.validate() == []
    assert cfg.tls_enabled is True


def test_plaintext_opt_in_makes_proxy_mode_valid(tmp_path) -> None:
    """docker compose + nginx：应用说 HTTP，TLS 在代理上。"""
    cfg = ServerConfig(bind="0.0.0.0:8765", db_path=tmp_path / "auth.db",
                       allow_plaintext=True, trust_proxy=True,
                       allow_hosts=["erp-tools.lan:8443"])
    assert cfg.validate() == []
    assert cfg.tls_enabled is False


def test_plaintext_opt_in_still_requires_allow_hosts(tmp_path) -> None:
    cfg = ServerConfig(bind="0.0.0.0:8765", db_path=tmp_path / "auth.db",
                       allow_plaintext=True)
    problems = cfg.validate()

    assert any("--allow-host" in p for p in problems), problems


def test_tls_pair_must_be_complete(tmp_path) -> None:
    cfg = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db",
                       tls_cert="only-cert.pem")
    assert any("成对出现" in p for p in cfg.validate())


def test_env_flags_are_parsed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ERPSRV_BIND", "0.0.0.0:9000")
    monkeypatch.setenv("ERPSRV_ALLOW_PLAINTEXT", "1")
    monkeypatch.setenv("ERPSRV_TRUST_PROXY", "yes")
    monkeypatch.setenv("ERPSRV_ALLOW_HOST", "erp.lan:9000; 10.0.0.5:9000")

    cfg = ServerConfig.from_env()

    assert cfg.bind == "0.0.0.0:9000"
    assert cfg.allow_plaintext is True
    assert cfg.trust_proxy is True
    assert cfg.allow_hosts == ["erp.lan:9000", "10.0.0.5:9000"]


def test_cli_flags_override_env(monkeypatch) -> None:
    monkeypatch.setenv("ERPSRV_BIND", "127.0.0.1:8765")
    args = SimpleNamespace(bind="0.0.0.0:8765", db=None, global_concurrency=None,
                           tls_cert=None, tls_key=None, allow_host=["erp.lan:8443"],
                           trust_proxy=True, allow_plaintext=True)

    cfg = ServerConfig.from_args(args)

    assert cfg.bind == "0.0.0.0:8765"
    assert cfg.allow_plaintext is True
    assert cfg.allow_hosts == ["erp.lan:8443"]


# ══════════════════ MCP transport security ══════════════════
def test_transport_security_none_without_allow_hosts(tmp_path) -> None:
    cfg = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db")
    assert _transport_security(_state(cfg, tmp_path)) is None


def test_transport_security_enabled_for_team_mode(tmp_path) -> None:
    cfg = ServerConfig(bind="0.0.0.0:8765", db_path=tmp_path / "auth.db",
                       allow_plaintext=True, allow_hosts=["erp-tools.lan:8443"])
    settings = _transport_security(_state(cfg, tmp_path))

    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True
    assert "erp-tools.lan:8443" in settings.allowed_hosts
    assert "https://erp-tools.lan:8443" in settings.allowed_origins


def test_transport_security_honours_allow_hosts_on_loopback(tmp_path) -> None:
    """nginx 与应用同容器/同 Pod（应用只听 127.0.0.1）时，白名单也必须生效，
    否则 SDK 默认的本机保护会把外部 Host 挡成 421。"""
    cfg = ServerConfig(bind="127.0.0.1:8765", db_path=tmp_path / "auth.db",
                       allow_hosts=["erp-tools.lan:8443"])
    settings = _transport_security(_state(cfg, tmp_path))

    assert settings is not None
    assert settings.allowed_hosts == ["erp-tools.lan:8443"]
