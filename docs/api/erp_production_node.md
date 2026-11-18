# `erp_production_node` — 生产施工单子数据（18 个弹窗子页）

> 页面：详情页 `SC_SCSGD/Add.aspx` 里「点开才有」的弹窗子页
> CLI：`erp production-node --id <ID> --node <节点>`｜**只读**

## 打开协议（实测）

子页**不能直接 GET**，必须模拟「写隐藏字段 → 点按钮」：

1. GET 详情页，取整张表单快照；
2. 按该 `Open*` 函数的函数体写入它要求的 `lblSelect*`
   （实参取页面里**真实出现**的那次调用，避免参数错位）；
3. 回发对应 `btnSelect*`（WebForms 必须带按钮的 name/value）；
4. 响应里的 `showModalDialog('xxx.aspx?...')` 就是子页 URL；
5. GET 该 URL，解析成 `fields` + `grids` + `texts`。

## 参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `record_id` | str | 施工单记录 ID |
| `node` | str | 节点名（见下表）；`list` = 列出全部并标注这一单能否打开 |

## 节点清单（18 个）

| node | 子页 | 打开按钮 | 内容 |
| --- | --- | --- | --- |
| `cp` | `Add_CPZL.aspx` | `btnSelectCPZL` | 产品资料整张表单 |
| `cp_process` | `Add_CPGXZL.aspx` | `btnSelectCPGXZL` | 产品级工序 |
| `cp_materials` | `Add_CPFL.aspx` | `btnSelectCPFL` | 产品级辅料 |
| `part` | `Add_BJZL.aspx` | `btnSelectBJZL` | 部件印刷资料 |
| `part_new` | `Add_BJZL.aspx` | `btnNewBJGXZL` | 新增部件表单（空表单） |
| `part_materials` | `Add_BJFL.aspx` | `btnSelectBJFL` | 部件已选辅料 |
| `part_process` | `Add_BJGXZL.aspx` | `btnSelectBJGXZL` | 部件工序 |
| `part_machines` | `Add_BJJTZL.aspx` | `btnSelectBJJTZL` | 部件机台 |
| `product_check` | `Add_CPSHZL.aspx` | `btnSelectCPSHZL` | 送货 / 审核资料（21 列） |
| `part_pick` | `Modify_CPSHZL_Select.aspx` | `btnBJMS` | 部件明细（选择部件） |
| `product_bg` | `Add_CPZLBG.aspx` | `btnSelectCPZLBG` | 产品资料变更记录 |
| `feedback` | `Add_SCSGDXQFK.aspx` | `btnSelectKHBJXQFK` | 生产需求反馈正文 |
| `sgkb` | `Add_SGKB.aspx` | `BtnOpenSGKBT` | 手工开版资料附件 |
| `template_product` | `Add_CPMXZL.aspx` | `btnSelectCPMXForm` | 产品模板 |
| `by_template` | `Add_ByMFMB.aspx` | `btnSelectMBCPZL` | 按模板新建产品 |
| `binding` | `Add_D1_D1_D10.aspx` | `btnSelectBJZL_D10` | 装订 / 贴数（D10）明细 |
| `package_content` | `Add_D1_D9.aspx` | `btnSelectBXNR` | 包装内容（D9）明细 |
| `stock` | `Base_CPSJKC_Select.aspx` | `btn_SJKCMX` | 生产库存明细（选择页） |

### 为什么没有「其他费用 / 费项」

施工单页复制了报价页的模板，`OpenInputQTFXData` / `OpenBrowFXMX` 的**函数体还在**，
但 `btnSelectQTFX` / `btnBrowFXMX` 这两个按钮在施工单 Detail 页里**根本没有渲染**
（HTML 里搜不到）。因此 `open_fees`/`freight` 不作为节点声明；
`discover_nodes()` 现在也会校验「按钮是否真的渲染」，光有函数不算可用。
需要看费项请看报价/核价单：`erp_price_node --node fees` / `--node other_fees`。

中文别名（如 `部件辅料`、`部件机台`、`开版`、`D10`）也能直接当 `node` 传。

## 返回

```json
{
  "node": "part_materials",
  "node_label": "部件已选辅料",
  "page": "add_bjfl.aspx",
  "url": "/Admin/ShengChan/SC_SCSGD/Add_BJFL.aspx?PID=…&BJID=…&SetPageID=736_4.1&QXID=740",
  "fields": {"部件名称": "内文", "材料名称": "…"},
  "grids": [{"table": "gridView", "header": ["材料编码","材料名称","单价"], "rows": []}],
  "texts": {},
  "dropped_controls": ["txtBJBJLB", "txtBJZYFS"],
  "title": "部件辅料"
}
```

## 注意

- **必须剔除空 `<select>` 与 disabled 控件**再回发，否则 500「回发或回调参数无效」。
- 有些节点在这一单没有渲染数据（例如 `part_materials` 可能是空网格），
  `fields`/`grids` 为空不代表失败；`node=list` 会给每个节点打 `available`。
- 打开的是**只读态**页面（`Modify=0`），但理论上这些子页也能提交——
  本工具**只做 GET 与「打开弹窗」这一种回发**，不写数据。
