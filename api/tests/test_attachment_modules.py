"""附件模块注册表 / 入口自动发现 / 两种删除策略的离线测试。"""

from __future__ import annotations

import pytest

from erp_tools.tools import attachments as att
from erp_tools.tools.attachments import (
    AttachmentPageService,
    MODULES,
    get_module,
    module_names,
)


# ══════════════════ 注册表 ══════════════════
def test_registry_covers_five_modules() -> None:
    assert set(MODULES) == {"quote_request", "price", "production", "purchase",
                            "sales_order"}
    for module in MODULES.values():
        assert module.usable
        assert module.page_path.lower().endswith("_fj.aspx")
        # 每个模块都要有「怎么进详情/列表」的路径
        assert module.detail_path or module.list_path
    assert module_names() == list(MODULES)


def test_module_aliases() -> None:
    assert get_module("quote_request") is MODULES["quote_request"]
    assert get_module("客户报价需求") is MODULES["quote_request"]
    assert get_module("产品核价") is MODULES["price"]
    assert get_module("施工单") is MODULES["production"]
    assert get_module("采购订单") is MODULES["purchase"]
    assert get_module("客户订单") is MODULES["sales_order"]
    assert get_module("SALES_ORDER") is MODULES["sales_order"]
    with pytest.raises(Exception):
        get_module("不存在的模块")


def test_sales_order_uses_list_cell_entry() -> None:
    m = MODULES["sales_order"]
    assert m.entry == "cell" and m.list_path.endswith("Index.aspx")
    assert MODULES["production"].entry == "anchor"


# ══════════════════ 入口自动发现 ══════════════════
ANCHOR_PAGE = """
<html><body><a id="btnFJ" href="javascript:__doPostBack('btnFJ','')">[附件(无)]</a>
<input type="submit" name="Btn_FJ" /></body></html>
"""

CELL_PAGE = """
<html><body><table><tr>
 <td onclick="OnOpenFJ('FID-1','PID-1','2')" title="左击即可以调出附件">KHDD26110042</td>
</tr></table></body></html>
"""


def test_discover_entry_anchor() -> None:
    assert AttachmentPageService.discover_entry(ANCHOR_PAGE) == {"kind": "anchor"}


def test_discover_entry_cell() -> None:
    entry = AttachmentPageService.discover_entry(CELL_PAGE)
    assert entry["kind"] == "cell"
    assert entry["文件ID"] == "FID-1"
    assert entry["产品ID"] == "PID-1"
    assert entry["FJNum"] == "2"
    # 锚点优先：两种都在时按 anchor 处理（详情页就是这种）
    mixed = ANCHOR_PAGE + CELL_PAGE
    assert AttachmentPageService.discover_entry(mixed)["kind"] == "anchor"


def test_discover_entry_none() -> None:
    assert AttachmentPageService.discover_entry("<html>没有附件</html>") == {}


def test_popup_page_path_strips_host_and_query() -> None:
    svc = AttachmentPageService(None, page_path="x")
    text = ('window.open("http://192.168.1.199:8887/Admin/CaiGou/CG_CGDD/'
            'CG_CGDD_FJ.aspx?PID=abc&Modify=0&time=1");'
            'window.open("http://www.example.com/ad");')
    assert svc._popup_page_path(text) == "CaiGou/CG_CGDD/CG_CGDD_FJ.aspx"
    with pytest.raises(Exception):
        svc._popup_page_path('window.open("http://x/ad");')


# ══════════════════ 自动发现整条链路（桩） ══════════════════
class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status
        self.headers: dict[str, str] = {}


class _Client:
    """详情页 → 有附件锚点；回发 btnFJ → 弹窗附件页。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get(self, path: str, **_kw) -> _Resp:
        self.calls.append(("GET", path))
        return _Resp(ANCHOR_PAGE)

    def post(self, path: str, data=None, **_kw) -> _Resp:
        self.calls.append(("POST", path))
        if (data or {}).get("__EVENTTARGET") == "btnFJ":
            return _Resp('window.open("http://h:8887/Admin/BaoJia/BJ_KHBJ/'
                         'BJ_KHBJ_FJ.aspx?PID=rid&Modify=0&time=1");')
        return _Resp(ANCHOR_PAGE)


class _Auth:
    def __init__(self) -> None:
        self.client = _Client()
        self.user_id = "2069"
        self.cfg = type("C", (), {"host": "http://h:8889",
                                  "captcha_host": "http://h:8887"})()


def test_for_module_and_auto_discovery(monkeypatch) -> None:
    monkeypatch.setattr(att, "_page_cache", {})
    auth = _Auth()
    svc = AttachmentPageService.for_module(auth, "production")
    # 注册表里带着实测路径，直接可用
    assert svc.page_path == "ShengChan/SC_SCSGD/SC_SCSGD_FJ.aspx"
    assert svc.label.endswith("附件")

    # 清空 page_path 走自动发现
    svc.page_path = ""
    path = svc.discover_page("rid-1")
    assert path == "BaoJia/BJ_KHBJ/BJ_KHBJ_FJ.aspx"
    assert any(c[0] == "POST" for c in auth.client.calls)


def test_discover_cache_per_module(monkeypatch) -> None:
    monkeypatch.setattr(att, "_page_cache", {})
    auth = _Auth()
    svc = AttachmentPageService.for_module(auth, "purchase")
    svc.page_path = ""
    assert svc.resolve_page_path("rid-1") == "BaoJia/BJ_KHBJ/BJ_KHBJ_FJ.aspx"
    assert att._page_cache["purchase"] == "BaoJia/BJ_KHBJ/BJ_KHBJ_FJ.aspx"
    # 第二次走缓存，不再请求
    svc2 = AttachmentPageService.for_module(auth, "purchase")
    svc2.page_path = ""
    svc2.resolve_page_path("rid-1")
    assert att._page_cache["purchase"] == "BaoJia/BJ_KHBJ/BJ_KHBJ_FJ.aspx"


# ══════════════════ 删除策略：勾选 + Btn_DeleteFJ ══════════════════
PAGE_CHECKBOX_ONLY = """
<html><body><form name="form1" method="post" action="./X_FJ.aspx?PID=rid-1">
 <input type="hidden" name="__VIEWSTATE" value="vs" />
 <input type="submit" name="Btn_DeleteFJ" value="×" />
 <input type="submit" name="btnPostFile" value="" />
 <table><tr><td>
   <table id="gridView1">
     <tr><th>选择</th><th>排序号</th><th>附件名称</th><th>附件描述</th>
         <th>附件文件</th><th>备注</th></tr>
     <tr><td><input type="checkbox" name="gridView1$ctl02$0"/></td><td>1</td>
         <td onclick="OpenSelFieldValue('附件名称','FJMC','A.pdf','fa')">A.pdf</td>
         <td></td><td onclick="Display_FJ('fa','rid-1')">有</td><td></td></tr>
     <tr><td><input type="checkbox" name="gridView1$ctl03$1"/></td><td>2</td>
         <td onclick="OpenSelFieldValue('附件名称','FJMC','B.pdf','fb')">B.pdf</td>
         <td></td><td onclick="Display_FJ('fb','rid-1')">有</td><td></td></tr>
   </table>
 </td></tr></table>
</form></body></html>
"""


class _CbClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    def get(self, _path: str, **_kw) -> _Resp:
        return _Resp(PAGE_CHECKBOX_ONLY)

    def post(self, _path: str, data=None, files=None, **_kw) -> _Resp:
        self.posts.append(data or {})
        return _Resp(PAGE_CHECKBOX_ONLY)


class _CbAuth:
    def __init__(self) -> None:
        self.client = _CbClient()
        self.user_id = "2069"
        self.cfg = type("C", (), {"host": "http://h:8889",
                                  "captcha_host": "http://h:8887"})()


def test_delete_falls_back_to_checkbox_button() -> None:
    auth = _CbAuth()
    svc = AttachmentPageService(auth, "ShengChan/SC_SCSGD/SC_SCSGD_FJ.aspx",
                               label="施工单附件")
    rows = svc._parse_rows(PAGE_CHECKBOX_ONLY)
    assert rows[0]["行号"] == ""                     # 没有行内 Delete$
    assert rows[1]["复选框"] == "gridView1$ctl03$1"

    preview = svc.delete_file("rid-1", name="B.pdf")
    assert preview["status"] == "needs_confirmation"
    assert preview["strategy"] == "button"
    assert preview["would_delete"]["文件ID"] == "fb"

    svc.delete_file("rid-1", name="B.pdf", confirm=True)
    sent = auth.client.posts[0]
    assert sent["gridView1$ctl03$1"] == "on"
    assert "Btn_DeleteFJ" in sent
    # 走按钮时必须清空 __EVENTTARGET，否则 ERP 会按旧目标回发
    assert sent["__EVENTTARGET"] == ""


def test_delete_prefers_row_strategy_when_available() -> None:
    from tests.test_attachment_delete import FJ_PAGE_EDIT, _Auth

    svc = AttachmentPageService(_Auth(), "BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx")
    preview = svc.delete_file("rid-1", name="AI摘要.txt")
    assert preview["strategy"] == "row"
    assert "_parse_rows" in dir(svc)
    assert "Delete$" in FJ_PAGE_EDIT      # 行内删除的标记
