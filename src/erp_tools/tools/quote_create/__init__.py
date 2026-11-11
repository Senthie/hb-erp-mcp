"""「新增客户报价需求」页面抽象（只读）。

当前只提供 **打开空白页 / 检索客户 / 组装并校验提交体** 三件事；
保存（btnSave）因明细行生成协议未验证而暂未开放，见 `service.py` 顶部说明。
"""

from .parser import (
    CUSTOMER_FIELDS,
    CustomerOption,
    HEADER_FIELDS,
    NewQuoteForm,
    SELECT_FIELDS,
    parse_customer_rows,
    parse_detail_rows,
    parse_new_form,
    parse_rows_count,
)
from .service import (
    CUSTOMER_PICKER_PATH,
    CUSTOMER_TO_HEADER,
    PAGE_PATH,
    REQUIRED_INPUTS,
    SELECT_LABELS,
    QuoteCreateService,
    clear_options_cache,
)

__all__ = [
    "CUSTOMER_FIELDS", "CUSTOMER_PICKER_PATH", "CUSTOMER_TO_HEADER", "CustomerOption",
    "HEADER_FIELDS", "NewQuoteForm", "PAGE_PATH", "QuoteCreateService",
    "REQUIRED_INPUTS", "SELECT_FIELDS", "SELECT_LABELS", "clear_options_cache",
    "parse_customer_rows", "parse_new_form", "parse_rows_count",
]
