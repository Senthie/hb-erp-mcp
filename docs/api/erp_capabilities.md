# `erp_capabilities` — 当前账号能访问哪些页面

> 菜单：—（基础设施，不挂在菜单上）
> 页面：`GET /Admin/indexAjax.ashx?FunName=GetMenuTree`｜CLI：`erp qxid`｜**只读**

## 用途

检查当前账号能访问哪些页面。返回两段：

* `pages` / `missing_pages`：**基础模块**（报价需求列表/详情、客户报价列表/详情、考勤）的菜单 QXID；
* `modules` / `module_missing`：**产品核价 / 生产施工单 / 采购订单** 三个模块的
  **实测** QXID 与可用性（菜单里能否找到该列表页）。

`missing_pages` 非空 = 当前账号没有该页面权限，调对应工具会失败。

## 参数

无。

## 返回

```json
{
  "username": "15627",
  "pages": { "quote_index": "876", "quote": "873",
             "price_index": "860", "price": "857", "attendance": "2553" },
  "missing_pages": [],
  "modules": {
    "costing":    {"label": "产品核价",   "list_qxid": "1281", "list_available": true,
                   "detail_path": "BaoJia/BJ_KHBJ/Add.aspx", "detail_reached_from": "list_row"},
    "production": {"label": "生产施工单", "list_qxid": "740",  "list_available": true,
                   "detail_path": "ShengChan/SC_SCSGD/Add.aspx", "detail_reached_from": "list_row"},
    "purchase":   {"label": "采购订单",   "list_qxid": "266",  "list_available": true,
                   "detail_path": "CaiGou/CG_CGDD/Add.aspx", "detail_reached_from": "list_row"}
  },
  "module_missing": [],
  "note": "modules 里的 QXID 是实测值：同一 aspx 常挂在多个菜单下…"
}
```

## 注意

- **`pages` 是菜单探测值，`modules` 是实测值**，两者口径不同，别混用：
  同一 aspx 会挂在多个菜单下（`BJ_KHBJ/Index.aspx` = 客户报价 860 + 产品核价 1281；
  `SC_SCSGD/Index.aspx` = 740 + 983），`parse_menu_urls` 用 `setdefault` 只留第一个。
  拿 860/983 去查会打开**别的菜单**的页面（症状：核价列表只有 56 列、施工单单号变 `ZBSQ…`）。
- 采购订单**没有新增/详情菜单**（详情从列表行双击进入 `Add.aspx`），
  所以 `detail_reached_from=list_row`，可用性跟列表页一致。
- 菜单接口**必须用 GET**，POST 返回空体。
- 完整菜单树见 [erp-menu.md](../erp-menu.md)。
