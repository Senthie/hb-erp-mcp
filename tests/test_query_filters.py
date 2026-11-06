"""查询条件（--where）与表单快照测试。"""

import pytest

from erp_tools.core.errors import ParseError
from erp_tools.tools.quote_index import apply_conditions, form_snapshot, parse_where

ROWS = [
    {"表单编号": "BJXQ261105013", "客户名称": "IBL10", "业务员": "许月闻",
     "报价状态": "需求审批", "制单日期": "2026-11-05 17:40:28", "数量描述": "3000"},
    {"表单编号": "BJXQ261105012", "客户名称": "ADG24", "业务员": "李东员",
     "报价状态": "报价审批", "制单日期": "2026-11-02 09:00:00", "数量描述": "10000"},
]
HEADERS = list(ROWS[0])


def test_parse_where_operators():
    conds = parse_where("客户名称~ADG, 报价状态=报价审批,制单日期>=2026-11-01,业务员!=李东员")
    assert [(c.field, c.op, c.value) for c in conds] == [
        ("客户名称", "~", "ADG"),
        ("报价状态", "=", "报价审批"),
        ("制单日期", ">=", "2026-11-01"),
        ("业务员", "!=", "李东员"),
    ]


def test_parse_where_rejects_garbage():
    with pytest.raises(ParseError):
        parse_where("客户名称没有操作符")


def test_contains_is_case_insensitive():
    rows = apply_conditions(ROWS, parse_where("客户名称~adg"), HEADERS)
    assert [r["表单编号"] for r in rows] == ["BJXQ261105012"]


def test_numeric_and_date_comparison():
    rows = apply_conditions(ROWS, parse_where("数量描述>=5000"), HEADERS)
    assert len(rows) == 1 and rows[0]["数量描述"] == "10000"

    rows = apply_conditions(ROWS, parse_where("制单日期>=2026-11-03"), HEADERS)
    assert len(rows) == 1 and rows[0]["表单编号"] == "BJXQ261105013"


def test_unknown_column_reports_available():
    with pytest.raises(ParseError) as err:
        apply_conditions(ROWS, parse_where("不存在的列=1"), HEADERS)
    assert "可用列" in str(err.value)


def test_form_snapshot_picks_controls():
    html = """
    <form name="form1">
      <input type="hidden" name="__VIEWSTATE" value="VS" />
      <input type="text" name="txtKSRQ" value="2026-10-30" />
      <input type="text" name="txtEvenPageDataSize" value="50" />
      <input type="image" name="btn_Search" />
      <input type="checkbox" name="gridView$ctl02$0" />
      <input type="checkbox" name="chkX" checked="checked" value="on" />
      <select name="DropField"><option value="BDBH">表单编号</option>
        <option value="KHMC" selected="selected">客户名称</option></select>
      <select id="txtCPLB"><option value="x">无 name 不提交</option></select>
    </form>
    """
    data = form_snapshot(html)
    assert data["__VIEWSTATE"] == "VS"
    assert data["txtKSRQ"] == "2026-10-30"
    assert data["DropField"] == "KHMC"
    assert data["chkX"] == "on"
    assert "btn_Search" not in data
    assert not any(k.startswith("gridView$") for k in data)
    assert "txtCPLB" not in data
