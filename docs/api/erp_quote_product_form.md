# `erp_quote_product_form` — 产品明细子页字段清单 + 上传 JSON Schema

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求 › 新增-客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/Add_CPZL.aspx`（QXID 873）｜CLI：—｜**只读**

## 用途

返回产品明细子页的字段注册表，并给出**上传用的 JSON Schema**：

`control`（ERP 提交名）/ `alias`（JSON 字段名）/ 中文标签 / 类型 /
`visible`（DOM 首屏可见）/ `writable` / `uploadable` / 已知下拉选项。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `record_id` | str | — | 给了就打开该单据的明细编辑页，返回当前值、真实下拉选项与只读状态 |
| `cpid` | str | — | 明细行 ID（来自 `erp_quote_details`），与 `record_id` 搭配定位单行 |

不传 `record_id` → 返回**静态注册表**（不联网）。

## 返回

```json
{
  "fields": [{ "control": "txtCPMC", "alias": "name", "label": "产品名称",
               "visible": true, "writable": true, "uploadable": true }],
  "upload": { "json_schema": { "…": "…" }, "example": { "…": "…" } },
  "categories": [{ "code": "27.01", "name": "普通BB书", "lbid": "…" }]
}
```

## 注意

- **只有 `uploadable=true` 的字段（实测 51 个）能被 `erp_quote_create` 写入**；
  折叠面板里的 142 个隐藏字段会被拒绝。
- 键名拼错会直接报错，不做静默忽略。
- 字段全量口径见 [tools/quote_create/README.md](../../src/erp_tools/tools/quote_create/README.md)。
