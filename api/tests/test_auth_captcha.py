"""验证码获取失败的错误分类：网络问题不能算「账号密码错」。

回归价值：`_fetch_captcha` 以前把**所有**异常都包成 `AuthError`，
于是「ERP 连不上」会变成 `401 invalid_credentials`，
不仅误导排查方向，还会白白消耗「连续失败 10 次锁定」的额度。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from erp_tools.core.auth import AuthManager
from erp_tools.core.config import ErpConfig
from erp_tools.core.errors import AuthError, NetworkError
from erp_tools.core.session import ErpClient


def _auth(tmp_path: Path) -> AuthManager:
    cfg = ErpConfig(host="http://127.0.0.1:9", username="21146", password="",
                    cookie_dir=tmp_path / "cookies")
    return AuthManager(ErpClient(cfg), interactive=False)


def test_captcha_connection_error_raises_network_error(monkeypatch, tmp_path) -> None:
    def boom(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr("requests.get", boom)

    with pytest.raises(NetworkError):
        _auth(tmp_path)._fetch_captcha()  # noqa: SLF001


def test_captcha_timeout_raises_network_error(monkeypatch, tmp_path) -> None:
    def boom(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr("requests.get", boom)

    with pytest.raises(NetworkError):
        _auth(tmp_path)._fetch_captcha()  # noqa: SLF001


def test_captcha_non_200_still_auth_error(monkeypatch, tmp_path) -> None:
    """端口通了但服务端报错（404 等）不属于网络故障，保持 AuthError。"""

    class _Resp:
        status_code = 404
        content = b""

    monkeypatch.setattr("requests.get", lambda *a, **kw: _Resp())

    with pytest.raises(AuthError):
        _auth(tmp_path)._fetch_captcha()  # noqa: SLF001
