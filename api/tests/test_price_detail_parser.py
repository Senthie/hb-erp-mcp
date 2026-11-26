"""客户报价详情页（BJ_KHBJ/Add.aspx）解析测试。"""

from erp_tools.tools.quote_price import parse_price_detail


def _detail(fixture_text):
    return parse_price_detail(fixture_text("price_detail_page.html"))


def test_ids_come_from_lblid_and_product_span(fixture_text):
    detail = _detail(fixture_text)

    assert detail.record_id == "89ad5e46-4872-4b26-83f5-18cab4fe2fe4"
    assert detail.pid == detail.record_id
    # CPID 是产品明细 ID，来自 OpenInputCPZLData('<CPID>','<PID>','0')
    assert detail.cpid == "CCEBDA70-6193-4A31-A920-05B4DB85D8B9"
    assert detail.bdbh == "KHBJ260915002-07"


def test_header_labels_and_scalars(fixture_text):
    header = _detail(fixture_text).header

    assert header["表单编号"] == "KHBJ260915002-07"
    assert header["业务人员"] == "许月霞"
    assert header["客户资料"] == "IBL10"
    assert header["单价体系"] == "IV级(NGV)"
    assert header["跟单员"] == "阮春亮"
    assert header["币种"] == "美元"
    # 汇率/发票税率/折扣率没有标签格，按控件名取
    assert header["汇率"] == "6.6475"
    assert header["折扣率"] == "10"
    # 未选中的下拉必须是空串，不能把整串 option 文字当值
    assert header["付款方式"] == ""


def test_product_block(fixture_text):
    product = _detail(fixture_text).product

    assert product["产品名称"].startswith("Shirley 点钻迪士尼贴纸骑马钉书")
    assert "【" not in product["产品名称"]           # 【 删除】按钮文字要去掉
    assert product["规格"] == "195*265*0"
    assert product["装订方式"] == "骑马钉"
    assert product["香港单号"] == "XY26091502-04"
    assert product["数量"] == "6000套"
    assert product["单价"] == "15.18237"
    assert product["款数"] == "2"
    assert product["语言数"] == "1"
    assert product["修改记录"]


def test_quantities_are_fixed_width_groups(fixture_text):
    """数量明细表：只第一组表头写「产品数量」，后面写「数量」；空值用 `.` 占位。"""
    quantities = _detail(fixture_text).quantities

    assert [q["数量"] for q in quantities][:7] == [
        "6000", "10000", "15000", "20000", "40000", "50000", "60000",
    ]
    # 单价/金额在只看详情页时是 `.`（要点开数量弹窗才有），解析时归一成空串
    assert quantities[0]["单价"] == "" and quantities[0]["金额"] == ""
    # 不能把后面部件表的行也当成数量行
    assert all(q["数量"] != "部件名称" for q in quantities)


def test_parts_include_sub_rows(fixture_text):
    parts = _detail(fixture_text).parts

    assert len(parts) == 6
    first = parts[0]
    assert first["部件名称"] == "封面"
    assert first["P数"] == "2"
    assert first["材料名称"].startswith("230g单粉纸卷筒889")
    assert first["开料尺寸"]
    # 跟在部件行后面的「印前工序 / 印刷机台 / 印后工序」要挂到该部件上
    assert first["印前工序"] == "分纸->切纸"
    assert "机台" in first["印刷机台"]
    assert first["印后工序"].startswith("过UV")
    assert parts[1]["部件名称"] == "内文"


def test_fees_and_audit(fixture_text):
    detail = _detail(fixture_text)

    assert detail.fees["纸箱费用"] == "318"
    assert detail.fees["台板费用"] == "352"
    assert detail.fees["海运费用"] == "3512"
    assert detail.fees["国内运费"] == "600"
    assert detail.audit["制单人"] == "陈亚蕊"
    assert detail.audit["制单日期"].startswith("2026/11/12")


def test_description_is_the_long_textarea(fixture_text):
    description = _detail(fixture_text).description

    assert description.startswith("书籍方向：【长书脊】")
    assert "【许月霞" in description
    assert len(description) > 1000


def test_real_page_has_no_diagnostics(fixture_text):
    assert _detail(fixture_text).diagnostics == []


# ══════════════════ 选配项目列表（不是每张单都有） ══════════════════
def test_record_without_optionals_has_empty_list(fixture_text):
    """KHBJ260915002-07 这张单页面上没有「选配项目列表」→ 空列表且不算异常。"""
    detail = _detail(fixture_text)
    assert detail.optionals == []
    assert detail.diagnostics == []


def test_optionals_are_grouped_by_item(fixture_text):
    """KHBJ261112003 多了「选 配 项 目 列 表」：选配项目/数量/单价/金额 ×3 组，要按项目归组。"""
    detail = parse_price_detail(fixture_text("price_detail_optionals_page.html"))

    assert detail.bdbh == "KHBJ261112003"
    assert len(detail.optionals) == 9
    first = detail.optionals[0]
    assert first["选配项目"] == "【封面】2mm厚(击凸版)(铜版)报价专用100%"
    assert [t["数量"] for t in first["tiers"]] == [
        "2000", "3000", "5000", "7000", "10000", "12000", "15000", "18000", "20000", "30000",
    ]
    assert first["tiers"][0]["单价"] == "0.523625"
    assert first["tiers"][0]["金额"] == "1047.25"


def test_optionals_drop_exact_duplicate_tiers(fixture_text):
    """ERP 里【封面】烫金整段重复了一遍，去重后每项只有 10 档。"""
    detail = parse_price_detail(fixture_text("price_detail_optionals_page.html"))
    by_name = {item["选配项目"]: item for item in detail.optionals}

    hot = by_name["【封面】烫金"]
    assert len(hot["tiers"]) == 10
    assert hot["dropped_duplicates"] == 10
    assert len({t["数量"] for t in hot["tiers"]}) == len(hot["tiers"])
    # 其他项都没有重复
    assert all(item.get("dropped_duplicates", 0) == 0
               for name, item in by_name.items() if name != "【封面】烫金")


def test_optionals_do_not_leak_into_quantities(fixture_text):
    detail = parse_price_detail(fixture_text("price_detail_optionals_page.html"))

    assert len(detail.quantities) == 10
    assert all(q["数量"] != "选配项目" for q in detail.quantities)
    assert len(detail.parts) == 7
    assert detail.diagnostics == []


def test_optionals_keep_interleaved_price_curves(fixture_text):
    """同一个选配项目有两条价格曲线时（同数量两种单价），不能丢、也不能猜归属。

    实测 `KHBJ261016002-01`：【产品】0.02mm厚PP空白收缩袋 10 档数量各出现两次，
    单价成对出现（如 1000 → 0.07124 / 0.08275）。
    """
    detail = parse_price_detail(fixture_text("price_detail_optionals_multi_page.html"))

    assert detail.bdbh == "KHBJ261016002-01"
    by_name = {item["选配项目"]: item for item in detail.optionals}

    bag = by_name["【产品】0.02mm厚PP空白收缩袋（报价专用）"]
    assert len(bag["tiers"]) == 20
    assert bag["同名重复档"] == 10
    first_two = bag["tiers"][:2]
    assert [t["数量"] for t in first_two] == ["1000", "1000"]
    assert {t["单价"] for t in first_two} == {"0.07124", "0.08275"}
    # 档位按（数量, 单价）排序，两条曲线相邻
    assert [t["数量"] for t in bag["tiers"][:4]] == ["1000", "1000", "2000", "2000"]


def test_broken_page_reports_diagnostics():
    detail = parse_price_detail("<html><body>nothing</body></html>")

    assert detail.product == {} and detail.parts == []
    assert any("表头" in item for item in detail.diagnostics)
    assert "页面没有部件明细表" in detail.diagnostics
    assert "页面没有数量明细表（只有汇总数量）" in detail.diagnostics


def test_page_without_quantity_or_part_tables_is_not_an_error():
    """套装单（如 KHBJ261111007）页面上根本没有数量/部件明细表，不能报“结构变了”。"""
    html = """
    <html><body><form id="form1">
      <input id="lblID" name="lblID" value="7f7dea9b-5e03-417d-81aa-2657953b7235">
      <input name="txtBDBH" id="txtBDBH" value="KHBJ261111007">
      <table id="Main_Top"><tr>
        <td>表单编号：</td><td>KHBJ261111007</td>
        <td>业务人员：</td><td><select name="txtYGXM"><option selected="selected"
          value="da2bd172">许月霞</option></select></td>
        <td>客户资料：</td><td>AUZ21</td>
      </tr></table>
      <table>
        <tr><td>产品名称：</td><td><span onclick="OpenInputCPZLData('CPID-1','PID-1','0')">套装产品</span></td>
            <td>规格：</td><td>240×20×308</td><td>装订方式：</td><td>套装</td></tr>
        <tr><td>香港单号：</td><td>XY26111101</td><td>数量：</td><td>400套</td>
            <td>单价：</td><td>11.01103</td><td>金额：</td><td>4404.41</td></tr>
      </table>
    </form></body></html>
    """
    detail = parse_price_detail(html)

    assert detail.parts == [] and detail.quantities == []
    assert "页面没有部件明细表" in detail.diagnostics
    assert "页面没有数量明细表（只有汇总数量）" in detail.diagnostics
    assert not any("结构可能变了" in item for item in detail.diagnostics)
