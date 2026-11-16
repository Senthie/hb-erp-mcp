# `erp_quote_details` — 报价需求的产品明细行

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求 › 列表-客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/Add.aspx`（QXID 873）｜CLI：—｜**只读**

## 用途

列出一张报价需求单下的**产品明细行**，返回每行的 `cpid`。
`erp_quote_update_product` 改单行、`erp_quote_delete` 之类都要先拿这个 `cpid`。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `record_id` | str | 必填 | 报价需求记录 ID（GUID），先由 `erp_quote_list` 查到 |

## 返回

```json
{
  "record_id": "…",
  "count": 2,
  "details": [
    { "cpid": "…", "产品名称": "…", "规格": "210*148", "数量": 3000 }
  ]
}
```

## 注意

- `cpid` 是**明细行**的 ID，不是单据 ID，两者不可混用。
- 单据只有一条明细时也照样返回列表，保持调用方逻辑一致。
