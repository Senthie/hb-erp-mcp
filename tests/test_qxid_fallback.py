"""QXID 探测失败时的兜底逻辑（拿不到用户 ID / 菜单接口不可用）。"""

import pytest

from erp_tools.core.auth import DEFAULT_QXIDS, AuthManager
from erp_tools.core.config import ErpConfig
from erp_tools.core.errors import AuthError
from erp_tools.core.session import ErpClient

ATTENDANCE = "RenShi/RS_KQDA_M/Add.aspx"


def make_auth(tmp_path) -> AuthManager:
    cfg = ErpConfig(host="http://127.0.0.1:1", cookie_dir=tmp_path)
    return AuthManager(ErpClient(cfg), interactive=False)


def test_default_qxids_cover_known_pages():
    assert DEFAULT_QXIDS["baojia/bj_khbjxq/add.aspx"] == "873"
    assert DEFAULT_QXIDS["baojia/bj_khbjxq/index.aspx"] == "876"
    assert DEFAULT_QXIDS["renshi/rs_kqda_m/add.aspx"] == "2553"


def test_probe_failure_falls_back_to_default(monkeypatch, tmp_path):
    auth = make_auth(tmp_path)

    def boom(self, refresh=False):
        raise AuthError("无法获取用户 ID（cookie 不完整），无法探测 QXID")

    monkeypatch.setattr(type(auth), "qxid_map", boom)
    assert auth.qxid_for(ATTENDANCE) == "2553"


def test_menu_value_wins_over_default(monkeypatch, tmp_path):
    auth = make_auth(tmp_path)
    monkeypatch.setattr(type(auth), "qxid_map",
                        lambda self, refresh=False: {"renshi/rs_kqda_m/add.aspx": "9999"})
    assert auth.qxid_for(ATTENDANCE) == "9999"


def test_unknown_page_still_raises(monkeypatch, tmp_path):
    auth = make_auth(tmp_path)
    monkeypatch.setattr(type(auth), "qxid_map", lambda self, refresh=False: {})
    with pytest.raises(Exception) as err:
        auth.qxid_for("Foo/Bar.aspx")
    assert "权限" in str(err.value) or "没有" in str(err.value)


def test_probe_failure_without_default_reraises(monkeypatch, tmp_path):
    auth = make_auth(tmp_path)
    monkeypatch.setattr(type(auth), "qxid_map",
                        lambda self, refresh=False: (_ for _ in ()).throw(AuthError("no uid")))
    with pytest.raises(AuthError):
        auth.qxid_for("Foo/Bar.aspx")
