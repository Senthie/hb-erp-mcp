"""MCP 入参模型（pydantic）的守卫测试。

`inputSchema` 是模型唯一能看到的接口说明，这里锁住三件事：

1. **单一真源**：字段清单来自 `quote_create/fields.py` 注册表，不能手写、不能漂移；
2. **下拉只做提示**：写成 `examples` 而不是 `enum`（ERP 可能有别的合法值）；
3. **老写法不回归**：字段平铺在 `products[0]` 上仍然有效。
"""

from __future__ import annotations

from erp_tools.apps.mcp.schemas import MAX_EXAMPLES, QuoteProductFields, QuoteProductInput
from erp_tools.tools.quote_create import fields as pf
from erp_tools.tools.quote_create.service import normalize_product_fields


def _product_schema() -> dict:
    return QuoteProductInput.model_json_schema(by_alias=True)


def _fields_props() -> dict:
    return _product_schema()["$defs"]["QuoteProductFields"]["properties"]


def test_fields_model_covers_every_uploadable_alias() -> None:
    props = set(_fields_props())
    expected = {s.alias or s.control for s in pf.PRODUCT_FIELDS if s.uploadable}
    assert props == expected, "明细字段模型与注册表漂移了"
    assert len(props) == 51
    assert props <= set(pf.UPLOAD_ALIASES)


def test_product_requires_name_spec_qty() -> None:
    assert _product_schema()["required"] == ["name", "spec", "qty"]


def test_dropdown_options_become_examples_not_enum() -> None:
    """下拉只做提示：写死成 enum 会把 ERP 实际接受的值在客户端判死。"""
    binding = _fields_props()["binding"]
    assert "enum" not in binding
    assert binding["title"] == "装订方式"
    assert "胶装/PAD书" in binding["examples"]
    assert len(binding["examples"]) <= MAX_EXAMPLES


def test_field_kinds_map_to_json_types() -> None:
    props = _fields_props()
    assert props["no_pallet"]["anyOf"][0]["type"] == "boolean"
    assert props["languages"]["anyOf"][0]["type"] == "number"
    assert props["packing"]["anyOf"][0]["type"] == "string"


def test_erp_required_fields_are_labelled() -> None:
    props = _fields_props()
    labeled = 0
    for spec in pf.PRODUCT_FIELDS:
        if not (spec.uploadable and spec.required):
            continue
        key = spec.alias or spec.control
        assert "ERP 必填" in (props[key].get("description") or ""), key
        labeled += 1
    assert labeled == 14


def test_flat_spelling_still_works() -> None:
    """老写法（字段平铺在 products[0] 上）必须继续有效。"""
    item = QuoteProductInput.model_validate(
        {"name": "A", "spec": "1*1", "qty": 10, "txtBZFS": "纸箱包装"}
    )
    dumped = item.model_dump(exclude_none=True)
    assert dumped["txtBZFS"] == "纸箱包装"
    assert "fields" not in dumped


def test_model_dump_feeds_service_normalizer() -> None:
    """工具层只做 model_dump，服务端的字段归一化逻辑不用改。"""
    item = QuoteProductInput.model_validate(
        {"name": "A", "spec": "1*1", "qty": "10", "fields": {"packing": "纸箱包装"}}
    )
    fields, errors, _warnings = normalize_product_fields(item.model_dump(exclude_none=True))
    assert errors == []
    assert fields["txtBZFS"] == "纸箱包装"


def test_hidden_field_is_rejected_after_model() -> None:
    """模型层 extra=allow，隐藏字段仍由服务端硬拒绕（不能只看 schema）。"""
    hidden = next(s for s in pf.PRODUCT_FIELDS if not s.visible and s.uploadable is False)
    item = QuoteProductInput.model_validate(
        {"name": "A", "spec": "1*1", "qty": 1, "fields": {hidden.control: "折叠面板"}}
    )
    _fields, errors, _warnings = normalize_product_fields(item.model_dump(exclude_none=True))
    assert errors and "不可见" in errors[0]


def test_unknown_alias_gets_suggestion() -> None:
    item = QuoteProductInput.model_validate(
        {"name": "A", "spec": "1*1", "qty": 1, "fields": {"bindin": "胶装/PAD书"}}
    )
    _fields, errors, _warnings = normalize_product_fields(item.model_dump(exclude_none=True))
    assert errors and "相近字段" in errors[0]


def test_fields_model_allows_unlisted_keys() -> None:
    """未声明的键必须还能传：隐藏字段的拒绝理由由服务端给中文说明。"""
    assert QuoteProductFields.model_config.get("extra") == "allow"
    assert QuoteProductInput.model_config.get("extra") == "allow"
