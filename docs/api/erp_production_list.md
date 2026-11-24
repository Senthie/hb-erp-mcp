# `erp_production_list` — 生产施工单【列表】

> 菜单：**生产管理 › 生产施工单 › 列表-生产施工单**
> 页面：`ShengChan/SC_SCSGD/Index.aspx`（QXID **740**）｜CLI：`erp production-list`｜**只读**

## 用途

按日期区间 / 服务端下拉 / 本地条件筛**生产施工单**并导出 CSV / JSONL。

**一行 = 一张施工单**，生产单号 `SGD` + `YYMMDD` + 流水（如 `SGD2611180011`）。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `from_date` / `to_date` | str | 本月 1 号 / 今天 | 日期区间 |
| `date_type` | str | `制单日期` | `制单日期`(0) / `交货日期`(1) / `审批日期`(2)，对应控件 `txtRQLX` |
| `filters` | dict | — | **服务端**下拉，见下表 |
| `where` | str | — | 本地过滤，如 `生产单号~SGD2611,工单状态=已审批` |
| `key` / `field` | str | `""` | 服务端关键字 + 字段中文名（生产单号 / 订单编号 / 产品名称 / 客户名称…） |
| `key1` / `field1` | str | `""` | 第二组关键字 |
| `columns` | str | 65 列 | 只返回这些列 |
| `limit` | int | 50 | 1~500 |

### 服务端下拉（`filters` 的键，中文/控件名都认）

| 中文键 | 控件 | 常见值 |
| --- | --- | --- |
| 工单状态 | `txtSCZT` | 全部 / 编辑中 / 审批中 / 已审批 / 入库中 / 入库完 / 已送货 / 已暂停 / 已报废 / 已审批或入库中 |
| 审批情况 | `txtCheck` | 全部 / 未审批 / 已全审批 / 未全审批 |
| 报废状态 | `txt_SFBF` | 全部 / 已报废 / 未报废 |
| 工单类型 | `dropGDLX` | 订料(017) / 大货(013) / 补数(014) / 变更(021) / 打办(015) / 预排单(030) / 试产(022) / 模版(024) / 全检工单(027) |
| 文件状态 | `txtWJZT2` | 文件接收 / 文件批复 / 文件完成 |
| 推送状态 | `txtTSZT` | 全部 / 未推送 / 已推送 / 需重新推送 |
| 是否补数 / 是否调版 / 是否变更 | `txtBSC` / `txtDBF` / `txtBGF` | 全部 / 是 / 否 |
| 客户属性 | `txtKHSX` | 内销 / 外销 / 转厂 |
| 模板 | `txtMBF` | 全部 / 是模板 / 非模板 |
| 数据类型 | `txtDataLX` | 产品信息(0) / 送货信息(1) |

## 返回

```json
{
  "columns": ["生产单号", "订单编号", "产品名称", "客户名称", "工单状态", "制单日期"],
  "handle_columns": ["记录ID", "产品ID", "可点动作"],
  "records": [{
    "生产单号": "SGD2611230005",
    "工单状态": "编辑中",
    "记录ID": "30a7b30c-…",
    "产品ID": "204731AE-…",
    "可点动作": ["gjqk", "zhzy", "wjqk", "clqd", "print", "info", "bjremark", "cphj"]
  }],
  "count": 50,
  "total": 112,
  "pages": 1,
  "truncated": true,
  "note": "一行 = 一张生产施工单；详情用 erp_production_detail"
}
```

### 三个「句柄」列始终会带回

| 列 | 用途 |
| --- | --- |
| `记录ID` | `erp_production_detail` / `erp_production_node` / `erp_attachment` 的入参 |
| `产品ID` | 列表行内子页 `erp_production_row` 的入参 |
| `可点动作` | 这一行**实际**挂了哪些行内事件（与上一次实验一致） |

它们不是业务列，所以：**即使传了 `columns` 也会一并返回**
（`columns` 里只反映你要的业务列，`handle_columns` 列出实际带回的句柄）。

> 这条是踩出来的：早期版本把 `记录ID` 当成普通列，调用方一旦指定 `columns`
> 就把它裁掉了，于是「拿列表 → 查详情」链断在第一步。

## 典型调用链

```
erp_production_list(columns="生产单号,工单状态")   # → 每行带 记录ID / 产品ID
   ├─ erp_production_detail(record_id=…)          # 整张施工单：字段+网格+子页清单
   │     └─ erp_production_node(record_id, node)  # 详情页里的弹窗子页
   └─ erp_production_row(record_id, action=…)     # 列表行上的图标/链接子页
```

## 注意

- **QXID 必须是 740**。菜单里 `SC_SCSGD/Index.aspx` 还挂着 983 号菜单（另一个页面），
  用菜单探测会打开 66 列、单号 `ZBSQ…`（制版申请）的错误列表。
- `btn_Search` 是 `input type=submit value=搜索`，必须回发 `btn_Search=搜索`，否则条件全被忽略。
- 实测 2026-11-01~18：89 行（页大小 5000 一页取全）。
