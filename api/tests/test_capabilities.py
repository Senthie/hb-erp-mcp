"""`erp_capabilities` 的页面权限来源与降级行为（离线）。

背景（2026-11-26 实测坑）：`pages` 只在**登录那一刻**填。网关重启后会话是从磁盘
cookie 恢复的（`SessionProvider._restore`），旧版本不回填 `pages` →
对**任何账号**（包括 15627 这种权限齐全的）都返回 `pages` 全 null、
四个 module `list_available=false`，看起来像「权限全没了」。

修好之后：
* 恢复会话时用磁盘 `qxid-<user>.json` 缓存回填（纯本地）；
* `erp_capabilities` 再兜一层「缓存 → 真探一次」；
* 真取不到时用 `pages_known=false` + `pages_error` 明确区分
  「该账号没权限」与「菜单没探到」，并用 `available: null` 而不是 `false`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from erp_tools.apps.mcp.tools import _ensure_pages, _module_capabilities
from erp_tools.core.auth import AuthManager
from erp_tools.core.config import ErpConfig
from erp_tools.core.errors import UpstreamError
from erp_tools.core.session import ErpClient
from erp_tools.runtime.sessions import UserSession

PAGES = {
    "BaoJia/BJ_KHBJXQ/Index.aspx": "876",
    "BaoJia/BJ_KHBJXQ/Add.aspx": "873",
    "ShengChan/SC_SCSGD/Index.aspx": "740",
}


class FakeAuth:
    """最小 AuthManager 替身：只实现能力查询用到的两个方法。"""

    def __init__(self, cached: dict[str, str] | None = None,
                 probe: dict[str, str] | None = None,
                 cached_boom: Exception | None = None,
                 probe_boom: Exception | None = None) -> None:
        self._cached = cached or {}
        self._probe = probe or {}
        self._cached_boom = cached_boom
        self._probe_boom = probe_boom
        self.probed = 0

    def cached_qxid_map(self) -> dict[str, str]:
        if self._cached_boom is not None:
            raise self._cached_boom
        return dict(self._cached)

    def qxid_map(self, *, refresh: bool = False) -> dict[str, str]:
        self.probed += 1
        if self._probe_boom is not None:
            raise self._probe_boom
        return dict(self._probe)


def make_session(pages: dict[str, str] | None = None, auth=None) -> UserSession:
    return UserSession(username="zhangshuang", auth=auth or FakeAuth(),
                       user_id="9999", pages=dict(pages or {}))


# ══════════════════ _ensure_pages ══════════════════
def test_pages_from_login_are_used_as_is() -> None:
    session = make_session(PAGES)
    session.pages_source = "login"
    pages, source, error = _ensure_pages(session)
    assert pages["baojia/bj_khbjxq/add.aspx"] == "873"
    assert source == "login"
    assert error == ""


def test_empty_pages_fall_back_to_disk_cache() -> None:
    """重启恢复的会话（pages 空）→ 从 qxid 缓存回填，且不额外探测。"""
    auth = FakeAuth(cached=PAGES)
    session = make_session(auth=auth)
    pages, source, error = _ensure_pages(session)
    assert pages["shengchan/sc_scsgd/index.aspx"] == "740"
    assert source == "cache"
    assert error == ""
    assert auth.probed == 0
    # 回填进会话，后续调用不再走磁盘
    assert session.pages_source == "cache"


def test_empty_pages_and_no_cache_probe_once() -> None:
    auth = FakeAuth(probe=PAGES)
    session = make_session(auth=auth)
    pages, source, error = _ensure_pages(session)
    assert pages["baojia/bj_khbjxq/index.aspx"] == "876"
    assert source == "probe"
    assert auth.probed == 1
    # 第二次调用直接命中会话
    _ensure_pages(session)
    assert auth.probed == 1


def test_no_permission_account_reports_error_not_silent_nulls() -> None:
    """该账号菜单本想空（权限不足）→ 必须带出原因，而不是静默全 null。"""
    auth = FakeAuth(probe_boom=UpstreamError("菜单接口没有返回任何菜单项，可能权限不足"))
    session = make_session(auth=auth)
    pages, source, error = _ensure_pages(session)
    assert pages == {}
    assert source == ""
    assert "权限不足" in error
    assert session.pages_error and "权限不足" in session.pages_error


def test_broken_cache_does_not_break_capabilities() -> None:
    auth = FakeAuth(cached_boom=OSError("权限拒绝"), probe=PAGES)
    pages, source, _error = _ensure_pages(make_session(auth=auth))
    assert pages and source == "probe"


# ══════════════════ _module_capabilities ══════════════════
def test_unknown_menu_does_not_claim_no_permission() -> None:
    """菜单没取到时 `available` 必须是 null（不知道），不能是 false（没权限）。"""
    modules = _module_capabilities({}, known=False)
    assert set(modules) == {"costing", "production", "purchase", "sales_order"}
    for item in modules.values():
        assert item["available"] is None
        assert item["list_available"] is False
        assert "无法判断" in item["note"]


def test_known_menu_reports_availability() -> None:
    pages = {"shengchan/sc_scsgd/index.aspx": "740"}
    modules = _module_capabilities(pages, known=True)
    assert modules["production"]["available"] is True
    assert modules["production"]["list_qxid"] == "740"
    assert "实测值" in modules["production"]["note"]
    assert modules["costing"]["available"] is False
    assert "可能无权限" in modules["costing"]["note"]


# ══════════════════ 磁盘缓存（真实 AuthManager，不打网络）══════════════════
def test_cached_qxid_map_reads_cache_without_network(tmp_path: Path) -> None:
    cfg = ErpConfig(host="http://127.0.0.1:1", username="zhangshuang", password="",
                    cookie_dir=tmp_path)
    cache = tmp_path / "qxid-zhangshuang.json"
    cache.write_text(json.dumps({"qxid": PAGES}), encoding="utf-8")

    auth = AuthManager(ErpClient(cfg), interactive=False)
    assert auth.cached_qxid_map() == PAGES
    # 二次调用命中内存，不再读盘
    cache.unlink()
    assert auth.cached_qxid_map() == PAGES


def test_cached_qxid_map_returns_empty_without_cache(tmp_path: Path) -> None:
    cfg = ErpConfig(host="http://127.0.0.1:1", username="zhangshuang", password="",
                    cookie_dir=tmp_path)
    auth = AuthManager(ErpClient(cfg), interactive=False)
    assert auth.cached_qxid_map() == {}


def test_restore_fills_pages_from_cache(tmp_path: Path, monkeypatch) -> None:
    """`_restore` 必须回填 pages —— 否则重启后大家都「没有权限」。"""
    from erp_tools.runtime.sessions import SessionProvider

    cookie_dir = tmp_path / "cookies"
    cookie_dir.mkdir()
    cfg = ErpConfig(host="http://127.0.0.1:1", username="zhangshuang", password="",
                    cookie_dir=cookie_dir)
    (cookie_dir / "qxid-zhangshuang.json").write_text(
        json.dumps({"qxid": PAGES}), encoding="utf-8")

    provider = SessionProvider(cfg, cookie_dir=cookie_dir)
    monkeypatch.setattr(AuthManager, "load_cookies", lambda self: True)
    monkeypatch.setattr(AuthManager, "is_logged_in", lambda self: True)

    session = provider._restore("zhangshuang")  # noqa: SLF001 - 直接验证恢复路径
    assert session is not None
    assert session.pages_source == "cache"
    assert session.pages["baojia/bj_khbjxq/add.aspx"] == "873"


@pytest.mark.parametrize("has_cache", [True, False])
def test_restore_without_cache_leaves_pages_empty(tmp_path: Path, monkeypatch,
                                                  has_cache: bool) -> None:
    from erp_tools.runtime.sessions import SessionProvider

    cookie_dir = tmp_path / "cookies"
    cookie_dir.mkdir()
    if has_cache:
        (cookie_dir / "qxid-zhangshuang.json").write_text(
            json.dumps({"qxid": PAGES}), encoding="utf-8")
    cfg = ErpConfig(host="http://127.0.0.1:1", username="zhangshuang", password="",
                    cookie_dir=cookie_dir)
    provider = SessionProvider(cfg, cookie_dir=cookie_dir)
    monkeypatch.setattr(AuthManager, "load_cookies", lambda self: True)
    monkeypatch.setattr(AuthManager, "is_logged_in", lambda self: True)

    session = provider._restore("zhangshuang")  # noqa: SLF001
    assert session is not None
    if has_cache:
        assert session.pages
    else:
        assert session.pages == {}
        assert session.pages_source == ""


# ══════════════════ 菜单为空的报错要说清原因 ══════════════════
class FakeResp:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


def _empty_menu_auth(tmp_path: Path, monkeypatch, body: str) -> AuthManager:
    cfg = ErpConfig(host="http://127.0.0.1:1", username="zhangshuang", password="",
                    cookie_dir=tmp_path)
    auth = AuthManager(ErpClient(cfg), interactive=False)
    monkeypatch.setattr(type(auth), "user_id", property(lambda self: "9999"))
    monkeypatch.setattr(type(auth.client), "get",
                        lambda self, url, headers=None: FakeResp(body))
    return auth


def test_empty_menu_explains_no_menu_at_all(tmp_path: Path, monkeypatch) -> None:
    """账真没配菜单 → 报错里要说「完全没有 url 节点」。"""
    from erp_tools.core.errors import UpstreamError

    auth = _empty_menu_auth(tmp_path, monkeypatch, "<html></html>")
    with pytest.raises(UpstreamError) as err:
        auth.qxid_map(refresh=True)
    assert "完全没有 url 节点" in str(err.value)
    assert "菜单权限" in str(err.value)


def test_empty_menu_explains_missing_qxid(tmp_path: Path, monkeypatch) -> None:
    """菜单有 url 但没 QXID（接口改版）→ 报错里要区分开。"""
    from erp_tools.core.errors import UpstreamError

    auth = _empty_menu_auth(tmp_path, monkeypatch,
                            "{url:'BaoJia/BJ_KHBJXQ/Add.aspx',text:'报价需求'}")
    with pytest.raises(UpstreamError) as err:
        auth.qxid_map(refresh=True)
    assert "含 url 节点但没有 QXID" in str(err.value)
