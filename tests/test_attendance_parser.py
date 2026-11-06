"""考勤表格解析测试（夹具为真实考勤查询结果）。"""

from erp_tools.tools.attendance import parse_attendance_table


def test_parses_headers_and_rows(fixture_text):
    table = parse_attendance_table(fixture_text("attendance_page.html"))

    assert table.headers[:5] == ["选择", "部门名称", "职位名称", "工卡号", "姓名"]
    assert "工卡号" in table.headers
    # 表头脏空格（换行 / \xa0）要抹平，并且重名列不能互相覆盖
    assert "职位名称" in table.headers
    assert "实际迟到分钟" in table.headers
    assert "实际迟到分钟(平时)" in table.headers
    assert len(table.headers) == len(set(table.headers))

    assert table.total == 5
    assert len(table.rows) == 5
    assert table.page == 1 and table.pages == 1
    assert not table.has_more_pages

    first = table.rows[0]
    assert first["姓名"]
    assert first["日"].isdigit()
    assert first["选择"] == ""  # 复选框列不当作内容


def test_no_grid_returns_empty():
    table = parse_attendance_table("<html><body>没有表格</body></html>")
    assert table.rows == []
    assert table.headers == []
