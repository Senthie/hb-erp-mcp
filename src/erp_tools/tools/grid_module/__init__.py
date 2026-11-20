"""模块化列表页（生产施工单 / 采购订单 / 产品核价）。

这三个页面和报价需求页是同一套 WebForms 模板：

  * 结果表在 `div#gViewDiv > table#gridView`，第一行是 `tr.FixedTitleRow` 表头；
  * 行主记录在 `ondblclick="OnDBClickOpenData('<ID>','<QXID>',3)"`；
  * 查询参数固定是 `txtKSRQ/txtJZRQ` + `txtKey/DropField` + `txtKey1/DropField1`；
  * 服务端过滤靠若干 `<select>`；
  * 详情页 `Add.aspx` 用 `Option=3` 只读打开，值在「标签格 → 右邻格」里；
  * 详情页的「子数据」是弹窗子页：页面 JS 里每个 `Open*` 函数都是
    「写几个 `lblSelect*` 隐藏字段 → 点击某个 `btnSelect*`」，响应里的
    `showModalDialog('xxx.aspx?...')` 才是真正的子页 URL。

差别只在「页面路径 + QXID + 过滤下拉 + 子页清单」，所以这里用 `ModuleSpec`
参数化，解析逻辑共享。页面特有的语义（列名、子页别名）留在 spec 里。
"""

from __future__ import annotations

from .spec import MODULES, ModuleSpec, get_module, module_names
from .tool import ModuleDetailTool, ModuleListTool, ModuleNodeTool
from .tools import (
    CostingListTool,
    ProductionDetailTool,
    ProductionListTool,
    ProductionNodeTool,
    PurchaseDetailTool,
    PurchaseListTool,
    PurchaseNodeTool,
    SalesOrderAttachmentTool,
    SalesOrderDetailTool,
    SalesOrderListTool,
    SalesOrderNodeTool,
)

__all__ = [
    "MODULES",
    "ModuleSpec",
    "get_module",
    "module_names",
    "ModuleListTool",
    "ModuleDetailTool",
    "ModuleNodeTool",
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
    "SalesOrderAttachmentTool",
]
