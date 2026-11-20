# `erp_sales_order_detail` — 客户订单【详情】

> 菜单：**客户订单管理 › 客户订单 › 列表-客户订单**（详情由列表的 `记录ID` 进入）
> 页面：`DingDan/DD_KHDD/Add.aspx`｜CLI：`erp sales-order-detail`｜**只读**

## 用途

打开一张客户订单产品行的完整详情：表头 + 产品字段、页内三个网格、备注类文本域，
以及这一单能点开的**子页清单**（`nodes`，配合 `erp_sales_order_node` 使用）。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `record_id` | str | — | `erp_sales_order_list` 返回的 `记录ID` |
| `number` | str | — | 表单编号，如 `KHDD26110043`（与 `record_id` 二选一） |
| `include_texts` | bool | `true` | 是否返回备注类文本域（`textarea1..8`，较长） |

用 `number` 定位时工具会在 当天 → 当月 → 本月 → 近一年 的日期区间里搜索，
命中多行只取第一行并给出候选提示——**多产品单请用 `record_id` 精确指定**。

## 返回

```json
{
  "record_id": "87fd1ad1-f5d4-4929-95cf-f3ea86cf8d98",
  "page_record_id": "87fd1ad1-…",
  "self_check": true,
  "fields": {"表单编号": "KHDD26110043", "订单日期": "…", "…": "…"},
  "grids": [
    {"table": "", "header": ["项目名称", "数量", "款数", "…"], "rows": [[…], […]]},
    {"table": "", "header": ["部件名称", "P数", "色数", "…"], "rows": [[…]]},
    {"table": "gridView", "header": ["选择", "序号", "客户名称", "…"], "rows": [[…]]}
  ],
  "texts": {"textarea1": "封面：350g单粉（FSC）…"},
  "nodes": [{"node": "cp", "page": "Add_CPZL.aspx", "button": "btnSelectCPZL", "label": "产品资料表单（整张产品页）"}]
}
```

- `self_check`：页面上的隐藏 ID 与请求的 `record_id` 是否一致（`false` 会自动重试一次）。
- `grids` 里名为 `gridView` 的网格是**送货明细**；两个无名网格分别是
  **数量档/报价档**和**部件清单**。

## 字段（46 项）

表头：表单编号 / 订单日期 / 订单类型 / 业务员 / 客户资料 / 销售公司 / 送货方式 /
发票类别 / 收款方式 / 收款期限 / 币种 / 特殊要求 / 显示模式 / 制单人 / 制单日期 /
跟单员 / 联系人 / 损益值(%) / 权限名称 / 附送比例(%) / 内价比例(%) / 发票类型

产品：产品描述 / 报价反馈 / 产品名称 / 规格 / 产品类别 / 香港单号 / 客户单号 /
数量 / 附送数量 / 生产数量 / 单位 / 交货日期 / 产品类型 / 产品编号 / FSC类别 /
FSC比例 / 部件备注 / 印前工序 / 印刷机台 / 印后工序 / 部件辅料 / 成品工序 /
包装要求 / 检测项目

## 三个页内网格

| 网格 | 列数 | 列 |
| --- | --- | --- |
| 数量档 / 报价档 | 13 | 项目名称 / 数量 / 款数 / 语言数 / 台板类型 / 规格 / 币种 / 汇率 / 建议利润(%) / 单价 / 金额 / 运输费用 / 送货方式 |
| 部件清单 | 16 | 部件名称 / P数 / 色数 / 部件尺寸 / 开料尺寸 / 单面P数 / 帖数 / 开数 / 拼版方式 / 版材数量 / 材料名称 / 印刷正数 / 印刷加放 / 印后加放 / 印刷交货 / 切纸数量 |
| 送货明细（`gridView`） | 28 | 选择 / 序号 / 客户名称 / 产品名称 / 语言 / 货名名称 / 货名代码 / 送货地址 / 送货数量 / 装箱方式 / 客户订单号 / 最大送货数量 / 最小送货数量 / 附送数量 / 留样数量 / 出样数量 / 出样金额 / 出样日期 / 送货日期 / 联系人 / 联系电话 / 手机 / 离场日期 / 打卡板 / 备注 / 取消送货否 / 吨数 / 产品原币单价 |

## 子页（20 个，详见 `erp_sales_order_node`）

```
cp               Add_CPZL.aspx             产品资料表单（整张产品页）
cp_process       Add_CPGXZL.aspx           产品级工序
cp_materials     Add_CPFL.aspx             产品级辅料
part             Add_BJZL.aspx             部件印刷资料
part_new         Add_BJZL.aspx             新增部件表单
part_materials   Add_BJFL.aspx             部件已选辅料
part_process     Add_BJGXZL.aspx           部件工序
part_machines    Add_BJJTZL.aspx           部件机台
product_check    Add_CPSHZL.aspx           送货/审核资料
quantities       Add_CPSL.aspx             数量档（数量/单价/金额）
fees             Brow_FXMX.aspx            费项明细（⚠ ERP 死链，见 node 文档）
other_fees       Add_QTFX.aspx             其他费用项目（⚠ ERP 死链）
feedback         Add_KHDDXQFK.aspx         订单需求反馈正文
sgkb             Add_SGKB.aspx             手工开版资料附件
product_bg       Add_CPZLBG.aspx           产品资料变更记录
template_product Add_CPMXZL.aspx           产品模板
by_template      Add_ByMFMB.aspx           按模板新建产品
product_guide    KHBJXQ_CPZHZY.aspx        产品综合指引
extra_info       KHBJXQ_CPGJXX.aspx        报价附加信息
stock            Base_CPSJKC_Select.aspx   生产库存明细（选择页）
```

## 注意

- 只读接口：工具只打开详情页并解析，不点任何保存按钮。
- **附件不在详情页**：附件入口在**列表页**的「表单编号」单元格上（`title=左击即可以调出附件`），
  用 `erp_sales_order_attachment` / `erp sales-order-attachment` 取。
- 文本域是无 `name` 的 `textarea1..8`，内容为封面/内文用料、装箱、拼版等工艺说明；
  `include_texts=false` 可省掉这部分体积。
- 与「客户报价」「产品核价」共用同一套子页文件名，但订单页的按钮集合略有差异。
