"""按表单编号定位记录（单号里含制单日期）。"""

import pytest

from erp_tools.core.errors import ParseError
from erp_tools.tools.quote_index import date_windows_for_bdbh, parse_list_page
from erp_tools.tools.quote_request import QuoteRequestTool


def test_windows_from_bdbh_day_then_month():
    assert date_windows_for_bdbh("BJXQ261105013") == [
        ("2026-11-05", "2026-11-05"),
        ("2026-11-01", "2026-11-30"),
    ]


def test_windows_handle_suffix_and_year_end():
    assert date_windows_for_bdbh("BJXQ210818002-02")[0] == ("2021-08-18", "2021-08-18")
    assert date_windows_for_bdbh("BJXQ231231001")[1] == ("2023-12-01", "2023-12-31")


def test_windows_reject_unparseable():
    assert date_windows_for_bdbh("ABC") == []
    assert date_windows_for_bdbh("BJXQ269999999") == []   # 无 99 月


def test_placeholder_row_is_skipped():
    """查不到数据时 ERP 返回一行全空占位（ondblclick 的 ID 为空），不能当成记录。"""
    html = """
    <div id="gViewDiv"><table id="gridView">
      <tr class="FixedTitleRow">
        <td>选择</td><td>信息</td><td>打印</td><td>表单编号</td><td>客户名称</td>
      </tr>
      <tr ondblclick="OnDBClickOpenData('','876',3)">
        <td></td><td></td><td></td><td></td><td></td>
      </tr>
      <tr ondblclick="OnDBClickOpenData('8c3a02cf-8df0-45b1-9a38-1c84b3548464','876',3)">
        <td></td><td></td><td></td><td>BJXQ261105013</td><td>IBL10</td>
      </tr>
    </table></div>
    """
    page = parse_list_page(html)
    assert len(page.rows) == 1
    assert page.rows[0]["表单编号"] == "BJXQ261105013"
    assert page.rows[0]["记录ID"] == "8c3a02cf-8df0-45b1-9a38-1c84b3548464"


def test_quote_loader_accepts_record_id_column(tmp_path):
    csv_path = tmp_path / "list.csv"
    csv_path.write_text("表单编号,客户名称,记录ID\n"
                        "BJXQ261105013,IBL10,8c3a02cf-8df0-45b1-9a38-1c84b3548464\n",
                        encoding="utf-8-sig")
    rows = QuoteRequestTool._load_rows(csv_path, None)
    assert rows == [{"id": "8c3a02cf-8df0-45b1-9a38-1c84b3548464", "creator": None,
                     "hk_no": "", "form_no": "BJXQ261105013"}]


def test_quote_loader_rejects_missing_csv():
    with pytest.raises(FileNotFoundError):
        QuoteRequestTool._load_rows("no_such_file.csv", None)
