# `erp_quote_options` — 新增页的下拉选项

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求 › 新增-客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/Add.aspx`（QXID 873）｜CLI：—｜**只读**

## 用途

列出当前账号在「新增客户报价需求」页能选的：业务员 `sales`、跟单员 `merchandiser`、
单价体系 `price_system`。每项给 `name`（显示名）与 `id`（提交用 GUID）。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `field` | str | 全部 | 只看某一组：`sales` / `merchandiser` / `price_system`（也接受中文名） |
| `refresh` | bool | `false` | `true` 跳过 5 分钟缓存，重新向 ERP 读取 |

## 返回

```json
{
  "sales": [{ "name": "陈小美", "id": "…GUID…" }],
  "merchandiser": [{ "name": "肖也", "id": "…" }],
  "price_system": [{ "name": "IV级(NGV)", "id": "…" }],
  "employees": [{ "name": "陈小美", "sales": true, "merchandiser": false }],
  "cached_at": "2026-11-16 10:00:00"
}
```

## 注意

- 默认缓存 5 分钟（权限变动后靠 `refresh=true` 刷新）。
- 选项来自页面实际渲染的 `select`，不同账号看到的人不一样，别把结果当全局常量。
- `employees` 是把三组名单合并去重后的视图，方便判断某人能否当业务员/跟单员。
