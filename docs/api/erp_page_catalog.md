# `erp_page_catalog` — 四个模块的「数据页面清单」

> 菜单：**业务报价管理 › 产品核价**、**生产管理 › 生产施工单**、**采购管理 › 采购订单**、
> **客户订单管理 › 客户订单**
> CLI：`erp pages [--module costing|production|purchase|sales_order]`｜**只读（不访问 ERP）**

## 用途

回答「这些模块一共有哪些数据页面、各自怎么打开」。返回的是**代码里的实测清单**，
不是猜的：

| 模块 | 列表页 | 详情页 | 弹窗子页 |
| --- | --- | --- | --- |
| 产品核价 `costing` | `BaoJia/BJ_KHBJ/Index.aspx`（QXID **1281**） | `BaoJia/BJ_KHBJ/Add.aspx` | 复用客户报价的 22 个节点 → `erp_price_node` |
| 生产施工单 `production` | `ShengChan/SC_SCSGD/Index.aspx`（QXID **740**） | `ShengChan/SC_SCSGD/Add.aspx` | **18 个** |
| 采购订单 `purchase` | `CaiGou/CG_CGDD/Index.aspx`（QXID **266**） | `CaiGou/CG_CGDD/Add.aspx` | **无数据子页**（明细在详情页内联网格 `gridView2`） |
| 客户订单 `sales_order` | `DingDan/DD_KHDD/Index.aspx`（QXID **687**） | `DingDan/DD_KHDD/Add.aspx` | **20 个** → `erp_sales_order_node`；附件 → `erp_sales_order_attachment` |

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `module` | str | `""` | `costing` / `production` / `purchase` / `sales_order`；留空返回全部 |

## 返回

```json
{
  "modules": [{
    "module": "production",
    "label": "生产施工单",
    "row_note": "一行 = 一张生产施工单（生产单号 SGD 开头）",
    "pages": {
      "list":   {"path": "ShengChan/SC_SCSGD/Index.aspx", "qxid": "740", "mcp": "erp_production_list"},
      "detail": {"path": "ShengChan/SC_SCSGD/Add.aspx", "mcp": "erp_production_detail"}
    },
    "filters": {"txtSCZT": "工单状态", "txtCheck": "审批情况"},
    "date_fields": {"制单日期": "0", "交货日期": "1", "审批日期": "2"},
    "nodes": [{"node": "part_materials", "page": "Add_BJFL.aspx",
               "button": "btnSelectBJFL", "label": "部件已选辅料",
               "mcp": "erp_production_node"}]
  }],
  "count": 4
}
```

## 注意

- **同一路径可能挂在多个菜单下**：`BJ_KHBJ/Index.aspx` 既是「列表-客户报价」（QXID 860）
  又是「列表-产品核价」（QXID 1281）；`SC_SCSGD/Index.aspx` 也有 740 / 983 两个菜单。
  菜单探测用 `setdefault` 只留第一个，所以这四个模块的 QXID **以实测值为准**，
  可用 `ERP_QXID_COST_INDEX` / `ERP_QXID_PRODUCTION_INDEX` / `ERP_QXID_PURCHASE_INDEX` /
  `ERP_QXID_SALES_ORDER_INDEX` 覆盖。
- 采购订单的「子页」只有选择弹窗（供应商档案等），没有可采集的数据页。
- 客户订单与产品核价/客户报价的子页文件名高度相似（`Add_CPZL.aspx` 等），
  但按钮集合不同，以 `nodes` 里的实测清单为准。
