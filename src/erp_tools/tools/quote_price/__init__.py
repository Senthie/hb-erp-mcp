"""客户报价列表 / 详情查询工具（BaoJia/BJ_KHBJ）。"""

from .detail_parser import PriceDetail, parse_price_detail
from .node_parser import (
    parse_fees_node,
    parse_materials_node,
    parse_part_node,
    parse_quantities_node,
)
from .parser import ADD_PAGE_PATH, PAGE_PATH, PriceListPage, parse_price_list_page
from .tool import (
    DATE_FIELDS,
    PriceDetailTool,
    PriceListTool,
    PriceNodeTool,
    default_date_range,
)

__all__ = [
    "PriceListTool",
    "PriceDetailTool",
    "PriceNodeTool",
    "PAGE_PATH",
    "ADD_PAGE_PATH",
    "PriceListPage",
    "parse_price_list_page",
    "PriceDetail",
    "parse_price_detail",
    "parse_quantities_node",
    "parse_part_node",
    "parse_materials_node",
    "parse_fees_node",
    "DATE_FIELDS",
    "default_date_range",
]
