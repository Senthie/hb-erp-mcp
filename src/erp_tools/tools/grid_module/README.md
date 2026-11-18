# grid_module —— 三个「列表页 → 详情页 → 弹窗子页」模块

一次把三个同源模块做完：**列表-产品核价 / 列表-生产施工单 / 列表-采购订单**。
它们和「客户报价需求」「客户报价」是同一套 WebForms 模板，只是页面路径、QXID
和过滤下拉不同，所以这里用 `ModuleSpec` 参数化，解析逻辑共享。

```
spec.py     三个模块的页面/QXID/过滤下拉/子页目录（module → 元数据）
parser.py   详情页解析（标签→值、内联网格、文本域）+ 子页入口自动发现
tool.py     ModuleListTool / ModuleDetailTool / ModuleNodeTool
tools.py    按模块拆出的具体类（CLI 名 production-list / MCP 名 erp_production_list）
```

## 页面事实（实测 2026-11-18，账号 15627 → UserID 2069）

| 模块 | 列表页 | QXID | 一行是什么 | 详情页 | 弹窗数据子页 |
| --- | --- | --- | --- | --- | --- |
| 产品核价 `costing` | `BaoJia/BJ_KHBJ/Index.aspx` | **1281** | 一个核价产品行（`CPHJ…`） | `BJ_KHBJ/Add.aspx` | 复用客户报价的 22 个节点 |
| 生产施工单 `production` | `ShengChan/SC_SCSGD/Index.aspx` | **740** | 一张施工单（`SGD…`） | `SC_SCSGD/Add.aspx` | 18 个 |
| 采购订单 `purchase` | `CaiGou/CG_CGDD/Index.aspx` | **266** | 一条材料明细（`CGDD…`） | `CG_CGDD/Add.aspx` | 无（明细在 `gridView2`） |

## 三个必须知道的坑

### 1. QXID 不能靠菜单探测

`parse_menu_urls` 用 `setdefault`，同一路径挂多个菜单时只留第一个：

- `BJ_KHBJ/Index.aspx` → 探测到 **860**（客户报价，56 列），而核价要 **1281**（74 列）
- `SC_SCSGD/Index.aspx` → 探测到 **983**（另一个菜单），而施工单要 **740**
  （症状：66 列、单号变成 `ZBSQ…` 制版申请单）

所以 `qxid_for()` 的顺序是 **配置覆盖 > spec 内置实测值 > 菜单探测**。
环境变量：`ERP_QXID_COST_INDEX` / `ERP_QXID_PRODUCTION_INDEX` / `ERP_QXID_PURCHASE_INDEX`。

### 2. 详情页回发必须先剔除「空 options / disabled」控件

施工单详情页有 `<select name="txtBJBJLB"></select>`（**零个 option**）和一堆
`disabled` 控件。浏览器根本不会提交它们；若我们照 `form_snapshot` 原样发回去，
ERP 的事件验证（`enableEventValidation="true"`）直接 500
「回发或回调参数无效」。`_drop_invalid_controls()` 就是干这个的。

### 3. 子页 URL 只能从回发响应里拿，且实参要从页面「反向推导」

- 子页不是 GET 出来的：写 `lblSelect*` 隐藏字段 → 回发 `btnSelect*` →
  响应里的 `showModalDialog('xxx.aspx?...')` 才是子页 URL。
- 「哪个参数写哪个隐藏字段」**从 `Open*` 函数体反推**：
  `$HB("lblX").value = 参数名` / `InputData("lblX", 参数名)`。
- 「实参」取页面里**真实出现**的那次调用。注意三点：
  * 有的赋值**不带分号**（`= PID` 后面直接换行），正则只能截到行尾，
    否则会把下一行吞进来；
  * 未加引号的实参是表达式（`OpenX(PID, CPID)`），带换行/空格，必须丢弃；
  * `onclick` 里的单引号会被转成 `&#39;`，解析前要 unescape。
- 有些按钮在只读页里**没有**带实参的调用（按钮在隐藏工具栏），此时按字段名语义
  用详情页已解析出的 `pid/cpid/bjid` 回填（`fill_missing`）。只认明确的字段名，
  `lblSelectTZCPZLID`（套装产品 ID）/ 费项页里的 `lblSelectBJZLID`（其实是数量）
  都不回填，宁可留空。
- **「有函数」≠「能点开」**：施工单复制了报价页模板，`OpenBrowFXMX` / `OpenInputQTFXData`
  的函数体还在，但 `btnBrowFXMX` / `btnSelectQTFX` 按钮压根没渲染 → 这类节点不声明、
  `discover_nodes()` 也会校验按钮是否真的存在（否则列表里会出现永远打不开的节点）。

## 验证方式

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_grid_module.py -q        # 解析层（夹具，离线）
.\.venv\Scripts\erp.exe pages --module production                        # 页面清单
.\.venv\Scripts\erp.exe production-list --limit 3                        # 列表
.\.venv\Scripts\erp.exe production-node --id <ID> --node list            # 这一单能开哪些子页
```
