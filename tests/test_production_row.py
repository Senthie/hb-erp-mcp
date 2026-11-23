"""生产施工单「列表行内点击事件」：动作解析、URL 拼装、弹窗解析、行内事件提取。"""

from __future__ import annotations

import pytest

from erp_tools.core.errors import ParseError
from erp_tools.tools.gridlist import extract_row_actions, parse_grid_page
from erp_tools.tools.grid_module.row_action import (
    PRODUCTION_ROW_ACTIONS,
    build_url,
    parse_popup,
    resolve_action,
)

PID = "30a7b30c-67aa-4550-a1b5-0638cdbca708"
CPID = "204731AE-8832-41C5-90B2-8E742CFE1BEA"


# ══════════════════ 动作解析 ══════════════════
def test_resolve_action_by_key_and_alias() -> None:
    assert resolve_action("esign") == "esign"
    assert resolve_action("电子稿袋") == "esign"
    assert resolve_action("材料清单") == "clqd"
    assert resolve_action("打印") == "print"
    assert resolve_action("WJQK") == "wjqk"


def test_resolve_action_unknown_lists_candidates() -> None:
    with pytest.raises(ParseError) as exc:
        resolve_action("不存在的动作")
    assert "可用" in str(exc.value) and "esign" in str(exc.value)


def test_build_url_for_each_usable_action() -> None:
    for key, spec in PRODUCTION_ROW_ACTIONS.items():
        if not spec.usable:
            continue
        url = build_url(key, pid=PID, cpid=CPID, qxid="740", uid="1662")
        assert url.startswith("/Admin/")
        assert "{" not in url, f"{key} 还有未替换的占位符"
        assert "1662" in url or key == "clqd" or key == "print" or key == "esign"


def test_build_url_needs_product_id() -> None:
    with pytest.raises(ParseError, match="cpid"):
        build_url("gjqk", pid=PID)          # 跟进信息只要 cpid
    with pytest.raises(ParseError, match="pid"):
        build_url("print", pid="", cpid=CPID)


def test_build_url_refuses_unusable_actions() -> None:
    for key in ("info", "bjremark", "cphj"):
        with pytest.raises(ParseError) as exc:
            build_url(key, pid=PID, cpid=CPID)
        assert PRODUCTION_ROW_ACTIONS[key].label in str(exc.value)


def test_catalog_entries_mark_usability() -> None:
    infos = [spec.as_dict() for spec in PRODUCTION_ROW_ACTIONS.values()]
    keys = {i["action"] for i in infos}
    assert {"esign", "gjqk", "zhzy", "wjqk", "zszm", "clqd", "print"} <= keys
    for info in infos:
        if info["usable"]:
            assert info["page"] and "note" not in info
        else:
            assert info["note"]


# ══════════════════ 行内事件提取 ══════════════════
ROW = f"""
<tr ondblclick="OnDBClickOpenData('{PID}','740',3)">
  <td><input type="checkbox" name="gridView$ctl02$0" /></td>
  <td><img onclick="showMessagePop('{PID}')" /></td>
  <td><img onclick="IndexPrint('{PID}')" /></td>
  <td>SGD2611230005</td>
  <td onclick="OpenSelectWJQK('{CPID}')">有</td>
  <td onclick="OpenLinkGJQK('{CPID}')">有</td>
  <td onclick="LinCLQD('','3029','740')">x</td>
  <td onclick="OpenDirFileList('{PID}','{CPID}')">y</td>
</tr>
"""


def test_extract_row_actions_finds_cpid_and_markers() -> None:
    from erp_tools.core.aspnet import soup_of

    tr = soup_of(f"<table>{ROW}</table>").find("tr")
    found = extract_row_actions(tr)
    assert found["产品ID"] == CPID
    assert set(found["可点动作"]) >= {"info", "print", "wjqk", "gjqk", "clqd", "esign"}


def test_parse_grid_page_carries_cpid() -> None:
    html = f"""
    <html><body>
    <div id="gViewDiv"><table id="gridView">
      <tr class="FixedTitleRow"><td>选择</td><td>信息</td><td>打印</td>
        <td>生产单号</td><td>工单状态</td><td>客户名称</td><td>产品名称</td></tr>
      {ROW}
    </table></div></body></html>
    """
    page = parse_grid_page(html)
    assert len(page.rows) == 1
    row = page.rows[0]
    assert row["记录ID"] == PID
    assert row["产品ID"] == CPID
    assert "clqd" in row["可点动作"]


def test_parse_grid_page_without_actions_has_no_extra_keys() -> None:
    html = """
    <html><body>
    <div id="gViewDiv"><table id="gridView">
      <tr class="FixedTitleRow"><td>选择</td><td>单号</td><td>状态</td><td>客户</td></tr>
      <tr ondblclick="OnDBClickOpenData('id-1','740',3)">
        <td><input type="checkbox" /></td><td>SGD1</td><td>编辑中</td><td>IBL10</td></tr>
    </table></div></body></html>
    """
    row = parse_grid_page(html).rows[0]
    assert row["记录ID"] == "id-1"
    assert "产品ID" not in row and "可点动作" not in row


# ══════════════════ 弹窗解析 ══════════════════
POPUP = """
<html><head><title>【产品跟进信息】</title></head><body>
<table id="gridView3">
  <tr><td>序号</td><td>日期</td><td>跟进内容</td></tr>
  <tr><td>1</td><td>2026-11-20</td><td>客户确认刀线</td></tr>
  <tr><td></td><td></td><td></td></tr>
  <tr><td>2</td><td>2026-11-21</td><td>补版</td></tr>
</table>
<a href="../../BaoJia/BJ_KHBJXQ/X.pdf">刀线图</a>
<a href="javascript:__doPostBack('x','')">排序</a>
<a href="#">空</a>
<textarea name="txtRemark">备注内容</textarea>
</body></html>
"""


def test_parse_popup_tables_links_texts() -> None:
    out = parse_popup(POPUP, base_path="/Admin/ShengChan/SC_SCSGD/")
    assert out["title"] == "【产品跟进信息】"
    table = out["tables"][0]
    assert table["id"] == "gridView3"
    assert table["headers"] == ["序号", "日期", "跟进内容"]
    assert [r["跟进内容"] for r in table["rows"]] == ["客户确认刀线", "补版"]  # 空行被丢
    assert table["count"] == 2 and table["truncated"] is False

    assert [link["text"] for link in out["links"]] == ["刀线图"]              # js/# 被过滤
    assert out["links"][0]["href"] == "/Admin/BaoJia/BJ_KHBJXQ/X.pdf"
    assert out["texts"] == {"txtRemark": "备注内容"}


def test_parse_popup_truncates_long_tables() -> None:
    rows = "".join(f"<tr><td>{i}</td><td>v{i}</td></tr>" for i in range(30))
    html = f"<html><body><table><tr><td>a</td><td>b</td></tr>{rows}</table></body></html>"
    out = parse_popup(html, max_rows=5)
    assert out["tables"][0]["count"] == 30
    assert len(out["tables"][0]["rows"]) == 5
    assert out["tables"][0]["truncated"] is True


def test_parse_popup_empty_page() -> None:
    out = parse_popup("<html><body>没有表格</body></html>")
    assert out == {"title": "", "tables": [], "links": [], "texts": {}}
