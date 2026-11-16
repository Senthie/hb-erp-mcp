# `erp_quote_detail` — 客户报价需求【详情】

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求 › 列表-客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/Add.aspx`（QXID 873）｜CLI：`erp quote`｜**只读**

## 用途

取一张报价需求单的完整字段（表头 + 产品明细 + 数量行 + 备注/配件/检测项目等），
输出可直接落库的「订单需求 JSON」。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `record_id` | str | — | 记录 ID（GUID），来自 `erp_quote_list` 的 `记录ID` 列 |
| `bdbh` | str | — | 表单编号如 `BJXQ261112001`；会用单号里的日期反查记录 ID |

`record_id` 与 `bdbh` 至少给一个。

## 返回

```json
{
  "record_id": "…",
  "record": {
    "表单编号": "BJXQ261112001",
    "客户名称": "…",
    "产品明细": [{ "产品名称": "…", "规格": "…", "数量": 3000 }]
  }
}
```

## 注意

- 只给 `bdbh` 时按 **单号自带日期** 反查；历史已归档单号查不到（返回明确错误）。
- 页面有 A/B 两种布局（textarea 行在 `td#tdCPZLLIst` 里 vs 在 `div#Pan_gridView7` 里），
  解析按优先级多作用域查找，别只认一个容器。
- 字段口径见 [tools/quote_request/README.md](../../src/erp_tools/tools/quote_request/README.md)。
