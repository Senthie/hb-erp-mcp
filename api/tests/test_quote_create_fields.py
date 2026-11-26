"""`quote_create.fields` / `normalize_product_fields`：明细子页字段白名单（全离线）。

规则（2026-11-12 起）：**只有 DOM 首屏可见的字段能上传**；
折叠面板（`display:none`）里的 142 个字段一律拒绝，`allow_readonly` 也不能绕过。
"""

from __future__ import annotations

import pytest

from erp_tools.tools.quote_create import (
    ALIAS_MAP,
    CONTROL_MAP,
    PRODUCT_FIELDS,
    REQUIRED_ALIASES,
    REQUIRED_PRODUCT_FIELDS,
    STATE_FIELDS,
    UPLOAD_CONTROLS,
    VISIBLE_CONTROLS,
    normalize_product_fields,
    parse_product_controls,
    resolve_control,
    upload_payload_schema,
)


# ══════════════════ 注册表 ══════════════════
def test_registry_covers_the_whole_sub_page() -> None:
    """2026-11-12 抓到的真实页面是 205 个控件（195 业务 + 10 状态域）。"""
    controls = {spec.control for spec in PRODUCT_FIELDS}
    assert len(controls) == 195
    assert len(VISIBLE_CONTROLS) == 53


def test_only_visible_and_writable_fields_are_uploadable() -> None:
    """可见性是硬约束：隐藏字段一个都不在 UPLOAD_CONTROLS 里。"""
    assert len(UPLOAD_CONTROLS) == 51
    assert UPLOAD_CONTROLS <= VISIBLE_CONTROLS
    # 首屏可见但只读的两个：香港单号 / 客户地区
    assert set(VISIBLE_CONTROLS) - UPLOAD_CONTROLS == {"txtKHDDH", "txtKHDQ"}
    # 隐藏字段全部被挡住
    hidden = {s.control for s in PRODUCT_FIELDS
              if not s.visible and s.control not in STATE_FIELDS}
    assert len(hidden) == 142
    assert not (hidden & UPLOAD_CONTROLS)


def test_field_spec_exposes_visible_and_uploadable_flags() -> None:
    packing = CONTROL_MAP["txtBZFS"]
    assert packing.visible is True
    assert packing.uploadable is True

    exchange_rate = CONTROL_MAP["txtHL"]
    assert exchange_rate.visible is False
    assert exchange_rate.uploadable is False

    hk_no = CONTROL_MAP["txtKHDDH"]
    assert hk_no.visible is True
    assert hk_no.writable is False
    assert hk_no.uploadable is False


def test_legacy_aliases_are_unchanged_and_all_uploadable() -> None:
    expected = {
        "binding": "txtZDFS",
        "packing": "txtBZFS",
        "pallet": "txtTBLX",
        "delivery": "txtSHFS",
        "delivery_date": "txtJHRQ",
        "expected_date": "txtWCRQ",
        "parts_desc": "txtRemark1",
        "modify_log": "txtRemark3",
        "inspection": "txtRemark4",
        "product_desc": "txtCPMS",
        "attribute": "txtCPSX",
        "product_type": "txtCPLX",
        "cases": "txtTZS",
        "languages": "txtYYS",
    }
    for alias, control in expected.items():
        assert ALIAS_MAP[alias] == control
        assert control in UPLOAD_CONTROLS, f"{alias} 必须仍然可以上传"


def test_aliases_are_unique_and_resolvable() -> None:
    seen: dict[str, str] = {}
    for spec in PRODUCT_FIELDS:
        names = (spec.alias, *spec.extra_aliases)
        for name in names:
            if not name:
                continue
            assert name not in seen or seen[name] == spec.control, f"别名 {name} 冲突"
            seen[name] = spec.control
    for control in CONTROL_MAP:
        assert resolve_control(control) == control
    assert resolve_control("查无此字段") is None


def test_required_fields_are_all_uploadable() -> None:
    """14 个 ERP 必填字段必须都在可上传集合里（否则调用方永远凑不齐）。"""
    assert len(REQUIRED_PRODUCT_FIELDS) == 14
    assert REQUIRED_PRODUCT_FIELDS <= UPLOAD_CONTROLS
    assert set(REQUIRED_ALIASES) == {
        CONTROL_MAP[c].alias for c in REQUIRED_PRODUCT_FIELDS
    }
    for control in REQUIRED_PRODUCT_FIELDS:
        assert CONTROL_MAP[control].required is True
    # 产品类别与产品数量不在里面：前者靠档案回填、后者由 qty 自动写
    assert "txtCPLB_Input" not in REQUIRED_PRODUCT_FIELDS
    assert "txtCPSL" not in REQUIRED_PRODUCT_FIELDS


# ══════════════════ normalize_product_fields ══════════════════
def test_normalize_accepts_alias_control_and_fields() -> None:
    fields, errors, warnings = normalize_product_fields({
        "name": "彩盒",
        "spec": "210*148",
        "qty": 100,
        "packing": "纸箱包装",                       # 别名
        "txtCPSX": "三岁以上",                       # 控件名
        "fields": {"txtCD": "210", "inspection": "无"},  # 显式分组
    })

    assert errors == []
    assert warnings == []
    assert fields == {
        "txtBZFS": "纸箱包装",
        "txtCPSX": "三岁以上",
        "txtCD": "210",
        "txtRemark4": "无",
    }
    assert "name" not in fields and "fields" not in fields


def test_normalize_no_longer_drops_remark_and_description() -> None:
    """旧版把 description/remark 收下又静默丢掉——现在必须落到页面字段。"""
    fields, errors, _ = normalize_product_fields(
        {"name": "x", "spec": "1*1", "qty": 1, "remark": "加急", "description": "四色"}
    )

    assert errors == []
    assert fields["txtRemark"] == "加急"
    assert fields["txtCPMS"] == "四色"


@pytest.mark.parametrize("key", [
    "exchange_rate",       # txtHL 汇率（折叠面板）
    "txtTMLX",             # 条码类型（折叠面板）
    "payment_term",        # 付款期限（折叠面板）
    "qty_total",           # 汇总数量 txtCPSL（隐藏，只能由 qty 间接写）
    "packaging_note_3",    # 包装备注（折叠面板）
])
def test_normalize_rejects_hidden_fields_even_with_allow_readonly(key: str) -> None:
    fields, errors, _ = normalize_product_fields(
        {"name": "x", "spec": "1*1", "qty": 1, key: "1", "allow_readonly": True}
    )

    assert fields == {}
    assert len(errors) == 1
    assert "不可见" in errors[0]


def test_normalize_blocks_readonly_visible_field() -> None:
    _, errors, _ = normalize_product_fields({"name": "x", "hk_order_no": "XY123"})
    assert errors and "只读" in errors[0]

    # 可见的只读字段仍可由 allow_readonly 显式放行
    fields, errors, _ = normalize_product_fields(
        {"name": "x", "hk_order_no": "XY123", "allow_readonly": True}
    )
    assert errors == []
    assert fields["txtKHDDH"] == "XY123"


def test_normalize_reports_unknown_key_with_hint() -> None:
    _, errors, _ = normalize_product_fields({"name": "x", "packingg": "纸箱包装"})

    assert len(errors) == 1
    assert "packingg" in errors[0]
    assert "packing" in errors[0]


def test_normalize_converts_checkboxes() -> None:
    fields, errors, _ = normalize_product_fields({
        "name": "x",
        "no_pallet": True,
        "no_container": False,
        "has_schedule": "on",
    })

    assert errors == []
    assert fields["chkBSYTB"] == "on"
    assert "chkBSYJZX" not in fields      # False = 不提交
    assert fields["chkZDBJF"] == "on"


def test_normalize_warns_on_unknown_select_value() -> None:
    fields, errors, warnings = normalize_product_fields({
        "name": "x",
        "packing": "编织袋包装",          # 离线选项表里没有
    })

    assert errors == []
    assert fields["txtBZFS"] == "编织袋包装"
    assert warnings and "编织袋包装" in warnings[0]


def test_normalize_rejects_state_fields() -> None:
    _, errors, _ = normalize_product_fields({"name": "x", "__VIEWSTATE": "abc"})
    assert errors and "状态域" in errors[0]


@pytest.mark.parametrize("key", ["qty_1", "qty_10"])
def test_visible_multi_slot_families_are_reachable(key: str) -> None:
    fields, errors, _ = normalize_product_fields({"name": "x", key: "12"})
    assert errors == []
    assert list(fields.values()) == ["12"]


# ══════════════════ 上传 JSON Schema ══════════════════
def test_upload_schema_only_lists_visible_fields() -> None:
    payload = upload_payload_schema()
    schema = payload["json_schema"]
    props = schema["properties"]["fields"]["properties"]

    assert schema["required"] == ["name", "spec", "qty"]
    assert schema["additionalProperties"] is False
    assert len(props) == 51 == payload["allowed"]["count"]

    allowed_controls = {p["x-control"] for p in props.values()}
    assert allowed_controls == UPLOAD_CONTROLS
    assert "txtHL" not in allowed_controls
    assert "txtBZFS" in allowed_controls

    # 隐藏字段单独列出来，明确告知不可写
    assert payload["excluded"]["hidden_count"] == 142
    assert "txtHL" in payload["excluded"]["hidden"]
    assert payload["excluded"]["readonly"] == ["txtKHDDH", "txtKHDQ"]


def test_upload_schema_types_and_enum() -> None:
    props = upload_payload_schema()["json_schema"]["properties"]["fields"]["properties"]

    assert props["packing"]["type"] == "string"
    assert props["packing"]["enum"] == ["纸箱包装", "牛皮纸包装", "胶袋包装", "卡板摆放"]
    assert props["no_pallet"]["type"] == "boolean"
    assert props["cases"]["type"] == "number"
    assert props["delivery_date"]["type"] == "string"


def test_upload_example_only_uses_allowed_keys() -> None:
    payload = upload_payload_schema()
    example = payload["example"]
    allowed = set(payload["allowed"]["aliases"])

    assert set(example) >= {"name", "spec", "qty", "fields"}
    assert set(example["fields"]) <= allowed
    assert example["fields"]["unit"] == "本"


# ══════════════════ parse_product_controls ══════════════════
def test_parse_product_controls_reads_values_and_hidden_state() -> None:
    html = """
    <html><body><form>
      <table><tr>
        <td>包装方式：</td>
        <td><select name="txtBZFS">
          <option value="-0-">&lt;--请选择--&gt;</option>
          <option value="01" selected>纸箱包装</option>
        </select></td>
      </tr>
      <tr><td>产品描述：</td><td><textarea name="txtCPMS">四色</textarea></td></tr>
      <tr><td><input type="checkbox" name="chkBSYTB" checked /></td></tr>
      <tr><td><input type="text" name="txtCPSL" value="3000" /></td></tr>
      <tr><td><input type="submit" name="btnSave" value="保存" /></td></tr>
      </table>
      <table id="TableVisible" style="display:none">
        <tr><td>汇率：</td><td><input type="text" name="txtHL" value="7.2" /></td></tr>
      </table>
    </form></body></html>
    """

    controls = {c.control: c for c in parse_product_controls(html)}

    assert set(controls) == {"txtBZFS", "txtCPMS", "chkBSYTB", "txtCPSL", "txtHL"}
    assert controls["txtBZFS"].kind == "select"
    assert controls["txtBZFS"].value == "01"
    assert controls["txtBZFS"].options["纸箱包装"] == "01"
    assert controls["txtCPMS"].kind == "textarea"
    assert controls["txtCPMS"].value == "四色"
    assert controls["chkBSYTB"].kind == "checkbox"
    assert controls["txtHL"].css_hidden is True
    assert controls["txtCPSL"].css_hidden is False
