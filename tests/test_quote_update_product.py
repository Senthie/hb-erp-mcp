"""`quote_create`：明细文本字段（含 CPMS 产品描述）的修改与回读（全离线）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from erp_tools.core.errors import ParseError
from erp_tools.tools.quote_create import QuoteCreateService
from erp_tools.tools.quote_create.service import _same_text, _same_value

RECORD_ID = "aaaaaaaa-0000-0000-0000-000000000001"
CPID = "cpid-1"

_RECORD_PAGE = (
    '<form id="form1">'
    f'<input type="hidden" name="lblID" value="{RECORD_ID}" />'
    '<input type="hidden" name="txtBDBH" value="BJXQ261120009" />'
    '<input type="hidden" name="txtKHID" value="khid-1" />'
    '<input type="hidden" name="txtZKL" value="10" />'
    f'<span onclick="OpenInputCPZLData(\'{CPID}\',\'pid-1\',\'0\')">产品甲</span>'
    '</form>'
)

_EDITOR_PAGE = (
    '<form id="form1">'
    '<input type="hidden" name="__VIEWSTATE" value="vs" />'
    f'<input type="hidden" name="txtCPID" value="{CPID}" />'
    '<input type="text" name="txtCPMC" value="产品甲" />'
    '<input type="text" name="txtGG" value="210*148" />'
    '<input type="text" name="txtCPSL" value="2000" />'
    '<select name="txtCPLB"><option value="27.01;普通BB书" selected="selected">普通BB书</option></select>'
    '<input type="text" name="txtCPLB_Input" value="27.01;普通BB书" />'
    '<textarea name="txtCPMS">皮壳：157g哑粉裱1200g灰板</textarea>'
    '<textarea name="txtRemark1">无</textarea>'
    '<textarea name="txtRemark3">新款报价</textarea>'
    '<textarea name="txtRemark4">EN71 part1</textarea>'
    '</form>'
)


class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.status_code = status
        self.text = text
        self.headers: dict[str, str] = {}


class _Client:
    """GET 返回编辑页；POST 记录下来，之后 GET 返回「已落库」的页面。"""

    def __init__(self, editor_html: str = _EDITOR_PAGE, post_html: str | None = None,
                 updated_html: str | None = None) -> None:
        self.editor_html = editor_html
        self.post_html = post_html if post_html is not None else editor_html
        self.updated_html = updated_html
        self.posts: list[tuple[str, dict | None]] = []
        self.gets: list[str] = []

    def get(self, url: str) -> _Resp:
        self.gets.append(url)
        return _Resp(self.updated_html or self.editor_html)

    def post(self, url: str, data=None) -> _Resp:
        self.posts.append((url, data))
        return _Resp(self.post_html)


def _service(client: _Client) -> QuoteCreateService:
    auth = SimpleNamespace(user_id="2069", client=client)
    svc = QuoteCreateService(auth)  # type: ignore[arg-type]
    svc.open_record = lambda record_id: (_RECORD_PAGE, "/record")  # type: ignore[assignment]
    svc._sub_page_url = lambda *a, **kw: "/stub/sub.aspx"  # type: ignore[assignment]
    svc._restore_product_category = lambda *a, **kw: None  # type: ignore[assignment]
    svc.list_details = lambda record_id: {  # type: ignore[assignment]
        "count": 1,
        "details": [{"cpid": CPID, "pid": "pid-1", "name": "产品甲"}],
    }
    return svc


# ══════════════════ 产品描述 CPMS ══════════════════
def test_product_desc_preview_does_not_post() -> None:
    """confirm=False 只回待改字段，绝不发 POST。"""
    client = _Client()
    result = _service(client).update_product(RECORD_ID, CPID, product_desc="新描述")

    assert result["status"] == "needs_confirmation"
    assert result["changes"] == {"txtCPMS": "新描述"}
    assert result["fields"]["txtCPMS"] == "产品描述"
    assert client.posts == []


def test_product_desc_writes_and_reads_back() -> None:
    saved = _EDITOR_PAGE.replace(
        "<textarea name=\"txtCPMS\">皮壳：157g哑粉裱1200g灰板</textarea>",
        "<textarea name=\"txtCPMS\">新描述</textarea>",
    )
    client = _Client(updated_html=saved)
    result = _service(client).update_product(RECORD_ID, CPID, product_desc="新描述", confirm=True)

    assert result["status"] == "updated", result
    assert result["ok"] is True
    assert result["verified"]["txtCPMS"] == "新描述"
    assert result["mismatched"] == []

    url, body = client.posts[0]
    assert url == "/stub/sub.aspx"
    assert body["txtCPMS"] == "新描述"
    # 快照里原有的其他文本域必须原样带回去，否则会被清空
    assert body["txtRemark1"] == "无"
    assert body["txtRemark4"] == "EN71 part1"
    assert body["btnSave"] == "· 保存 ·"


def test_product_desc_multiline_compared_loosely() -> None:
    """ERP 会把 \\r\\n 改成 \\n，比较时不能因此判失败。"""
    editor = _EDITOR_PAGE.replace(
        "<textarea name=\"txtCPMS\">皮壳：157g哑粉裱1200g灰板</textarea>",
        "<textarea name=\"txtCPMS\">第一行\n第二行</textarea>",
    )
    result = _service(_Client(editor)).update_product(
        RECORD_ID, CPID, product_desc="第一行\r\n第二行", confirm=True)
    assert result["status"] == "updated"
    assert result["mismatched"] == []


def test_product_desc_reports_mismatch() -> None:
    """保存后回读不一致必须 failed，并在 mismatched 里点名。"""
    result = _service(_Client(_EDITOR_PAGE)).update_product(
        RECORD_ID, CPID, product_desc="新描述", confirm=True)
    assert result["status"] == "failed"
    assert result["ok"] is False
    assert result["mismatched"] == ["txtCPMS"]
    assert result["verified"]["txtCPMS"] == "皮壳：157g哑粉裱1200g灰板"


def test_save_rejection_without_readback_is_failed() -> None:
    """回读没变成预期值 + 页面有校验提示 → failed，并把提示带出来。

    （不能只看提示就判失败：ERP 保存成功也会残留旧提示，见下一个用例。）
    """
    rejected = ('<script>$.ligerDialog.error(\'【产品描述】不能为空！\');</script>'
                + _EDITOR_PAGE)
    result = _service(_Client(_EDITOR_PAGE, post_html=rejected)).update_product(
        RECORD_ID, CPID, product_desc="", confirm=True)
    assert result["status"] == "failed"
    assert result["ok"] is False
    assert "不能为空" in result["message"]


def test_stale_alert_does_not_fail_a_successful_save() -> None:
    """保存成功但页面残留旧提示时，以**回读**为准，只附 warning。

    真实案例：只读页保存后仍带着上一次的 `$.ligerDialog.error(...)`，
    若把它当失败就会假报「改不了」。
    """
    saved = _EDITOR_PAGE.replace(
        "<textarea name=\"txtCPMS\">皮壳：157g哑粉裱1200g灰板</textarea>",
        "<textarea name=\"txtCPMS\">新描述</textarea>",
    )
    stale = '<script>$.ligerDialog.error(\'【检测项目】不能为空！\');</script>' + saved
    result = _service(_Client(updated_html=saved, post_html=stale)).update_product(
        RECORD_ID, CPID, product_desc="新描述", confirm=True)
    assert result["status"] == "updated", result
    assert result["ok"] is True
    assert "以回读为准" in result["warning"]


# ══════════════════ fields 别名 ══════════════════
def test_fields_accept_aliases() -> None:
    client = _Client()
    result = _service(client).update_product(
        RECORD_ID, CPID,
        fields={"parts_desc": "有", "modify_log": "改款", "inspection": "EN71"},
    )
    assert result["status"] == "needs_confirmation"
    assert result["changes"] == {
        "txtRemark1": "有", "txtRemark3": "改款", "txtRemark4": "EN71",
    }


def test_fields_accept_control_names() -> None:
    result = _service(_Client()).update_product(
        RECORD_ID, CPID, fields={"txtCPMS": "直接写控件名"})
    assert result["changes"] == {"txtCPMS": "直接写控件名"}


def test_fields_rejects_unknown_key_with_hint() -> None:
    with pytest.raises(ParseError) as exc:
        _service(_Client()).update_product(RECORD_ID, CPID, fields={"desc": "x"})
    message = str(exc.value)
    assert "未知的产品字段" in message
    assert "product_desc" in message        # 给出候选


def test_fields_rejects_readonly_control() -> None:
    with pytest.raises(ParseError) as exc:
        _service(_Client()).update_product(RECORD_ID, CPID, fields={"txtOldCPMS": "x"})
    assert "只读" in str(exc.value)


def test_fields_rejects_qty() -> None:
    """数量必须走 qty（要同时写 txtCPSL 与 txtCPSL1）。"""
    with pytest.raises(ParseError) as exc:
        _service(_Client()).update_product(RECORD_ID, CPID, fields={"txtCPSL1": "3000"})
    assert "qty" in str(exc.value)


def test_combined_change_keeps_name_and_desc() -> None:
    result = _service(_Client()).update_product(
        RECORD_ID, CPID, name="产品甲", product_desc="新描述")
    assert set(result["changes"]) == {"txtCPMC", "txtCPMS"}


def test_same_text_normalises_whitespace() -> None:
    assert _same_text("a\r\n b", "a\n b")
    assert _same_text(" a  b ", "a b")
    assert not _same_text("a", "b")


def test_date_field_compares_only_date_part() -> None:
    """ERP 会把 `2026-12-30` 存成 `2026/12/30 0:00:00`，不能因此判失败。"""
    assert _same_value("txtJHRQ", "2026-12-30", "2026/12/30 0:00:00")
    assert _same_value("txtJHRQ", "2026/12/30", "2026-12-30")
    assert not _same_value("txtJHRQ", "2026-12-30", "2026-12-31")
    # 非日期字段仍按文本严格比较
    assert not _same_value("txtCPMS", "2026-12-30", "2026/12/30 0:00:00")
