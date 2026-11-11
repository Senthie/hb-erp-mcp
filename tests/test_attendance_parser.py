"""考勤表格解析测试（夹具为真实考勤查询结果）。"""

import pytest

from erp_tools.core.errors import ConfigError
from erp_tools.tools.attendance import (
    MAX_DAY,
    build_search_params,
    clamp_day,
    page_month,
    parse_attendance_table,
)


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


# ══════════════════ 日期区间（ERP 下拉只有 01-30） ══════════════════
def test_end_day_31_is_clamped_to_30():
    """传 31 时服务端只会回 1 号一行，所以必须收敛到 30。"""
    params = build_search_params(qxid="2553", month="2026-11", end_day="31")
    assert params["txtJZDay"] == f"{MAX_DAY:02d}"
    assert params["txtKSDay"] == "01"


def test_days_are_clamped_into_range():
    assert clamp_day("31") == MAX_DAY
    assert clamp_day("0") == 1
    assert clamp_day("") == 1
    assert clamp_day(None, default=MAX_DAY) == MAX_DAY
    assert clamp_day("05") == 5


def test_invalid_day_raises():
    with pytest.raises(ConfigError):
        build_search_params(qxid="2553", month="2026-11", start_day="abc")


def test_end_before_start_raises():
    with pytest.raises(ConfigError):
        build_search_params(qxid="2553", month="2026-11", start_day="10", end_day="05")


# ══════════════════ 月份切换（历史月份必须切月份再查） ══════════════════
def test_page_month_reads_selected_option(fixture_text):
    html = fixture_text("attendance_page.html")
    assert page_month(html) == "2026-11"
    assert page_month("<html><body>no select</body></html>") == ""
