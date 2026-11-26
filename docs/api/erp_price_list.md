# `erp_price_list` — 客户报价【列表】查询

> 菜单：**业务报价管理 › 客户报价 › 客户报价 › 列表-客户报价**
> 页面：`BaoJia/BJ_KHBJ/Index.aspx`（QXID 860）｜CLI：`erp price`｜**只读**

## 用途

按日期 / 服务端下拉 / 本地条件筛**报价产品行**，导出 CSV / JSONL。

⚠ **一行 = 一个报价产品行，不是一张单据**。表单编号形如 `KHBJ261112003-01`，
末尾 `-NN` 是行号；同一张单的多个产品会是多行，定位单行用「记录ID」。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `from_date` / `to_date` | str | 本月 1 号 / 今天 | 日期区间 |
| `date_type` | str | `制单日期` | 区间作用于：`制单日期`(ZDRQ) / `交货日期`(KHJHRQ) / `离厂日期`(LCRQ) |
| `filters` | dict | — | **服务端**下拉：审批状态 / 报废状态 / 确认情况 / 签订单 / 查询方式 / 客户属性 / 模板 / 预计报价月份 / 报价分类（值可写中文或代码） |
| `where` / `customer` / `sales` / `status` | str | — | 本地过滤 |
| `key` / `field` | str | `""` | 服务端关键字 + 字段中文名 |
| `columns` | str | 53 列 | 只返回这些列（操作列 `选择/信息/打印` 默认剔除） |
| `limit` | int | 50 | 1~500 |

## 返回

```json
{
  "columns": ["报价状态", "客户名称", "表单编号", "记录ID"],
  "records": [{ "表单编号": "KHBJ261112003-01", "记录ID": "…" }],
  "count": 50,
  "total": 123,
  "pages": 1,
  "truncated": true,
  "note": "一行 = 一个报价产品行；同一表单编号可能多行，用 记录ID 定位单行"
}
```

## 注意

- `btn_Search` 是 `input type=submit`，必须回发 `btn_Search=搜索`；只发 `.x/.y` 会让**所有条件被忽略**。
- 默认列表 ≠ 搜索结果（同一天：默认 89 行 / 显式搜索后 123 行），要数据一律带日期区间。
- 下拉值既接受显示文字也接受代码，写错会报错并列出真实选项。
- 字段清单与坑见 [tools/quote_price/README.md](../../api/src/erp_tools/tools/quote_price/README.md)。
