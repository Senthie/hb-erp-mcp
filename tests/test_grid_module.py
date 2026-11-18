"""三个模块（产品核价 / 生产施工单 / 采购订单）的解析层测试。

夹具是真实页面的**结构裁剪版**（`tests/fixtures/production_detail_page.html`），
按页面真实排版写：标签格用全角冒号、部件表 17 列、送货表 21 列、末尾合计行、
以及详情页里真实出现过的 `Open*` 调用（含 GUID 实参）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from erp_tools.tools.grid_module import MODULES, get_module
from erp_tools.tools.grid_module.parser import (
    detail_context,
    discover_nodes,
    fill_missing,
    node_payload,
    parse_detail,
    parse_grids,
    popup_url,
    split_args,
)
from erp_tools.tools.grid_module.spec import COSTING, PRODUCTION, PURCHASE

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def production_html() -> str:
    return (FIXTURES / "production_detail_page.html").read_text(encoding="utf-8")


# ══════════════════ 模块规格 ══════════════════
def test_module_aliases() -> None:
    assert get_module("production") is PRODUCTION
    assert get_module("生产施工单") is PRODUCTION
    assert get_module("scsgd") is PRODUCTION
    assert get_module("采购订单") is PURCHASE
    assert get_module("产品核价") is COSTING
    assert get_module("CPHJ") is COSTING
    with pytest.raises(Exception):
        get_module("不存在")


def test_modules_registry() -> None:
    assert set(MODULES) == {"production", "purchase", "costing"}
    # 三个列表页都必须填 QXID 兜底值（菜单探测在这三个页面上不可靠）
    for spec in MODULES.values():
        assert spec.fallback_qxid.isdigit()
        assert spec.list_path.endswith("Index.aspx")


def test_filter_aliases_resolve() -> None:
    assert PRODUCTION.control_for("工单状态") == "txtSCZT"
    assert PRODUCTION.control_for("txtSCZT") == "txtSCZT"
    assert PRODUCTION.control_for("状态") == "txtSCZT"
    assert PURCHASE.control_for("材料类别") == "txtCLLB"
    assert COSTING.control_for("签订单") == "txtXKHDD"
    assert PRODUCTION.control_for("不存在的过滤项") == ""


def test_date_fields() -> None:
    assert PRODUCTION.resolve_date_field("交货日期") == "1"
    assert PRODUCTION.resolve_date_field("1") == "1"
    assert COSTING.resolve_date_field("离厂日期") == "LCRQ"
    # 采购订单没有日期字段下拉，固定按制单日期
    assert PURCHASE.date_control == ""
    assert PURCHASE.default_date_field() == "ZDRQ"


# ══════════════════ 详情页解析 ══════════════════
def test_parse_detail_fields(production_html: str) -> None:
    parsed = parse_detail(production_html, record_id="362a2b46-0bdc-4c80-a6e9-c3c423790b95")
    fields = parsed["fields"]
    assert fields["表单编号"] == "SGD2611180011"
    assert fields["产品名称"] == "GH20261109001-NG-国家地理-锁线胶装书 【删除】"
    assert fields["数量"] == "80630"
    assert fields["香港单号"] == "LG26090102-02"
    assert fields["制单人"] == "梁炳"
    # 内部 ID 不应该当业务字段返回
    assert "ID" not in fields


def test_parse_detail_self_check(production_html: str) -> None:
    rid = "362a2b46-0bdc-4c80-a6e9-c3c423790b95"
    assert parse_detail(production_html, record_id=rid)["self_check"] is True
    other = parse_detail(production_html, record_id="00000000-0000-0000-0000-000000000000")
    assert other["self_check"] is False
    assert other["page_record_id"] == rid


def test_parse_detail_grids(production_html: str) -> None:
    grids = parse_detail(production_html)["grids"]
    by_id = {g["table"]: g for g in grids}
    assert set(by_id) == {"", "gridView"}
    part = by_id[""]
    assert len(part["header"]) == 17
    assert part["header"][:4] == ["部件名称", "P数", "色数", "部件尺寸"]
    assert [r["部件名称"] for r in part["rows"]] == ["内文", "封面"]
    assert part["rows"][0]["材料名称"] == "双铜纸157g"

    delivery = by_id["gridView"]
    assert len(delivery["header"]) == 21
    assert [r["序号"] for r in delivery["rows"]] == ["1"]      # 合计行被丢掉
    assert delivery["rows"][0]["送货数量"] == "8000"
    assert delivery["rows"][0]["备注"] == ""                   # `.` 占位当空串


def test_parse_grids_drops_total_rows() -> None:
    html = """
    <table>
      <tr><td>名称</td><td>数量</td><td>金额</td></tr>
      <tr><td>A</td><td>1</td><td>2</td></tr>
      <tr><td></td><td>合计</td><td>2</td></tr>
    </table>
    """
    grids = parse_grids(__import__("bs4").BeautifulSoup(html, "lxml"))
    assert len(grids) == 1
    assert [r["名称"] for r in grids[0]["rows"]] == ["A"]


def test_parse_grids_keeps_named_empty_grid() -> None:
    """有 id 的真实数据网格即使零行也要返回，否则调用方分不清「没有表」和「空表」。

    真实例子：只填了表头的新建采购订单，`gridView2` = 33 列表头 + 0 行。
    """
    html = """
    <table id="gridView2">
      <tr><th>材料编码</th><th>材料名称</th><th>单价</th></tr>
    </table>
    <table>
      <tr><td>甲</td><td>乙</td><td>丙</td></tr>
    </table>
    """
    grids = parse_grids(__import__("bs4").BeautifulSoup(html, "lxml"))
    assert [g["table"] for g in grids] == ["gridView2"]
    assert grids[0]["header"] == ["材料编码", "材料名称", "单价"]
    assert grids[0]["rows"] == []


def test_parse_detail_texts(production_html: str) -> None:
    texts = parse_detail(production_html)["texts"]
    assert texts["txtRemark"] == "请按交期送货"
    assert texts["txtCPMX"] == "10本书收缩为一套"


# ══════════════════ 子页入口 ══════════════════
def test_discover_nodes_finds_production_nodes(production_html: str) -> None:
    entries = {e.node: e for e in discover_nodes(production_html, PRODUCTION.nodes)}
    assert {"part_materials", "part", "product_check", "part_process"} <= set(entries)
    # 费项 / 其他费用只在报价页渲染按钮，施工单目录里不该出现
    assert "freight" not in PRODUCTION.nodes
    assert "other_fees" not in PRODUCTION.nodes

    bjfl = entries["part_materials"]
    assert bjfl.button == "btnSelectBJFL"
    assert bjfl.function == "OpenInputBJFLData"
    assert bjfl.page == "add_bjfl.aspx"
    # 参数下标从函数体反推：PID=0 → lblSelectCPZLPID，BJID=1 → lblSelectBJZLID
    assert bjfl.sets["lblSelectCPZLPID"] == 0
    assert bjfl.sets["lblSelectBJZLID"] == 1
    assert bjfl.sets["txtDJTXID"] == 2


def test_discover_nodes_skips_nodes_without_button(production_html: str) -> None:
    """catalog 里有、但这张单页面没渲染对应按钮的节点不能算可用。"""
    entries = {e.node for e in discover_nodes(production_html, PRODUCTION.nodes)}
    assert "sgkb" not in entries          # BtnOpenSGKBT 没在这个裁剪页里
    assert "cp" not in entries            # btnSelectCPZL 同理


def test_discover_nodes_ignores_function_definition_args(production_html: str) -> None:
    """实参必须是页面里真实调用的 GUID，不能把形参名当实参。"""
    entries = {e.node: e for e in discover_nodes(production_html, PRODUCTION.nodes)}
    args = entries["part_materials"].sample_args
    assert args[0].startswith("8a77ad26")
    assert args[1].startswith("BD3FAF6C")
    assert args[0] != "PID"


def test_node_payload(production_html: str) -> None:
    entries = {e.node: e for e in discover_nodes(production_html, PRODUCTION.nodes)}
    payload = node_payload(entries["part_process"])
    assert payload["lblSelectCPZLPID"] == "8a77ad26-2290-46eb-a6a6-02de5b4031ea"
    assert payload["lblSelectBJZLID"] == "BD3FAF6C-90A7-46CF-A71B-E5C500FA06C6"
    assert payload["lblSelectBJGXLB"] == "1"
    assert payload["txtDJTXID"] == "02"
    assert payload["txtTYSC"] == "0"


def test_node_payload_handles_html_escaped_quotes(production_html: str) -> None:
    """ERP 把 onclick 里的单引号转成 `&#39;`，解析必须先还原。"""
    entries = {e.node: e for e in discover_nodes(production_html, PRODUCTION.nodes)}
    payload = node_payload(entries["product_check"])
    assert payload["lblSelectBJZLID"] == "13F184FD-845B-4A09-9332-6694D8991934"
    assert payload["lblSelectCPZLPID"] == "362a2b46-0bdc-4c80-a6e9-c3c423790b95"


def test_split_args_keeps_commas_inside_quotes() -> None:
    assert split_args("'a,b','c', d") == ["a,b", "c", "d"]
    assert split_args("") == []


def test_set_value_without_semicolon() -> None:
    """页面里有的赋值**不带分号**，正则只能截到行尾，不能把下一行吞进来。

    真实例子（`OpenBrowFXMX`）：
        $HB("lblSelectCPZLPID").value = PID
        $HB("lblSelectCPZLID").value = CPID;
    如果按 `[^;]+` 抓，第一条会把下一行整段当成表达式。
    """
    js = """
    <input type="submit" name="btnBrowFXMX" value="">
    <script>
    function OpenBrowFXMX(PID, CPID, TZZLID) {
        $HB("lblSelectCPZLPID").value = PID
        $HB("lblSelectCPZLID").value = CPID;
        $HB("lblSelectTZCPZLID").value = TZZLID
        $HB("btnBrowFXMX").click();
    }
    </script>
    """
    entry = discover_nodes(js, {"freight": ("Brow_FXMX.aspx", "btnBrowFXMX", "费项")})[0]
    assert entry.sets == {"lblSelectCPZLPID": 0, "lblSelectCPZLID": 1,
                          "lblSelectTZCPZLID": 2}


def test_discover_nodes_requires_rendered_button() -> None:
    """光有 `Open*` 函数不算可用：按钮本身没渲染就不能算节点。

    真实情况：施工单复制了报价页模板，`OpenBrowFXMX` / `OpenInputQTFXData` 的函数体
    还在，但 `btnBrowFXMX` / `btnSelectQTFX` 在 HTML 里根本不存在。
    """
    js = """
    <script>
    function OpenBrowFXMX(PID, CPID) {
        $HB("lblSelectCPZLPID").value = PID;
        $HB("btnBrowFXMX").click();
    }
    </script>
    """
    assert discover_nodes(js, {"freight": ("Brow_FXMX.aspx", "btnBrowFXMX", "费项")}) == []


def test_fill_missing_uses_detail_context() -> None:
    """页面没给实参时，用详情页解析出的 pid/cpid/bjid 按字段名语义回填。"""
    payload = {"lblSelectCPZLPID": "", "lblSelectCPZLID": "", "lblSelectBJZLID": ""}
    ctx = {"pid": "P-1", "cpid": "C-1", "bjid": "B-1", "cpslid": ""}
    filled = fill_missing(payload, ctx)
    assert filled == {"lblSelectCPZLPID": "P-1", "lblSelectCPZLID": "C-1",
                      "lblSelectBJZLID": "B-1"}


def test_detail_context_from_page(production_html: str) -> None:
    ctx = detail_context(production_html, "362a2b46-0bdc-4c80-a6e9-c3c423790b95")
    assert ctx["cpid"] == "8a77ad26-2290-46eb-a6a6-02de5b4031ea"
    assert ctx["bjid"] == "BD3FAF6C-90A7-46CF-A71B-E5C500FA06C6"
    assert ctx["pid"] == "362a2b46-0bdc-4c80-a6e9-c3c423790b95"


def test_popup_url() -> None:
    html = "arrArgs = window.showModalDialog('Add_BJFL.aspx?PID=1&BJID=2', '', 'x');"
    assert popup_url(html) == "Add_BJFL.aspx?PID=1&BJID=2"
    assert popup_url("<html>no popup</html>") == ""


def test_purchase_has_no_data_nodes() -> None:
    """采购订单没有弹窗数据子页，只有供应商选择弹窗。"""
    assert set(PURCHASE.nodes) == {"supplier"}


def test_costing_reuses_price_pages() -> None:
    assert COSTING.list_path == "BaoJia/BJ_KHBJ/Index.aspx"
    assert COSTING.detail_path == "BaoJia/BJ_KHBJ/Add.aspx"
    assert COSTING.fallback_qxid == "1281"
    assert COSTING.nodes == {}          # 子数据走 erp_price_node
