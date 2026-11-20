# `erp_quote_update` — 修改报价需求表头（**会写入 ERP**）

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求 › 新增-客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/Add.aspx`｜CLI：—｜**写**

## 用途

改单据**表头**：备注 / 联系人 / 电话 / 折扣率 / 产品描述（`txtCPMX`）/ 业务员 / 跟单员。
内部先回发 `btnModify` 解锁 [保存]，再回发 `btnSave`。

⚠ **改不了明细行**。明细的产品描述（CPMS）请用
[`erp_quote_update_product`](erp_quote_update_product.md) 的 `product_desc`；
明细的名称/规格/数量也在那个接口里。

## 参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `record_id` | str | 报价需求记录 ID（必填） |
| `remark` | str | 备注 |
| `contact` / `phone` | str | 联系人 / 联系电话 |
| `mobile` / `fax` | str | ⚠ 实测该 ERP 不落库 |
| `discount` | str | 折扣率如 `0.45` |
| `product_memo` | str | 产品描述（`txtCPMX`） |
| `sales` / `merchandiser` | str | 业务员 / 跟单员（姓名或 GUID） |
| `confirm` | bool | `false`（默认）只返回将要修改的字段；`true` 才写库 |

**不传的参数 = 不修改**，不会被清空。

## 返回

```json
{ "status": "preview", "changes": { "remark": "…" }, "needs_confirmation": true }
```

## 注意

- `confirm=false` 时**完全不发包**，只回显将改的字段。
- 保存成功也可能残留旧的 `$.ligerDialog.error` 提示，判成败以**回读**为准。
- 若单据已在其它会话被改过，前端 `__VIEWSTATE` 会失效 → 报错后重开一次即可。
