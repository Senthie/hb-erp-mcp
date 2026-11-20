# `erp_sales_order_node` — 客户订单子数据

> CLI：`erp sales-order-node --id <ID> --node <节点>`｜**只读**

## 用途

客户订单详情页里「点开才有的子数据」。每个节点 = 一次「写隐藏字段 + 回发按钮 + GET 子页」，
耗时 1~2 秒。`node="list"` 会列出全部节点，并标出**这一单**实际能点开哪些
（ERP 只渲染有权限/有数据的按钮）。

## 参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `record_id` | str | `erp_sales_order_list` 的 `记录ID` |
| `node` | str | 节点名或中文别名；`list` = 清单 |

`record_id` 需要的上下文（`cpid` / `bjid` / `cpslid` / `pid`）由工具从详情页自动提取，
不用手填。

## 节点清单（20 个，2026-11-20 实测）

| node | 子页 | 按钮 | 说明 |
| --- | --- | --- | --- |
| `cp` | `Add_CPZL.aspx` | `btnSelectCPZL` | 产品资料表单（整张产品页） |
| `cp_process` | `Add_CPGXZL.aspx` | `btnSelectCPGXZL` | 产品级工序 |
| `cp_materials` | `Add_CPFL.aspx` | `btnSelectCPFL` | 产品级辅料 |
| `part` | `Add_BJZL.aspx` | `btnSelectBJZL` | 部件印刷资料 |
| `part_new` | `Add_BJZL.aspx` | `btnNewBJGXZL` | 新增部件表单 |
| `part_materials` | `Add_BJFL.aspx` | `btnSelectBJFL` | 部件已选辅料 |
| `part_process` | `Add_BJGXZL.aspx` | `btnSelectBJGXZL` | 部件工序 |
| `part_machines` | `Add_BJJTZL.aspx` | `btnSelectBJJTZL` | 部件机台 |
| `product_check` | `Add_CPSHZL.aspx` | `btnSelectCPSHZL` | 送货/审核资料 |
| `quantities` | `Add_CPSL.aspx` | `btnSelectCPSLZL` | 数量档（数量/单价/金额） |
| `fees` | `Brow_FXMX.aspx` | `btnBrowFXMX` | 费项明细（⚠ ERP 死链） |
| `other_fees` | `Add_QTFX.aspx` | `btnSelectQTFX` | 其他费用项目（⚠ ERP 死链） |
| `feedback` | `Add_KHDDXQFK.aspx` | `btnSelectKHBJXQFK` | 订单需求反馈正文 |
| `sgkb` | `Add_SGKB.aspx` | `BtnOpenSGKBT` | 手工开版资料附件 |
| `product_bg` | `Add_CPZLBG.aspx` | `btnSelectCPZLBG` | 产品资料变更记录 |
| `template_product` | `Add_CPMXZL.aspx` | `btnSelectCPMXForm` | 产品模板 |
| `by_template` | `Add_ByMFMB.aspx` | `btnSelectMBCPZL` | 按模板新建产品 |
| `product_guide` | `KHBJXQ_CPZHZY.aspx` | `btnSelectCPZHZY` | 产品综合指引 |
| `extra_info` | `KHBJXQ_CPGJXX.aspx` | `btnSelectExport` | 报价附加信息 |
| `stock` | `Base_CPSJKC_Select.aspx` | `btn_SJKCMX` | 生产库存明细（选择页） |

### 中文别名（可直接当 `node` 传入）

产品资料 / 产品 / 产品表单 → `cp`；产品工序 → `cp_process`；产品辅料 → `cp_materials`；
部件 / 部件资料 → `part`；新增部件 → `part_new`；部件辅料 / 辅料 → `part_materials`；
部件工序 → `part_process`；部件机台 / 机台 → `part_machines`；
送货资料 / 送货信息 / 审核资料 → `product_check`；数量 / 数量档 / 数量明细 → `quantities`；
费项 / 费项明细 / 成本明细 → `fees`；其他费用 / 其他费项 → `other_fees`；
需求反馈 / 订单需求 → `feedback`；开版 / 手工开版 → `sgkb`；
产品变更 / 变更记录 → `product_bg`；模板产品 / 产品模板 → `template_product`；
按模板 → `by_template`；综合指引 / 产品综合指引 → `product_guide`；
附加信息 → `extra_info`；库存 / 生产库存 → `stock`。

## 返回

```json
{
  "node": "quantities",
  "node_label": "数量档（数量/单价/金额）",
  "page": "Add_CPSL.aspx",
  "url": "http://…/DingDan/DD_KHDD/Add_CPSL.aspx?…",
  "fields": {"…": "…"},
  "grids": [{"table": "gridView1", "header": ["…"], "rows": [["…"]]}],
  "texts": {},
  "dropped_controls": [],
  "title": "…"
}
```

- 子页数据是**打开时快照**，不点保存；本工具没有任何写操作。
- `stock` 是「选择页」，返回的是库存候选列表而不是订单数据。

## ⚠ ERP 侧死链（`fees` / `other_fees`）

客户订单详情页上的「费项明细」(`btnBrowFXMX`) 与「其他费用」(`btnSelectQTFX`)
按钮**指向了不存在的页面**：ERP 回发的弹窗 URL 是相对路径 `Brow_FXMX.aspx` /
`Add_QTFX.aspx`，解析后落在 `DingDan/DD_KHDD/` 目录下，返回 **404「无法找到资源」**
（这两个 aspx 只存在于 `BaoJia/BJ_KHBJ/`）。浏览器里点开同样报错，是 ERP 页面模板遗留问题。

工具在这种情况下**不会静默返回空数据**，而是抛错并指明按钮、子页与 404 路径。
需要成本明细时改用：

- `quantities` 数量档（数量 / 单价 / 金额）
- `product_check` 送货资料
- 或到「产品核价 / 客户报价」模块用 `erp_price_node` 的 `fees` 节点

## 注意

- 子页数据是**打开时快照**，不点保存；本工具没有任何写操作。
- 「客户报价」「产品核价」「客户订单」三套页面子页文件名高度相似，
  但按钮是否渲染不同——工具只暴露 `node=list` 里列出的节点。
- 同一 `记录ID` 多产品行时，`cp/part/…` 默认取第一行产品；
  需要指定行请先用 `erp_sales_order_list` 拿到对应产品行的 `记录ID`。
