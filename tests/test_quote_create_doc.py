"""文档与实现的一致性守卫：`docs/erp-quote-create.md` 里的示例载荷必须真的能被接受。

文档最容易过期的是「示例 JSON」——字段名写错、枚举值写错，复制过去直接报错。
这里直接把文档里的代码块解析出来对着 `upload_payload_schema()` 校验。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from erp_tools.tools.quote_create import (
    CONTROL_MAP,
    PRODUCT_FIELDS,
    REQUIRED_ALIASES,
    RESERVED_ITEM_KEYS,
    UPLOAD_ALIASES,
    upload_payload_schema,
)

DOC = Path(__file__).resolve().parents[1] / "docs" / "erp-quote-create.md"


def _example_payload() -> dict:
    text = DOC.read_text(encoding="utf-8")
    match = re.search(r"## 2\. 完整 JSON 示例\s*```json\n(.*?)```", text, re.S)
    assert match, "文档里找不到『## 2. 完整 JSON 示例』的 json 代码块"
    return json.loads(match.group(1))


def test_doc_example_uses_only_uploadable_fields() -> None:
    payload = _example_payload()
    schema = upload_payload_schema()["json_schema"]
    allowed = schema["properties"]["fields"]["properties"]

    assert payload["products"], "示例里必须至少有一条明细"
    for item in payload["products"]:
        for key in item.get("fields") or {}:
            assert key in allowed, f"文档示例里的 {key!r} 不是可上传字段"
        # 平铺写法也要合法（老写法兼容）；name/spec/qty/fields/product_category 是保留键
        for key in item:
            if key in RESERVED_ITEM_KEYS:
                continue
            assert key in UPLOAD_ALIASES, f"文档示例里平铺的 {key!r} 不是可上传字段"


def test_doc_example_enum_values_are_valid() -> None:
    payload = _example_payload()
    props = upload_payload_schema()["json_schema"]["properties"]["fields"]["properties"]

    for item in payload["products"]:
        for key, value in (item.get("fields") or {}).items():
            enum = props.get(key, {}).get("enum")
            if enum:
                assert value in enum, f"{key}={value!r} 不在可选值里：{enum}"


def test_doc_example_header_params_exist_in_create() -> None:
    """表头参数写错（比如已经删掉的 customer_order_no）也会静默失效。"""
    import inspect

    from erp_tools.tools.quote_create import QuoteCreateService

    params = set(inspect.signature(QuoteCreateService.create).parameters)
    for key in _example_payload():
        assert key in params, f"文档示例里的表头参数 {key!r} 不在 create() 签名里"


def test_doc_no_longer_mentions_removed_header_field() -> None:
    text = DOC.read_text(encoding="utf-8")
    # 客户单号已改走明细级 txtKHCPMC；表头页没有该控件
    assert "customer_order_no`（`txtKHDDH`" not in text
    assert "txtGG`）或 `length_mm" not in text
    assert CONTROL_MAP["txtKHCPMC"].visible is True


def test_doc_lists_every_uploadable_field_in_the_table() -> None:
    """第 3.3 节的表格必须逐个列出 51 个可上传字段（MCP 名 / ERP 控件名 / 中文 / 备注）。"""
    text = DOC.read_text(encoding="utf-8")
    section = text.split("### 3.3 `fields` 里的 51 个明细字段", 1)[1].split("\n### 3.4", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| `")]

    assert len(rows) == 51, f"3.3 表格应有 51 行，实际 {len(rows)}"
    listed = {row.split("`")[1] for row in rows}
    uploadable = {spec.alias for spec in PRODUCT_FIELDS if spec.uploadable}
    assert listed == uploadable

    # 每行四列：MCP 名 / ERP 控件名 / 中文含义 / 备注
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert len(cells) == 4, f"列数不对：{row}"
        control = cells[1].strip("`")
        assert control in CONTROL_MAP, f"ERP 控件名 {control!r} 不在注册表里"
        assert cells[2], f"中文含义不能为空：{row}"


def test_doc_header_table_covers_all_create_params() -> None:
    """3.1 节要把 `create()` 的每个参数都写到。"""
    import inspect

    from erp_tools.tools.quote_create import QuoteCreateService

    text = DOC.read_text(encoding="utf-8")
    section = text.split("### 3.1 外层 / 表头参数", 1)[1].split("\n### ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| `")]
    listed = {row.split("`")[1] for row in rows}

    params = set(inspect.signature(QuoteCreateService.create).parameters) - {
        "self", "products", "confirm",
    }
    assert params <= listed, f"3.1 漏了参数：{sorted(params - listed)}"


def test_doc_marks_exactly_the_required_fields() -> None:
    """3.3 表格里带 ✅必填 的行，必须恰好等于 `REQUIRED_PRODUCT_FIELDS`。"""
    text = DOC.read_text(encoding="utf-8")
    section = text.split("### 3.3 `fields` 里的 51 个明细字段", 1)[1].split("\n### 3.4", 1)[0]

    marked = {
        line.split("`")[1]
        for line in section.splitlines()
        if line.startswith("| `") and "✅ **必填**" in line
    }
    assert marked == set(REQUIRED_ALIASES), (
        f"文档多标了 {sorted(marked - set(REQUIRED_ALIASES))}，"
        f"漏标了 {sorted(set(REQUIRED_ALIASES) - marked)}"
    )
    assert len(marked) == 14


def test_doc_required_section_lists_all_of_them() -> None:
    """3.4 的 ERP 必填表也要列全，且顺序与实测必填链一致。"""
    text = DOC.read_text(encoding="utf-8")
    section = text.split("**② ERP 服务端必填（14 个）**", 1)[1].split("\n链上还有", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ")]
    rows = [r for r in rows if r.split("|")[1].strip().isdigit()]

    assert len(rows) == 14
    listed = [r.split("|")[2].strip().strip("`") for r in rows]
    assert listed == [
        "binding", "cases", "languages", "packing", "pallet", "delivery",
        "delivery_date", "parts_desc", "attribute", "product_type",
        "expected_date", "product_desc", "modify_log", "inspection",
    ]


def test_sample_payload_fills_all_uploadable_fields() -> None:
    """`docs/samples/quote-create-51-fields.json` 必须真的覆盖 51 个字段。"""
    sample = Path(__file__).resolve().parents[1] / "docs" / "samples" / "quote-create-51-fields.json"
    data = json.loads(sample.read_text(encoding="utf-8"))
    fields = data["products"][0]["fields"]
    uploadable = {spec.alias for spec in PRODUCT_FIELDS if spec.uploadable}

    assert len(fields) == 51
    assert set(fields) == uploadable


def test_sample_quantity_is_self_consistent() -> None:
    """`qty` 会写 txtCPSL，`qty_1` 会写 txtCPSL1，两者不同就会「数量描述」对不上。"""
    sample = Path(__file__).resolve().parents[1] / "docs" / "samples" / "quote-create-51-fields.json"
    item = json.loads(sample.read_text(encoding="utf-8"))["products"][0]
    qty_1 = item["fields"].get("qty_1")

    assert qty_1 == item["qty"], "qty 必须等于 qty_1（第 1 档）"
    # 档位之间应该是递增的（报价常见 3000/5000/7000）
    tiers = [item["fields"].get(f"qty_{i}") or 0 for i in range(1, 11)]
    filled = [t for t in tiers if t]
    assert filled == sorted(filled), f"数量档位必须递增：{filled}"
