"""`quote_create`：产品数量（`txtCPSL` / `txtCPSL1`）的提交与回读（全离线）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from erp_tools.core.errors import ParseError
from erp_tools.tools.quote_create import QuoteCreateService
from erp_tools.tools.quote_create.service import float_or_none


class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.status_code = status
        self.text = text
        self.headers: dict[str, str] = {}


class _FakeClient:
    def __init__(self, html: str) -> None:
        self.html = html
        self.posts: list[tuple[str, dict | None]] = []

    def get(self, url: str) -> _Resp:  # noqa: D102
        return _Resp(self.html)

    def post(self, url: str, data=None) -> _Resp:  # noqa: D102
        self.posts.append((url, data))
        return _Resp(self.html)


_RECORD_PAGE = (
    '<form id="form1">'
    '<input type="hidden" name="lblID" value="aaaaaaaa-0000-0000-0000-000000000001" />'
    '<input type="hidden" name="txtBDBH" value="BJXQ261111011" />'
    '<input type="hidden" name="txtKHID" value="khid-1" />'
    '<input type="hidden" name="txtZKL" value="10" />'
    '</form>'
)

# ══════════════════ 产品数量（txtCPSL / txtCPSL1）══════════════════
def test_float_or_none_tolerates_unit_suffix() -> None:
    """ERP 回读值形如 `3000` 或 `3000个`，比较前必须能变成数字。"""
    assert float_or_none("3000") == 3000.0
    assert float_or_none("3000个") == 3000.0
    assert float_or_none(" 1,200 ") == 1200.0
    assert float_or_none("1.5") == 1.5
    assert float_or_none("") is None
    assert float_or_none(None) is None
    assert float_or_none("无") is None


class _EditorClient:
    """只处理 GET：返回产品行编辑页。"""

    def __init__(self, editor_html: str) -> None:
        self.editor_html = editor_html
        self.gets: list[str] = []

    def get(self, url: str) -> _Resp:  # noqa: D102
        self.gets.append(url)
        return _Resp(self.editor_html)


def _qty_service(editor_html: str) -> tuple[QuoteCreateService, _EditorClient]:
    client = _EditorClient(editor_html)
    auth = SimpleNamespace(user_id="2474", client=client)
    svc = QuoteCreateService(auth)  # type: ignore[arg-type]
    svc.open_record = lambda record_id: (_RECORD_PAGE, "/record")  # type: ignore[assignment]
    svc._sub_page_url = lambda *a, **kw: "/stub/sub.aspx"  # type: ignore[assignment]
    return svc, client


def test_qty_readback_prefers_txtCPSL() -> None:
    """汇总值 `txtCPSL` 才是校验/落库用的那个字段。"""
    editor = (
        '<form><input type="text" name="txtCPSL" value="3000" />'
        '<input type="text" name="txtCPSL1" value="1000" /></form>'
    )
    svc, client = _qty_service(editor)

    assert svc._product_qty_readback("rec", "cpid-1") == "3000"
    assert client.gets == ["/stub/sub.aspx"]


def test_qty_readback_falls_back_to_txtCPSL1() -> None:
    editor = (
        '<form><input type="text" name="txtCPSL" value="" />'
        '<input type="text" name="txtCPSL1" value="3000" /></form>'
    )
    svc, _ = _qty_service(editor)

    assert svc._product_qty_readback("rec", "cpid-1") == "3000"


def test_qty_readback_returns_none_when_empty() -> None:
    editor = (
        '<form><input type="text" name="txtCPSL" value="" />'
        '<input type="text" name="txtCPSL1" value="" /></form>'
    )
    svc, _ = _qty_service(editor)

    assert svc._product_qty_readback("rec", "cpid-1") is None


def test_qty_readback_returns_none_when_record_missing() -> None:
    svc, client = _qty_service("<form></form>")

    def _boom(record_id: str):
        raise ParseError("记录不存在")

    svc.open_record = _boom  # type: ignore[assignment]

    assert svc._product_qty_readback("rec", "cpid-1") is None
    assert client.gets == [], "单据打不开时不应再请求行编辑页"


_RECORD_WITH_ROW = _RECORD_PAGE.replace(
    "</form>",
    '<span onclick="OpenInputCPZLData(\'cpid-1\',\'pid-1\',\'0\')">产品甲</span></form>',
)


def test_update_product_qty_sends_both_cpsl_fields() -> None:
    """只发 `txtCPSL1` 会被 ERP 挡在「【产品数量】不能为空！」，必须同时发 `txtCPSL`。"""
    client = _FakeClient(_RECORD_WITH_ROW)
    svc = QuoteCreateService(SimpleNamespace(user_id="2474", client=client))  # type: ignore[arg-type]

    result = svc.update_product("aaaaaaaa-0000-0000-0000-000000000001", "cpid-1", qty=500)

    assert result["status"] == "needs_confirmation"
    assert result["changes"] == {"txtCPSL1": "500", "txtCPSL": "500"}
    assert client.posts == [], "confirm=False 不能发任何 POST"
