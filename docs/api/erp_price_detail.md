# `erp_price_detail` — 客户报价【详情】（一个报价产品行）

> 菜单：**业务报价管理 › 客户报价 › 客户报价 › 新增-客户报价**
> 页面：`BaoJia/BJ_KHBJ/Add.aspx`（QXID 857，`&Option=3` 只读）｜CLI：`erp price-detail`｜**只读**

## 用途

取**一个报价产品行**的完整信息：表头、产品信息、数量明细、选配项目、部件明细、
费用、审核信息、产品描述长文本。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `record_id` | str | — | 记录 ID（列表的 `记录ID`） |
| `bdbh` | str | — | 表单编号如 `KHBJ261112003-01`；可只给前缀 `KHBJ261112003` |
| `row` | int | — | 配合 `bdbh`：取第几行（1 起）；命中多行又不给 `row` 时返回 `need_row` + 候选 |
| `include_description` | bool | `true` | 是否返回产品描述长文本（很长，约数 KB） |

## 返回

```json
{
  "record_id": "…",
  "bdbh": "KHBJ261112003-01",
  "cpid": "2F9AD6D3-…",
  "header": { "表单编号": "…", "业务人员": "…", "客户资料": "…", "汇率": "6.6475" },
  "product": { "产品名称": "…", "规格": "216×276×13" },
  "quantities": [{ "数量": "2000", "单价": "13.12183", "cpslid": "847EC69E-…" }],
  "optionals": [],
  "parts": [{ "部件名称": "封面", "P数": "2", "bjid": "488032a9-…" }],
  "fees": { "纸箱费用": "350" },
  "audit": { "制单人": "…", "审核人": "…" },
  "description": "…",
  "diagnostics": []
}
```

## 注意

- 页面是 `Option=3` 只读态：表头控件全 `disabled`，真值在**单元格里没有 name 的
  `<INPUT readonly VALUE='…'>`** 上 → 只能「标签格 → 右邻格」读，不能按控件名取。
- 一个格子可能同时装 `select` + 辅助 `input`（币种格里塞着汇率）→ 取值顺序
  select 选中项 > input 值 > 纯文本；占位项当空串。
- `汇率/发票税率/折扣率` 没有标签格，只能按 `txtHL/txtFPSL/txtZKL` 取。
- 返回的 `cpid` / `parts[].bjid` / `quantities[].cpslid` 可以直接喂给 `erp_price_node`。
- **已知坑**：同一 Cookie 下刚看过 A 再按 `id=B` GET，偶尔仍返回 A 的页面；
  工具已用页面内 `lblID` 自校验并重试一次。
