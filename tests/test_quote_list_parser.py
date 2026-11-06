"""客户报价需求列表页解析测试（夹具为真实查询结果裁剪）。"""

from erp_tools.tools.quote_index import parse_list_page


def test_headers_and_columns(fixture_text):
    page = parse_list_page(fixture_text("quote_list_page.html"))

    assert page.headers[:4] == ["选择", "信息", "打印", "表单编号"]
    assert "客户名称" in page.headers
    assert "产品名称" in page.headers
    assert "报价状态" in page.headers
    assert len(page.headers) == 38
    assert len(page.headers) == len(set(page.headers))

    # 分页信息
    assert (page.page, page.pages, page.total) == (1, 2, 67)
    assert page.has_more_pages


def test_rows_keep_column_alignment(fixture_text):
    page = parse_list_page(fixture_text("quote_list_page.html"))
    assert len(page.rows) == 3

    first = page.rows[0]
    assert first["表单编号"].startswith("BJXQ")
    assert first["客户名称"]                      # 客户代码，如 IBL10
    assert first["业务员"]
    assert first["制单日期"].startswith("20")
    assert first["选择"] == ""                    # 复选框列不当作内容


def test_record_id_from_row_ondblclick(fixture_text):
    """主记录 ID 只认 OnDBClickOpenData / showMessagePop，不能按「第一个 GUID」取。"""
    page = parse_list_page(fixture_text("quote_list_page.html"))
    ids = [row.get("记录ID") for row in page.rows]

    assert all(ids), f"有行没取到记录 ID：{ids}"
    assert len(set(ids)) == len(ids)
    assert all(len(i) == 36 and i.count("-") == 4 for i in ids)


def test_empty_page():
    page = parse_list_page("<html><body>nothing</body></html>")
    assert page.rows == []
    assert page.headers == []
