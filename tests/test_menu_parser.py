"""菜单树解析测试（夹具是真实的 GetMenuTree 响应，账号 2474）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from erp_tools.core.menu import (
    MenuParseError,
    duplicate_paths,
    menu_pages,
    parse_menu_tree,
    qxid_map,
    walk,
)

FIXTURE = Path(__file__).parent / "fixtures" / "menu_tree.js"


def load_tree():
    return parse_menu_tree(FIXTURE.read_text(encoding="utf-8"))


# ---------- 结构 ----------
def test_top_level_modules() -> None:
    tree = load_tree()
    assert [m.label for m in tree] == [
        "业务报价管理", "客户订单管理", "生产管理", "采购管理", "仓库管理",
        "设备管理", "人力资源管理", "财务管理", "个人信息", "系统管理",
    ]


def test_nested_path_matches_erp_left_menu() -> None:
    tree = load_tree()
    paths = {path for path, _item in walk(tree)}
    assert ("业务报价管理", "业务需求", "客户报价需求", "新增-客户报价需求") in paths
    assert ("业务报价管理", "业务需求", "客户报价需求", "列表-客户报价需求") in paths
    assert ("人力资源管理", "考勤管理", "考勤档案") in paths


def test_page_count_and_qxid() -> None:
    tree = load_tree()
    pages = menu_pages(tree)
    # 34 个菜单叶子，其中 2 个路径与别的菜单重名 → 唯一路径 32 个
    assert len(pages) == 34
    assert len(qxid_map(tree)) == 32
    mapping = qxid_map(tree)
    assert mapping["baojia/bj_khbjxq/add.aspx"] == "873"
    assert mapping["baojia/bj_khbj/index.aspx"] == "860"
    assert mapping["renshi/rs_kqda_m/add.aspx"] == "2553"


# ---------- 三个已知的“坑” ----------
def test_empty_url_group_is_kept_but_not_a_page() -> None:
    tree = load_tree()
    items = {item.label: item for _path, item in walk(tree)}
    assert "系统管理" in items
    assert items["系统管理"].is_page is False
    assert items["系统管理"].url == ""


def test_same_path_under_two_menus() -> None:
    """KC_CLCRKSQ 既是「采购申请」又是「部门领料申请」→ 不能按路径去重。"""
    tree = load_tree()
    dups = duplicate_paths(tree)
    assert "kucun/kc_clcrksq/add.aspx" in dups
    assert sorted(dups["kucun/kc_clcrksq/add.aspx"]) == ["1036", "331"]
    # 首个生效（与 auth.parse_menu_urls 同口径）
    assert qxid_map(tree)["kucun/kc_clcrksq/add.aspx"] == "1036"


def test_label_strips_erp_wrapping_spaces() -> None:
    tree = load_tree()
    labels = {item.label for _path, item in walk(tree)}
    assert "业务需求" in labels          # 原文是 '业务需 求'
    assert "业务需 求" not in labels
    assert "报价纸张价格设置" in labels   # 原文是 '报价 纸张价格设置'


def test_english_space_is_kept() -> None:
    from erp_tools.core.menu import MenuItem

    assert MenuItem(text="运费清单 FOB 盐田").label == "运费清单 FOB 盐田"
    assert MenuItem(text="新增-设备 维修通知单").label == "新增-设备维修通知单"


# ---------- 容错 ----------
def test_trailing_comma_and_escapes() -> None:
    tree = parse_menu_tree("[{text:'A\\'B',url:'',children:[{text:'C',},],},]")
    assert tree[0].text == "A'B"
    assert tree[0].children[0].text == "C"


def test_empty_and_broken_payloads() -> None:
    with pytest.raises(MenuParseError):
        parse_menu_tree("")
    with pytest.raises(MenuParseError):
        parse_menu_tree("{}")
    with pytest.raises(MenuParseError):
        parse_menu_tree("[{text:'A'")


def test_roundtrip_dict() -> None:
    from erp_tools.core.menu import MenuItem, tree_to_items

    tree = load_tree()
    again = tree_to_items([m.to_dict() for m in tree])
    assert [(p, i.label) for p, i in walk(again)] == [(p, i.label) for p, i in walk(tree)]
    assert isinstance(again[0], MenuItem)
