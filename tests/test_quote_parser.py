"""报价需求页解析测试（夹具为真实记录页）。"""

from erp_tools.tools.quote_request import parse_quote_page


def test_parse_real_page(fixture_text):
    html = fixture_text("quote_page.html")
    data = parse_quote_page(html, fallback_ywy="李东员", record_id="rec-1")

    assert data["id"] == "rec-1"
    assert data["BDBH"] == "BJXQ260204009-02"
    assert data["CPMC"]                     # 产品名称
    assert data["KHDDH"] == "DY26020406-02"  # 香港单号
    assert data["CPMS"]                     # 产品描述（标签行 textarea）
    assert data["YWY"]                      # 业务员（空则用 CSV 制单人兜底）
    assert isinstance(data["CD"], int) and isinstance(data["KD"], int)

    # 数量解析：字符串带单位时转数字，'.' 视为 None
    for i in range(1, 11):
        assert f"CPSL{i}" in data


def test_label_scope_fallback_handles_layout_b():
    """布局 B：tdCPZLLIst 有内容但不含「产品描述」行，行在 Pan_gridView7 里。"""
    html = """
    <table>
      <tr><td id="tdCPZLLIst">
        <table><tr><td>产品名称：</td><td><input value="测试产品"/></td></tr></table>
      </td></tr>
      <tr><td><div id="Pan_gridView7">
        <table>
          <tr><td>产品描述：</td><td><textarea>共【2】款，每款【3】语言</textarea></td></tr>
          <tr><td>备注：</td><td><textarea>明细区备注</textarea></td></tr>
        </table>
      </div></td></tr>
    </table>
    """
    data = parse_quote_page(html)
    assert data["CPMC"] == "测试产品"
    assert data["CPMS"].startswith("共【2】款")
    assert data["TZS"] == 2 and data["YYS"] == 3
    assert data["Remark"] == "明细区备注"


def test_cpsl_ignores_summary_quantity():
    """onclick 第一个参数为 '-1' 的是汇总数量，不能当 CPSL1。"""
    html = """
    <div id="Pan_gridView7"><table>
      <tr><td>产品数量：</td>
          <td><input value="100" onclick="OpenInputCPSLZLData('AAA-1',1)"/></td>
          <td><input value="200" onclick="OpenInputCPSLZLData('AAA-2',2)"/></td></tr>
      <tr><td>数量：</td><td><input value="300本" onclick="OpenInputCPSLZLData('-1',0)"/></td></tr>
    </table></div>
    """
    data = parse_quote_page(html)
    assert data["CPSL1"] == 100
    assert data["CPSL2"] == 200
    assert data["CPSL3"] is None


def test_missing_textarea_yields_none_not_empty():
    data = parse_quote_page("<html><body><div id='Pan_gridView7'></div></body></html>")
    assert data["CPMS"] == ""
    assert data["Remark1"] is None
    assert data["Remark3"] is None
