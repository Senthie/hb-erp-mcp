"""MCP 工具的入参模型（pydantic）。

MCP 的 `inputSchema` 是**由函数注解推导**的，所以「参数格式 / 类型 / 示例」只能
通过 pydantic 声明来表达。这里的字段清单**不手写**：全部由
`erp_tools.tools.quote_create.fields` 的注册表派生，和
`upload_payload_schema()`、服务端校验共用同一份真源，避免三处描述漂移。

两条设计取舍：

1. **下拉用 `examples` 而不是 `Literal`**。注册表里的 `options` 是「离线已知选项」，
   ERP 侧可能还有别的合法值；写死成枚举会在客户端就把真实可用的值判死。
2. **`extra="allow"`**。折叠面板里的隐藏字段、只读字段、「最接近的字段」提示
   都由服务端的 `normalize_product_fields()` 负责，那里能给出中文原因和拼写建议，
   比 pydantic 的 `Extra inputs are not permitted` 更有用。
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from ...tools.quote_create import fields as pf

#: 单个字段最多展示几个下拉示例（多一个示例就多一份常驻上下文开销）
MAX_EXAMPLES = 6

#: JSON Schema 类型 → Python 类型（其余一律按字符串处理）
_JSON_TYPE_TO_PY: dict[str, type] = {
    "boolean": bool,
    "number": float,
}


def _annotation(prop: dict[str, Any]) -> Any:
    """把 `fields.schema_property()` 的输出转成 pydantic 注解。"""
    py = _JSON_TYPE_TO_PY.get(str(prop.get("type")), str)
    examples = prop.get("examples")
    if examples is None and prop.get("enum"):
        examples = list(prop["enum"])
    if examples:
        examples = list(examples)[:MAX_EXAMPLES]
    return Annotated[
        py | None,
        Field(
            default=None,
            title=prop.get("title"),
            description=prop.get("description"),
            examples=examples,
        ),
    ]


def _build_fields_model() -> type[BaseModel]:
    """按注册表生成 `QuoteProductFields`（51 个可见可写字段）。"""
    annotations: dict[str, Any] = {}
    for spec in pf.PRODUCT_FIELDS:
        if not spec.uploadable:
            continue
        prop = dict(pf.schema_property(spec))
        if spec.required:
            note = prop.get("description")
            prop["description"] = f"ERP 必填。{note}" if note else "ERP 必填"
        annotations[spec.alias or spec.control] = _annotation(prop)

    namespace: dict[str, Any] = {
        "__annotations__": annotations,
        "model_config": ConfigDict(extra="allow", title="产品明细字段"),
    }
    namespace.update(dict.fromkeys(annotations, None))
    return type("QuoteProductFields", (BaseModel,), namespace)


#: 明细子页里 **DOM 首屏可见且可写** 的 51 个字段。
#: 隐藏字段（折叠面板）与只读字段不在这里，传了会被服务端拒绝。
QuoteProductFields = _build_fields_model()


class QuoteProductInput(BaseModel):
    """`products[]` 里的一条明细：产品标识 + 明细字段。"""

    model_config = ConfigDict(extra="allow", title="产品明细")

    name: Annotated[
        str,
        Field(
            description=(
                "产品名称。先按名称在【产品档案】里模糊检索，必须唯一匹配；"
                "搜不到就当作新产品，此时必须同时给 product_category"
            ),
            examples=["512961503"],
        ),
    ]
    spec: Annotated[
        str,
        Field(
            description="规格，形如 210*148；只做非空校验，真正落库的规格仍取产品档案",
            examples=["210*148"],
        ),
    ]
    qty: Annotated[
        float,
        Field(gt=0, description="产品数量，必须 > 0；写入 txtCPSL 与首屏的 txtCPSL1",
              examples=[3000]),
    ]
    product_category: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "产品类别，形如 `27.01;普通BB书`（也接受 `27.01` 或 `普通BB书`）。"
                "产品档案搜不到时必填；档案能命中时可省略"
            ),
            examples=["27.01;普通BB书", "04.01;STD彩盒"],
        ),
    ] = None
    fields: Annotated[
        QuoteProductFields | None,
        Field(
            default=None,
            description=(
                "明细字段，键名用本对象的字段名，或直接用页面控件名（如 txtZDFS）；"
                "折叠面板里的 142 个隐藏字段与只读字段会被拒绝。完整清单见 "
                "erp_quote_product_form"
            ),
        ),
    ] = None


__all__ = ["MAX_EXAMPLES", "QuoteProductFields", "QuoteProductInput"]
