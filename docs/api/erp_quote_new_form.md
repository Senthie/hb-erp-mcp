# `erp_quote_new_form` — 新增报价需求页的字段结构

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求 › 新增-客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/Add.aspx`（QXID 873）｜CLI：—｜**只读**

## 用途

新建单据前先看「能填什么」：表头字段及其只读标记、三个下拉的
「名称 → GUID」映射、textarea 列表。用来确定 `erp_quote_create` 该传什么、业务员怎么传。

## 参数

无。

## 返回

```json
{
  "header": [{ "control": "txtKHMC", "label": "客户名称", "readonly": false }],
  "selects": {
    "sales": [{ "name": "陈小美", "id": "…GUID…" }],
    "merchandiser": [{ "name": "肖也", "id": "…" }],
    "price_system": [{ "name": "IV级(NGV)", "id": "…" }]
  },
  "textareas": ["txtRemark", "txtCPMX"]
}
```

## 注意

- 只读探测，不落库。
- 三个下拉的选项**按账号权限实时读取**；需要最新值先调 `erp_quote_options(refresh=true)`。
- 产品明细子页的字段是另一份结构，见 `erp_quote_product_form`。
