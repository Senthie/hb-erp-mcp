# `erp_quote_list` — 客户报价需求【列表】查询

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求 › 列表-客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/Index.aspx`（QXID 876）｜CLI：`erp query`｜**只读**

## 用途

按日期区间 + 一个服务端关键字查报价需求单，其余条件下拉到本地用 `where` 过滤，
输出 38 列（含「记录ID」，取详情/改/删都用它）。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `from_date` | str | 本月 1 号 | 制单起始日期 `YYYY-MM-DD` |
| `to_date` | str | 今天 | 制单结束日期 `YYYY-MM-DD` |
| `where` | str | — | 本地过滤，逗号分隔；`字段 操作符 值`，操作符 `= != ~ > < >= <=`（`~` 为包含） |
| `customer` | str | — | 客户名称片段（等价 `where` 里的 `客户名称~X`） |
| `sales` | str | — | 业务员姓名片段 |
| `status` | str | — | 报价状态片段（如 `需求审批`） |
| `key` | str | `""` | 服务端关键字，配合 `field` |
| `field` | str | `""` | 关键字字段中文名：`表单编号` / `客户名称` / `产品名称` / `香港单号` / `业务员` … |
| `columns` | str | 全部 | 只返回这些列，逗号分隔 |
| `limit` | int | 50 | 1~500，超出静默截断并置 `truncated` |

## 返回

```json
{
  "columns": ["表单编号", "客户名称", "报价状态", "记录ID"],
  "unknown_columns": [],
  "records": [{ "表单编号": "BJXQ261112001", "客户名称": "IBL10", "记录ID": "…" }],
  "count": 12,
  "total": 12,
  "pages": 1,
  "truncated": false
}
```

## 注意

- **服务端只支持「日期区间 + 1 个关键字」**，两个以上条件必须走 `where` 本地过滤。
- 日期区间必填语义：`txtKSRQ/txtJZRQ` 留空时服务端会套默认区间并**忽略关键字**。
- 搜索按钮是 `input type=submit`，必须回发 `btn_Search=搜索`；只发 `.x/.y` 会让所有条件失效。
- 查不到时接口返回一行全空占位，已丢弃。
- 详细字段与翻页策略见 [tools/quote_index/README.md](../../src/erp_tools/tools/quote_index/README.md)。
