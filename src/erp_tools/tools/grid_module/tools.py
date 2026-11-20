"""按模块拆出的具体工具类（CLI 名 / MCP 名一一对应）。

CLI 用中划线（`erp production-list`），MCP 用下划线（`erp_production_list`），
两边都从这些类派生，避免重复声明名称。
"""

from __future__ import annotations

from .spec import COSTING, PRODUCTION, PURCHASE, SALES_ORDER
from .tool import ModuleDetailTool, ModuleListTool, ModuleNodeTool


class ProductionListTool(ModuleListTool):
    name = "production-list"
    summary = "查询生产施工单列表（ShengChan/SC_SCSGD/Index.aspx）→ 表格 / CSV / JSONL"
    spec = PRODUCTION


class ProductionDetailTool(ModuleDetailTool):
    name = "production-detail"
    summary = "生产施工单【详情】（SC_SCSGD/Add.aspx，只读）→ 字段 + 部件表 + 送货表 + 子页清单"
    spec = PRODUCTION


class ProductionNodeTool(ModuleNodeTool):
    name = "production-node"
    summary = "生产施工单子数据（产品/部件/工序/辅料/机台/送货/D10/开版…）→ 字段 + 网格"
    spec = PRODUCTION


class PurchaseListTool(ModuleListTool):
    name = "purchase-list"
    summary = "查询采购订单列表（CaiGou/CG_CGDD/Index.aspx）→ 表格 / CSV / JSONL"
    spec = PURCHASE


class PurchaseDetailTool(ModuleDetailTool):
    name = "purchase-detail"
    summary = "采购订单【详情】（CG_CGDD/Add.aspx，只读）→ 表头字段 + 材料明细网格"
    spec = PURCHASE


class PurchaseNodeTool(ModuleNodeTool):
    name = "purchase-node"
    summary = "采购订单子页面（供应商档案选择等弹窗）→ 字段 + 网格"
    spec = PURCHASE


class CostingListTool(ModuleListTool):
    name = "costing-list"
    summary = ("查询产品核价列表（BaoJia/BJ_KHBJ/Index.aspx，QXID=1281；一行=一个核价产品行）"
               "→ 表格 / CSV / JSONL；详情用 price-detail / price-node")
    spec = COSTING


class SalesOrderListTool(ModuleListTool):
    name = "sales-order-list"
    summary = "查询客户订单列表（DingDan/DD_KHDD/Index.aspx，QXID=687）→ 表格 / CSV / JSONL"
    spec = SALES_ORDER


class SalesOrderDetailTool(ModuleDetailTool):
    name = "sales-order-detail"
    summary = "客户订单【详情】（DD_KHDD/Add.aspx，只读）→ 字段 + 选配/部件/送货网格 + 子页清单"
    spec = SALES_ORDER


class SalesOrderNodeTool(ModuleNodeTool):
    name = "sales-order-node"
    summary = "客户订单子数据（产品/部件/工序/辅料/数量档/费项/反馈/开版…）→ 字段 + 网格"
    spec = SALES_ORDER


__all__ = [
    "ProductionListTool",
    "ProductionDetailTool",
    "ProductionNodeTool",
    "PurchaseListTool",
    "PurchaseDetailTool",
    "PurchaseNodeTool",
    "CostingListTool",
    "SalesOrderListTool",
    "SalesOrderDetailTool",
    "SalesOrderNodeTool",
]
