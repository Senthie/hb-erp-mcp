"""菜单 / QXID 解析测试（夹具为真实菜单接口响应）。"""

from erp_tools.core.auth import normalize_page_path, parse_menu_urls


def test_normalize_page_path():
    assert normalize_page_path("/Admin/BaoJia/BJ_KHBJXQ/Add.aspx?QXID=873") == \
        "baojia/bj_khbjxq/add.aspx"
    assert normalize_page_path("BaoJia/BJ_KHBJXQ/Add.aspx") == "baojia/bj_khbjxq/add.aspx"


def test_parse_menu_urls_from_fixture(fixture_text):
    mapping = parse_menu_urls(fixture_text("menu_tree.js"))
    # 实测值：新增页 873 / 查询页 876 / 考勤 2553
    assert mapping["baojia/bj_khbjxq/add.aspx"] == "873"
    assert mapping["baojia/bj_khbjxq/index.aspx"] == "876"
    assert mapping["renshi/rs_kqda_m/add.aspx"] == "2553"
    assert len(mapping) > 20


def test_parse_menu_urls_ignores_url_without_qxid():
    text = "url:'SysHelp.aspx', url:'X/Y.aspx?QXID=42&MenuTree=1'"
    mapping = parse_menu_urls(text)
    assert mapping == {"x/y.aspx": "42"}
