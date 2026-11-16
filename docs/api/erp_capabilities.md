# `erp_capabilities` — 当前账号能访问哪些页面

> 菜单：—（基础设施，不挂在菜单上）
> 页面：`GET /Admin/indexAjax.ashx?FunName=GetMenuTree`｜CLI：`erp qxid`｜**只读**

## 用途

检查 5 个关键页面（报价需求列表/详情、客户报价列表/详情、考勤）的 QXID
能否从菜单里探测到。`missing_pages` 非空 = 当前账号没有该页面权限，
调对应工具会失败。

## 参数

无。

## 返回

```json
{
  "username": "21146",
  "pages": { "quote_index": "876", "quote": "873",
             "price_index": "860", "price": "857", "attendance": "2553" },
  "missing_pages": [],
  "note": "missing_pages 非空通常意味着当前账号没有该页面权限……"
}
```

## 注意

- QXID 是**按账号权限**探测出来的，换个人就不同 —— 所以代码里不写死。
- 菜单接口**必须用 GET**，POST 返回空体。
- 注释：同一路径可能挂在多个菜单下（`KC_CLCRKSQ` 既是采购申请 1036 又是部门领料申请 331），
  本接口的映射是「首个生效」口径；完整菜单树见 [erp-menu.md](../erp-menu.md)。
