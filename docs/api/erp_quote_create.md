# `erp_quote_create` — 新建客户报价需求（**会写入 ERP**）

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求 › 新增-客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/Add.aspx` + `Add_CPZL.aspx`｜CLI：—｜**写**

## 用途

新建一条报价需求：主表 + 1 条产品明细（含产品数量）。

`confirm=false`（默认）只校验并返回预览，**不落库**；`confirm=true` 才真正保存。

## 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `products` | list | ✅ | 产品明细，当前**只支持 1 条**；每项需 `name` / `spec` / `qty`，明细字段放 `fields`（也可平铺） |
| `customer` | str | — | 客户名称片段或客户 ID（先 `erp_quote_customer_search`） |
| `sales` | str | — | 业务员姓名或 GUID；为空取客户默认业务员 |
| `merchandiser` | str | — | 跟单员姓名或 GUID |
| `price_system` | str | — | 单价体系名称或 GUID |
| `contact` / `phone` | str | — | 联系人 / 联系电话 |
| `mobile` / `fax` | str | — | ⚠ 实测该 ERP **不落库**，提交后回读为空 |
| `discount` | str | — | 折扣率如 `0.45`；为空取客户默认 |
| `remark` | str | — | 备注（表头 `txtRemark`） |
| `product_memo` | str | — | 产品描述（表头 `txtCPMX`） |
| `attachments` | list | — | 附件，**建单成功后自动上传**；每项 `{filename, content_base64}` 或 `{filename, content_text}`，可选 `name`/`desc`/`remark`。详见 [erp_attachment.md](erp_attachment.md) |
| `confirm` | bool | — | `false` 只预览；`true` 才保存 |

## 返回

```json
{
  "status": "created",
  "ok": true,
  "errors": [],
  "warnings": [],
  "bdbh": "BJXQ261112001",
  "record_id": "…GUID…",
  "readback": { "明细CPMC": "…", "产品数量": "3000" }
}
```

`status` 取值：`needs_confirmation`（预览）/ `created` / `partial`（写了但回读不符）/ `failed`。

传了 `attachments` 时：

```json
{
  "status": "created",
  "record_id": "0c3e32d9-…",
  "bdbh": "BJXQ261123006",
  "attachments": {
    "ok": true, "verified": true, "count": 2,
    "uploaded": [
      {"filename": "AI生成摘要.txt", "name": "AI生成摘要.txt", "size": 59, "content_type": "text/plain"},
      {"filename": "报价示意图.pdf", "name": "报价示意图.pdf", "size": 193, "content_type": "application/pdf"}
    ],
    "errors": []
  }
}
```

预览（`confirm=false`）时只会返回 `attachments_planned`（文件名/字节数/Content-Type），不会上传。
附件失败**不会**把已落库的单据标成失败，而是 `attachments.ok=false` +
`attachments_error`，并追加一条 warning。

## 注意

- 产品名称会先在【产品档案】里模糊检索，**必须唯一匹配**才继续；
  新产品（档案里搜不到）请同时传 `products[0].product_category`
  （如 `"27.01"` / `"27.01;普通BB书"` / `"普通BB书"`），明细行不要求档案存在。
- 明细字段只接受 DOM 首屏可见的 51 个（见 `erp_quote_product_form`），隐藏字段会被拒绝。
- **写后一律回读校验**（明细 `CPMC` + 产品数量），不符就返回 `partial` 并说明卡在哪一步，不假报成功。
- 已知限制：多语言数量明细（`CPSLID`）不自动建；产品类别（树形下拉）不落库。
- 协议细节见 [tools/quote_create/README.md](../../api/src/erp_tools/tools/quote_create/README.md)。
