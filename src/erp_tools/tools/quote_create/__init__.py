"""「新增客户报价需求」页面抽象（读 + 写）。

`Add.aspx`（表头）与 `Add_CPZL.aspx`（产品明细子页）都有完整抽象：
读表单/下拉/客户/产品档案/字段清单，写新增、改表头、改产品行、删除。

明细字段**全量**映射见 `fields.py`（覆盖子页 205 个控件），
`erp_quote_product_form` 会把它连同真实页面的当前值与下拉选项一起返回。
"""

from .fields import (
    ALIAS_MAP,
    CONTROL_MAP,
    PRODUCT_FIELDS,
    REQUIRED_ALIASES,
    REQUIRED_PRODUCT_FIELDS,
    STATE_FIELDS,
    UPLOAD_ALIASES,
    UPLOAD_CONTROLS,
    VISIBLE_CONTROLS,
    WRITABLE_CONTROLS,
    FieldSpec,
    alias_of,
    grouped,
    resolve_control,
    suggest,
    upload_payload_schema,
)
from .parser import (
    CUSTOMER_FIELDS,
    CustomerOption,
    HEADER_FIELDS,
    NewQuoteForm,
    ProductControl,
    SELECT_FIELDS,
    parse_customer_rows,
    parse_detail_rows,
    parse_new_form,
    parse_product_controls,
    parse_rows_count,
)
from .service import (
    CUSTOMER_PICKER_PATH,
    CUSTOMER_TO_HEADER,
    PAGE_PATH,
    REQUIRED_INPUTS,
    SELECT_LABELS,
    SUB_FIELD_MAP,
    QuoteCreateService,
    clear_options_cache,
    normalize_product_fields,
)

__all__ = [
    "ALIAS_MAP", "CONTROL_MAP", "CUSTOMER_FIELDS", "CUSTOMER_PICKER_PATH",
    "CUSTOMER_TO_HEADER", "CustomerOption", "FieldSpec", "HEADER_FIELDS",
    "NewQuoteForm", "PAGE_PATH", "PRODUCT_FIELDS", "ProductControl",
    "QuoteCreateService", "REQUIRED_ALIASES", "REQUIRED_INPUTS",
    "REQUIRED_PRODUCT_FIELDS", "SELECT_FIELDS", "SELECT_LABELS",
    "STATE_FIELDS", "SUB_FIELD_MAP", "UPLOAD_ALIASES", "UPLOAD_CONTROLS",
    "VISIBLE_CONTROLS", "WRITABLE_CONTROLS", "alias_of", "clear_options_cache",
    "grouped", "normalize_product_fields", "parse_customer_rows",
    "parse_detail_rows", "parse_new_form", "parse_product_controls",
    "parse_rows_count", "resolve_control", "suggest", "upload_payload_schema",
]
