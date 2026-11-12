"""客户报价子数据节点解析测试（夹具全部裁自真实 ERP 页面）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from erp_tools.tools.quote_price import node_parser as np

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------- 产品工序 ----------
def test_cp_process_grid() -> None:
    rows = np.parse_cp_process_node(load("price_node_cp_process.html"))
    assert len(rows) == 3
    first = rows[0]
    assert first["工序名称"] == "精装book书上皮壳(成品)"
    assert first["工序单价"] == "0.4970998305"
    assert first["工序正数"] == "2000"
    assert "选择" not in first        # 复选框列去掉


# ---------- 部件工序 ----------
def test_part_process_grid() -> None:
    rows = np.parse_part_process_node(load("price_node_part_process.html"))
    assert len(rows) == 9
    names = {r["工序名称"] for r in rows}
    assert {"过哑胶", "分纸", "机器包皮壳"} <= names
    assert rows[0]["起版费"] == "164"
    # 部件工序比产品工序少「指定起版费」「选配」两列
    assert "选配" not in rows[0]


# ---------- 部件机台 ----------
def test_part_machines_grid_and_fields() -> None:
    data = np.parse_part_machines_node(load("price_node_part_machines.html"))
    rows = data["records"]
    assert len(rows) == 1
    assert rows[0]["机台名称"] == "三菱机虚拟6C印刷机"
    assert rows[0]["工序单价"] == "998.32704"
    assert rows[0]["正面颜色"] == "黑+黑+蓝+红+黄"


# ---------- 其他费项 ----------
def test_other_fees_grid() -> None:
    rows = np.parse_other_fees_node(load("price_node_other_fees.html"))
    assert len(rows) == 11
    assert rows[0]["费用项目"] == "纸箱费用："
    assert rows[0]["金额"] == "350"
    assert rows[0]["计价公式"] == "【海运费纸箱金额】*【系数】"


# ---------- 产品辅料（本单为空） ----------
def test_cp_materials_reads_list_not_the_edit_form() -> None:
    """页面上还有一块同宽的编辑表单，不能把表单的标签行当成材料行。"""
    rows = np.parse_cp_materials_node(load("price_node_cp_materials.html"))
    assert rows == []


# ---------- 产品资料 ----------
def test_cp_form_fields() -> None:
    data = np.parse_cp_node(load("price_node_cp.html"))
    fields = data["fields"]
    assert fields["产品大类"] == "书版"
    assert fields["装订方式"] == "慢线BB书"
    assert fields["产品名称"].startswith("Claire/")
    # 内部 ID 类字段不当业务数据
    assert "PID" not in fields and "CPID" not in fields


# ---------- 需求反馈 ----------
def test_feedback_reads_textarea_content() -> None:
    data = np.parse_feedback_node(load("price_node_feedback.html"))
    assert "纸箱数量" in data["反馈内容"]
    assert data["反馈人"] == ""


# ---------- 拆分价格 ----------
def test_split_price_trees() -> None:
    data = np.parse_split_price_node(load("price_node_split_price.html"))
    assert data["待分配"], "待分配树应该解析出节点"
    assert data["待分配"][0]["层级"] == 0
    assert data["待分配"][0]["名称"].startswith("【产品名称】")
    assert all("名称" in node for node in data["待分配"])


# ---------- 折叠面板 ----------
def test_panel_selected_name_ignores_list_settings() -> None:
    """`[列表设置]` 也是粗体，但它不是面板名。"""
    data = np.parse_panel_node(load("price_node_panel.html"))
    assert data["panel"] == "【产品描述】"
    assert data["grids"], "产品描述面板应有网格"
    headers = [tuple(g["header"]) for g in data["grids"]]
    assert any(h[:2] == ("产品数量", "单价") for h in headers)
    assert any("部件名称" in h for h in headers)
    # 重名列要加后缀，不能因为 dict 覆盖丢列
    quantity = next(g for g in data["grids"] if g["header"][0] == "产品数量")
    assert len(quantity["rows"][0]) > 6


def test_panel_fields_capture_label_value_block() -> None:
    data = np.parse_panel_node(load("price_node_panel.html"))
    assert isinstance(data["fields"], dict)


# ---------- 节点名归一 ----------
@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("产品工序", ("sub", "cp_process")),
        ("部件工序", ("sub", "part_process")),
        ("其他费用", ("sub", "other_fees")),
        ("CP_PROCESS", ("sub", "cp_process")),
        ("产品描述", ("panel", "Lbtn_CPZL")),
        ("panel:freight", ("panel", "Lbtn_HYFList")),
        ("Lbtn_DJMSList", ("panel", "Lbtn_DJMSList")),
        ("list", ("list", "")),
        ("all", ("all", "")),
    ],
)
def test_resolve_node(alias: str, expected: tuple[str, str]) -> None:
    from erp_tools.tools.quote_price.tool import PriceNodeTool

    assert PriceNodeTool.resolve_node(alias) == expected


def test_resolve_node_rejects_unknown() -> None:
    from erp_tools.core.errors import ParseError
    from erp_tools.tools.quote_price.tool import PriceNodeTool

    with pytest.raises(ParseError):
        PriceNodeTool.resolve_node("不存在的节点")


def test_catalog_lists_every_node_and_panel() -> None:
    from erp_tools.tools.quote_price.tool import PriceNodeTool

    rows = PriceNodeTool.catalog()
    names = {r["node"] for r in rows}
    assert {"quantities", "part", "part_materials", "fees", "cp", "cp_process",
            "part_process", "other_fees", "split_price"} <= names
    assert sum(1 for r in rows if r["kind"] == "panel") == 8
