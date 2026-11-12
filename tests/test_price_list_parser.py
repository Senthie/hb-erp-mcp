"""客户报价列表（BJ_KHBJ/Index.aspx）解析与服务端下拉参数测试。"""

import pytest

from erp_tools.core.errors import ParseError
from erp_tools.tools.gridlist import resolve_select_value, select_choices
from erp_tools.tools.quote_price import PriceListTool, parse_price_list_page
from erp_tools.tools.registry import tool_names

SELECT_HTML = """
<html><body><form id="form1">
<select name="txtZDorJH">
  <option selected="selected" value="ZDRQ">制单日期</option>
  <option value="KHJHRQ">交货日期</option>
  <option value="LCRQ">离厂日期</option>
</select>
<select name="txtCheck">
  <option selected="selected" value="全部">全部</option>
  <option value="未全审批">未全审批</option>
  <option value="已全审批">已全审批</option>
</select>
<select name="txt_SFBF">
  <option selected="selected" value="0">全部</option>
  <option value="1">已报废</option>
  <option value="2">未报废</option>
</select>
<select name="DropField">
  <option value="BJZT">报价状态</option>
  <option value="KHMC">客户名称</option>
  <option value="BDBH">表单编号</option>
</select>
</form></body></html>
"""


# ══════════════════ 列表解析 ══════════════════
def test_headers_match_the_real_56_columns(fixture_text):
    page = parse_price_list_page(fixture_text("price_list_page.html"))

    assert page.headers[:3] == ["选择", "信息", "打印"]
    assert "表单编号" in page.headers
    assert "客户名称" in page.headers
    assert "产品名称" in page.headers
    assert len(page.headers) == 56
    assert len(page.headers) == len(set(page.headers))


def test_meta_comes_from_lbl_fields(fixture_text):
    page = parse_price_list_page(fixture_text("price_list_page.html"))
    assert (page.page, page.pages, page.total) == (1, 1, 89)
    assert not page.has_more_pages


def test_rows_are_product_lines_with_record_id(fixture_text):
    """一行 = 一个报价产品行，记录 ID 来自 OnDBClickOpenData（不是行内其他 GUID）。"""
    page = parse_price_list_page(fixture_text("price_list_page.html"))

    assert len(page.rows) == 2
    first = page.rows[0]
    assert first["报价状态"] == "编辑中"
    assert first["客户名称"]  # 简称，如 IBL10
    assert first["产品名称"]
    assert first["表单编号"].startswith("KHBJ")
    assert first["记录ID"] == "89ad5e46-4872-4b26-83f5-18cab4fe2fe4"
    assert all(len(r["记录ID"]) == 36 for r in page.rows)


def test_total_and_placeholder_rows_are_dropped(fixture_text):
    """合计行与「查不到」的全空占位行都不能算数据行。"""
    page = parse_price_list_page(fixture_text("price_list_page.html"))

    assert all(row["记录ID"] for row in page.rows)
    assert not any("合计" in (row.get("客户名称") or "") for row in page.rows)
    assert not any("合计" in (row.get("单位") or "") for row in page.rows)


def test_empty_page():
    page = parse_price_list_page("<html><body>nothing</body></html>")
    assert page.rows == [] and page.headers == []


# ══════════════════ 服务端下拉参数 ══════════════════
def test_select_choices_and_value_resolution():
    assert select_choices(SELECT_HTML, "txt_SFBF") == [("0", "全部"), ("1", "已报废"),
                                                      ("2", "未报废")]
    # 中文别名（显示文字）和代码都要能提交
    assert resolve_select_value(SELECT_HTML, "txtZDorJH", "交货日期") == "KHJHRQ"
    assert resolve_select_value(SELECT_HTML, "txtZDorJH", "LCRQ") == "LCRQ"
    assert resolve_select_value(SELECT_HTML, "txtCheck", "已全审批") == "已全审批"
    assert resolve_select_value(SELECT_HTML, "txt_SFBF", "未报废") == "2"
    assert resolve_select_value(SELECT_HTML, "txt_SFBF", "") == ""


def test_unknown_option_lists_real_choices():
    with pytest.raises(ParseError) as exc:
        resolve_select_value(SELECT_HTML, "txtCheck", "已作废")
    message = str(exc.value)
    assert "已全审批" in message and "未全审批" in message


@pytest.mark.parametrize("key", ["审批状态", "审批", "txtCheck"])
def test_filter_aliases_resolve_to_controls(key):
    assert PriceListTool.parse_filters({key: "已全审批"}) == {"txtCheck": "已全审批"}


def test_filters_accept_list_form():
    assert PriceListTool.parse_filters(["审批状态=已全审批", "报废状态=未报废"]) == {
        "txtCheck": "已全审批",
        "txt_SFBF": "未报废",
    }


def test_unknown_filter_key_is_rejected():
    with pytest.raises(ParseError):
        PriceListTool.parse_filters({"不存在的条件": "x"})
    with pytest.raises(ParseError):
        PriceListTool.parse_filters(["审批状态"])  # 缺 '='


# ══════════════════ 注册 ══════════════════
def test_tool_is_registered():
    assert "price" in tool_names()
    assert PriceListTool.name == "price"
