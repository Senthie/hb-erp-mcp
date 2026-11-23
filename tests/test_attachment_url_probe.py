"""附件直链端口探测：ERP 给的端口可能不对（:8887 vs :8889），列表时要先验一次。"""

from __future__ import annotations

import pytest

from erp_tools.tools import attachments as att_mod
from erp_tools.tools.attachments import AttachmentPageService, probe_download_url

FJ_PATH = "/Admin/FuJian/BJ_KHBJXQ/Picture/a3b9125c-4f35-4adf-b02c-dc3cccc87eb6.zip"
GOOD = "http://192.168.1.199:8889" + FJ_PATH
BAD = "http://192.168.1.199:8887" + FJ_PATH


class _Cfg:
    host = "http://192.168.1.199:8889"
    captcha_host = "http://192.168.1.199:8887"


@pytest.fixture(autouse=True)
def _clean_cache():
    att_mod._working_host.clear()
    yield
    att_mod._working_host.clear()


def _install(monkeypatch, ok_hosts: set[str], calls: list[str]):
    import requests

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status_code = status

        def close(self) -> None:
            pass

    def head(url, **_kw):
        calls.append(url)
        return _Resp(200 if any(url.startswith(h) for h in ok_hosts) else 404)

    monkeypatch.setattr(requests, "head", head)


def test_probe_picks_working_port(monkeypatch) -> None:
    calls: list[str] = []
    _install(monkeypatch, {_Cfg.host}, calls)

    assert probe_download_url(BAD, _Cfg()) == GOOD
    assert calls == [BAD, GOOD]                 # 先试原样，再回退 ERP_HOST


def test_probe_reuses_cached_host(monkeypatch) -> None:
    calls: list[str] = []
    _install(monkeypatch, {_Cfg.host}, calls)
    probe_download_url(BAD, _Cfg())
    before = len(calls)

    other = "/Admin/FuJian/BJ_KHBJXQ/Picture/other.zip"
    assert probe_download_url("http://192.168.1.199:8887" + other, _Cfg()) == \
        "http://192.168.1.199:8889" + other
    assert len(calls) == before + 1             # 同模块只探一次，直接打已知可用 host


def test_probe_returns_original_when_all_fail(monkeypatch) -> None:
    calls: list[str] = []
    _install(monkeypatch, {}, calls)
    assert probe_download_url(BAD, _Cfg()) == BAD


def test_probe_falls_back_to_get_when_head_unsupported(monkeypatch) -> None:
    import requests

    seen: list[str] = []

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status_code = status

        def close(self) -> None:
            pass

    monkeypatch.setattr(requests, "head", lambda url, **_k: _Resp(405))
    monkeypatch.setattr(requests, "get",
                        lambda url, **_k: (seen.append(url), _Resp(200))[1])

    assert probe_download_url(BAD, _Cfg()) == BAD     # 405 后 GET 也 200 → 用原值
    assert seen == [BAD]


def test_probe_recovers_when_cached_host_dies(monkeypatch) -> None:
    calls: list[str] = []
    _install(monkeypatch, {_Cfg.host}, calls)
    probe_download_url(BAD, _Cfg())
    assert att_mod._working_host   # 已缓存 8889

    # 8889 挂了、只剩 8887 能用 → 缓存要被丢弃并重新探测
    calls.clear()
    _install(monkeypatch, {_Cfg.captcha_host}, calls)
    assert probe_download_url(BAD, _Cfg()) == BAD


def test_list_files_reports_both_urls(monkeypatch) -> None:
    """列表里 url 是验证过的，url_erp 保留 ERP 原样给的地址。"""
    calls: list[str] = []
    _install(monkeypatch, {_Cfg.host}, calls)

    page = f"""
    <html><body><form method="post" action="./X_FJ.aspx?PID=rid-1">
     <input type="hidden" name="__VIEWSTATE" value="vs" />
     <table><tr><td><table id="gridView1">
       <tr><th>选择</th><th>排序号</th><th>附件名称</th><th>附件描述</th>
           <th>附件文件</th><th>备注</th></tr>
       <tr><td><input type="checkbox"/></td><td>1</td>
           <td>原始件.zip</td><td></td>
           <td onclick="Display_FJ('f1','rid-1')">有</td><td></td></tr>
     </table></td></tr></table></form></body></html>
    """

    class _Auth:
        cfg = _Cfg()
        user_id = "2069"

    svc = AttachmentPageService(_Auth(), "BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx")
    svc._open = lambda *_a, **_k: (page, "", "")          # type: ignore[method-assign]
    svc.file_url = lambda *_a, **_k: BAD                  # type: ignore[method-assign]

    out = svc.list_files("rid-1", with_urls=True)
    row = out["files"][0]
    assert row["url"] == GOOD
    assert row["url_erp"] == BAD
    assert row["proxy_url"].endswith("zip&t=" + row["proxy_url"].rsplit("t=", 1)[1])
