"""客户报价子数据（数量档 / 部件辅料 / 费项明细）解析测试。"""

from erp_tools.tools.quote_price import (
    parse_fees_node,
    parse_materials_node,
    parse_part_node,
    parse_quantities_node,
)


def test_quantities_node_rows(fixture_text):
    rows = parse_quantities_node(fixture_text("price_node_quantities.html"))

    assert len(rows) == 10
    first = rows[0]
    assert first["数量"] == "2000"
    assert first["单价"] == "13.12183"
    assert first["金额"] == "26243.65"
    assert first["折扣率"] == "10"
    assert first["折后单价"] == "13.12183"
    assert first["单款数量"] == "2000"
    assert [r["数量"] for r in rows][:4] == ["2000", "3000", "5000", "7000"]


def test_materials_node_rows(fixture_text):
    rows = parse_materials_node(fixture_text("price_node_materials.html"))

    assert rows, "没有解析到材料行"
    first = rows[0]
    assert first["部件名称"]
    assert first["材料名称"]
    assert "系数" in first
    assert "材料长" in first and "材料宽" in first
    # 页面同一批材料会出现两遍，必须去重
    keys = [tuple(sorted(r.items())) for r in rows]
    assert len(keys) == len(set(keys))


def test_fees_node_groups_and_categories(fixture_text):
    payload = parse_fees_node(fixture_text("price_node_fees.html"))

    groups = payload["groups"]
    assert len(groups) == 1
    group = groups[0]
    assert group["数量档"] == "2000"
    assert group["单价"] == "0.26984"
    assert group["金额"] == "539.68"
    assert set(group["fees"]) == {"纸张费用", "辅料费用", "印前费用", "印刷费用",
                                 "版费", "印后费用", "其他费用"}

    paper = group["fees"]["纸张费用"]
    assert paper and paper[0]["部件"] == "封面"
    assert "157g双粉纸" in paper[0]["项目"]
    assert paper[0]["金额"] == "395.34"
    assert "公式" in paper[0]


def test_part_node_without_values_is_empty():
    """部件页在只读态只回 txtID，字段全空——解析器不要凭空造数据。"""
    payload = parse_part_node("<html><body><form id='form1'></form></body></html>")

    assert payload["fields"] == {}
    assert payload["selects"] == {}


def test_part_node_reads_label_grid():
    html = """
    <html><body><form id='form1'><table>
      <tr><td>部件名称：</td><td><input value="封面"></td>
          <td>P数：</td><td><input value="2"></td>
          <td>部件尺寸：</td><td>×</td>
          <td>比例关系：</td><td><select name="Drop_BJBL">
            <option selected="selected" value="1">自动正比关系</option></select></td></tr>
      <tr><td>书脊方向：</td><td><select name="txtSJFX">
            <option value="-0-">&lt;--请选择--&gt;</option>
            <option selected="selected" value="2">长书脊</option></select></td></tr>
    </table></form></body></html>
    """
    payload = parse_part_node(html)

    assert payload["fields"]["部件名称"] == "封面"
    assert payload["fields"]["P数"] == "2"
    assert payload["fields"]["书脊方向"] == "长书脊"
    assert payload["selects"]["txtSJFX"] == "长书脊"
