"""下拉字段落库修复的离线测试：text→value 映射、级联清单、中文标签解析。"""

from __future__ import annotations

import pytest

from erp_tools.tools.quote_create.fields import CONTROL_MAP, LABEL_MAP, resolve_control
from erp_tools.tools.quote_create.service import (
    CASCADE_CONTROLS,
    QuoteCreateService,
    _norm_select_text,
    _select_value,
)

#: 真实页面片段：option 的 value 是代码，文字里被排版插入了空格
PAGE = """
<html><body>
<select name="txtZXLB">
  <option value="-0-">&lt;--请选择--&gt;</option>
  <option selected="selected" value="008">普通纸箱</option>
  <option value="009">半翼 箱纸箱</option>
  <option value="066">特殊纸箱</option>
</select>
<select name="txtZBLX">
  <option value="007">特殊(看描述)</option>
  <option value="002">A=B双坑纸板</option>
  <option value="001">A3B单坑纸板</option>
</select>
<select name="txtZXFS">
  <option value="0">平装</option>
  <option value="1">竖装</option>
</select>
<select name="txtSHFS">
  <option value="-0-">&lt;--请选择--&gt;</option>
  <option value="FOB 盐田">FOB 盐 田</option>
</select>
<select name="txtSHDQ"></select>
</body></html>
"""


# ══════════════════ 归一化 ══════════════════
def test_norm_select_text_strips_all_spaces() -> None:
    assert _norm_select_text("半翼 箱纸箱") == "半翼箱纸箱"
    assert _norm_select_text(" FOB 盐\u00a0田 ") == "FOB盐田"
    assert _norm_select_text(None) == ""


# ══════════════════ text → value ══════════════════
def test_select_value_maps_text_to_code() -> None:
    assert _select_value(PAGE, "txtZXLB", "特殊纸箱") == "066"
    assert _select_value(PAGE, "txtZBLX", "A3B单坑纸板") == "001"
    assert _select_value(PAGE, "txtZXFS", "竖装") == "1"


def test_select_value_tolerates_layout_spaces() -> None:
    """页面把「半翼箱纸箱」渲染成「半翼 箱纸箱」时也必须匹配上。"""
    assert _select_value(PAGE, "txtZXLB", "半翼箱纸箱") == "009"
    assert _select_value(PAGE, "txtSHFS", "FOB 盐田") == "FOB 盐田"


def test_select_value_accepts_code_directly() -> None:
    assert _select_value(PAGE, "txtZXLB", "066") == "066"
    assert _select_value(PAGE, "txtZXFS", "1") == "1"


def test_select_value_none_when_no_option() -> None:
    assert _select_value(PAGE, "txtSHDQ", "香港") is None      # 级联下游此时是空的
    assert _select_value(PAGE, "txtZXLB", "不存在的纸箱") is None
    assert _select_value(PAGE, "no_such_control", "x") is None


# ══════════════════ 批量映射 ══════════════════
def test_map_product_selects_reports_unresolved() -> None:
    changes = {"txtZXLB": "特殊纸箱", "txtZXFS": "竖装",
               "txtSHDQ": "香港", "txtCPMS": "自由文本"}
    mapped, unresolved = QuoteCreateService._map_product_selects(PAGE, changes)
    assert mapped["txtZXLB"] == "066"
    assert mapped["txtZXFS"] == "1"
    assert mapped["txtCPMS"] == "自由文本"        # 非下拉原样保留
    assert unresolved == ["txtSHDQ"]              # 空下拉 → 明确报出来
    assert mapped["txtSHDQ"] == "香港"            # 但也不静默丢，原样发


def test_cascade_controls_match_page_onchange() -> None:
    """这 7 个是页面上 onchange=__doPostBack 的下拉（实测抓取）。"""
    assert set(CASCADE_CONTROLS) == {
        "txtCPDL", "txtBZ", "txtFSCMC", "txtYSLX", "txtFPLB", "txtXGBZ", "txtCPYS",
    }
    assert "txtSHDQ" not in CASCADE_CONTROLS      # 它是下游，不回发自己


# ══════════════════ 中文标签 ══════════════════
def test_resolve_control_accepts_chinese_label() -> None:
    assert resolve_control("纸箱类别") == "txtZXLB"
    assert resolve_control("送货地区") == "txtSHDQ"
    assert resolve_control("书脊方向") == "Drop_SJFX"
    assert resolve_control("纸箱类别：") == "txtZXLB"     # 标签带冒号
    assert resolve_control("txtZXLB") == "txtZXLB"        # 控件名
    assert resolve_control("carton_type") == "txtZXLB"    # 英文别名


def test_every_label_is_resolvable() -> None:
    """注册表里的中文标签都要能解析回自己的控件。"""
    for label, control in LABEL_MAP.items():
        assert resolve_control(label) == control
        assert control in CONTROL_MAP


def test_unknown_field_still_errors_with_candidates() -> None:
    assert resolve_control("完全不存在的字段") is None
    with pytest.raises(Exception) as exc:
        QuoteCreateService._resolve_product_fields({"完全不存在的字段": 1})
    assert "未知的产品字段" in str(exc.value)
