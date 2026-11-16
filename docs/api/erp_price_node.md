# `erp_price_node` — 客户报价【子数据】（22 个节点）

> 菜单：**业务报价管理 › 客户报价 › 客户报价 › 新增-客户报价**
> 页面：详情页上的弹窗子页 + 折叠面板｜CLI：`erp price-node`｜**只读**

## 用途

详情页里那些「单击可调出…明细」的格子，背后是 14 个弹窗子页 + 8 个折叠面板。
本接口把它们全部结构化，一次调用取一个节点，或 `node=all` 取整棵树。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `record_id` | str | 必填 | 记录 ID（列表的 `记录ID`） |
| `node` | str | 必填 | 见下表；`list` 列出全部节点，`all` 取整棵树 |
| `cpid` | str | 自动 | 产品明细 ID，不传从详情页取 |
| `bjid` | str | 第一个部件 | 部件级节点用 |
| `index` | int | — | 取第几个部件（1 起） |
| `cpslid` | str | 见说明 | 数量档 ID（`fees` 默认 `-1` 汇总，`other_fees` 默认第一档） |
| `include_formula` | bool | `false` | 是否返回费项的计算公式（体积很大） |

### 节点清单

| node | 内容 | 子页 |
| --- | --- | --- |
| `quantities` | 数量档（数量/单价/金额/折扣率/折后价） | `Add_CPSL` |
| `part` | 单个部件印刷资料（只读态子页是空表单 → 用 `detail_row`） | `Add_BJZL` |
| `part_materials` | 部件已选辅料 | `Add_BJFL` |
| `part_process` | 部件工序（27 列） | `Add_BJGXZL` |
| `part_machines` | 部件机台（30 列 + 机台参数） | `Add_BJJTZL` |
| `product_check` | 送货 / 审核资料（28 列） | `Add_CPSHZL` |
| `fees` | 每个数量档的费项（纸张/辅料/印前/印刷/版费/印后/其他） | `Brow_FXMX` |
| `other_fees` | 其他费用项目 | `Add_QTFX` |
| `cp` | 产品资料整表单 | `Add_CPZL` |
| `cp_process` | 产品级工序（29 列） | `Add_CPGXZL` |
| `cp_materials` | 产品级辅料（8 列） | `Add_CPFL` |
| `feedback` | 客户报价需求反馈正文 | `Add_KHBJXQFK` |
| `sgkb` | 手工开版资料附件 | `Add_SGKB` |
| `split_price` | 拆分价格设置（待分配 / 已分配两棵树） | `KHBJ_SetJEXSMS` |
| `panel:description` | 折叠面板【产品描述】：数量档 / 选配项目 / 部件 17 列 | `Lbtn_CPZL` |
| `panel:material_list` | 【材料清单】 | `Lbtn_CLQD` |
| `panel:freight` | 【运费清单】 | `Lbtn_HYFList` |
| `panel:manual_process` | 【手工工序】 | `Lbtn_SGGX` |
| `panel:change_log` | 【修改日志】 | `Lbtn_TLGL` |
| `panel:process_list` | 【工序清单】 | `Lbtn_GXQD` |
| `panel:part_list` | 【部件列表】 | `Lbtn_BJZLList` |
| `panel:price_mode` | 【单价模式】 | `Lbtn_DJMSList` |

节点名也接受中文别名（`产品工序` / `部件机台` / `其他费用` / `产品描述` …）。

## 返回

单个节点：`{ "node": "…", "url": "…", "records" | "fields" | "groups": …, "count": n }`

`node=all`：

```json
{
  "node": "all",
  "ids": { "record_id": "…", "cpid": "…", "bjid": "…", "cpslid": "…" },
  "nodes": { "quantities": { "records": [] }, "panel:description": { "grids": [] } },
  "errors": {},
  "count": 22
}
```

## 注意

- 弹窗子页**不能直接 GET**：先 POST `Add.aspx` 写 `lblSelect*` + 回发 `btnSelect*`，
  从响应的 `showModalDialog('…')` 取地址。
- 子页里「列表」和「左侧编辑表单」行宽往往相同 → 解析器找到表头后锁定在**那一张表**取行。
- `node=all` = 1 次详情 GET + 14 次子页 + 8 次面板回发，实测约 370 KB / 20 秒；
  单节点失败只记进 `errors`，不影响其它。
- 详细协议与坑见 [tools/quote_price/README.md](../../src/erp_tools/tools/quote_price/README.md)。
