# `新增报价需求` 页面抽象（BJ_KHBJXQ/Add.aspx）

把「新增客户报价需求」页抽象成**读 + 写**两套能力：读表单/下拉/客户/校验预览，
写新增（主表 + 1 条产品明细，**含产品数量**）、改表头、改产品行、删除。
写入类工具一律 `confirm=false` 先预演，`confirm=true` 才落库，并且**都会写后回读校验**。

## MCP 工具

| 工具 | 用途 | 写库 |
| --- | --- | --- |
| `erp_quote_new_form` | 读空白页：表头字段与只读标记、三个下拉的「名称 → GUID」、textarea 列表 | 否 |
| `erp_quote_options` | 单独查下拉选项：业务员 / 跟单员 / 单价体系，另给 `employees` 合并名单 | 否 |
| `erp_quote_customer_search` | 在客户资料页按名称片段检索，返回 `customer_id` 与该客户默认的业务员/跟单员/折扣率 | 否 |
| `erp_quote_prepare` | 校验并组装「将要提交的字段」，返回 `ok / errors / warnings / header` | 否 |
| `erp_quote_create` | 新建单据：主表 + 1 条产品明细（含产品数量） | ✅ |
| `erp_quote_details` | 列明细行，返回 `cpid`（改/删该行要用） | 否 |
| `erp_quote_update` | 改表头：备注/联系人/电话/手机/传真/折扣率/产品描述/业务员/跟单员 | ✅ |
| `erp_quote_update_product` | 改产品行：名称/规格/数量 | ✅ |
| `erp_quote_delete` | 删单据 | ✅ |

### `erp_quote_options` 返回什么

```jsonc
{
  "as_of": "2026-11-11T04:32:02+00:00",
  "cached": false,
  "fields": {
    "sales":        { "label": "业务员",   "page_field": "txtYGXM",  "count": 24, "options": [{"name": "陈纯", "id": "4d2f..."}] },
    "merchandiser": { "label": "跟单员",   "page_field": "txtGDYXM", "count": 8,  "options": [...] },
    "price_system": { "label": "单价体系", "page_field": "txtDJTX",  "count": 3,  "options": [...] }
  },
  "employees": [
    { "id": "...", "name": "李东员", "can_sales": true, "can_merchandiser": true }
  ]
}
```

- 选项是**按账号权限实时读取**的；换个账号数量可能不同，所以缓存 key 含工号。
- `employees` 是业务员与跟单员的合并视图，用来回答「谁能跟单、谁只能做业务」。
- 占位项（`<--请选择-->` / `-0-`）已剔除。
- 默认缓存 300 秒，`refresh=true` 强制重读；`field` 可只取一个维度（也接受中文名）。

## 页面事实（2026-11 实测，账号 21146 / user_id 2474）

```
GET /Admin/BaoJia/BJ_KHBJXQ/Add.aspx?QXID=873&MenuTree=1&LoginUserID=2474
→ 200，约 74.8 KB
```

- **`QXID` 必填**：不带 873 返回 **HTTP 500**。
- 表单带 `__VIEWSTATE`(17.6K) + `__EVENTVALIDATION`(3.4K)，POST 必须原样回传。
- `txtBDBH`（表单编号）与 `txtKHMC`（客户名称）是 **readonly**：单号由 ERP 生成，
  客户只能通过 `Base_Select/Base_KHZL_Select.aspx` 选择。
- `txtZDR` / `txtZDRQ` 是 `disabled`（制单人、制单时间由系统填）。
- 真正的 `<select>` 只有三个，且**默认都没选中**：
  `txtYGXM` 业务员(25) / `txtGDYXM` 跟单员(9) / `txtDJTX` 单价体系(4)。
  提交时必须给 **GUID/代码**，不能给显示文字。
- 其余约 50 个文本框：联系人 `txtLXR`、电话 `txtLXDH`、手机 `txtSJ`、传真 `txtCZ`、
  折扣率 `txtZKL`、`txtRemark`、`txtCPMX` 等。
- 动作：`btnSave`[保存] / `btnCancel`[取消] / `btnBack`[退出] / `btnFJ`[附件] /
  `btn_SonNew`【新增产品】/ `btnSHZL`【送货信息】。

## 客户 ID 从哪来

客户选择页的每一行把数据挂在行级 `ondblclick` 上：

```html
<tr ondblclick="SelectKHZL('<KHID>','<KHBM>','<KHZL>', ... )">
```

共 **39 个位置参数**，第 0 个就是客户 GUID。`parser.CUSTOMER_FIELDS` 固化了顺序，
且用**贪婪正则**匹配（`II级(标准)` 里含英文右括号，非贪婪会在那里截断导致整体错位）。

除了客户 ID，这一行还带回了默认业务员、跟单员、单价体系、折扣率、币种、汇率等，
所以「选客户」= 一次性填好大半个表头。

## 坑位

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 客户检索返回 1 行但单元格全空 | ERP 查不到时会返回**一行空占位** | `parse_customer_rows` 丢弃首参为空的行走 |
| `lblRowsCount` 有值但取不到客户 | 同上，占位行也算 1 行 | 以解析出的行数为准 |
| 客户下拉里姓名是乱码/取不到 | 提交的是显示文字而不是 GUID | `prepare` 统一经 `resolve_select` 换成 GUID |
| `txtKey` 搜不到中文行业词 | 该框只按客户名称匹配 | 用名称片段重试 |

## 写入现状（2026-11-11 实测，全部已跑通）

真实的写入分两步，已实现于 `QuoteCreateService.create()`：

1. **建主表**：`POST 主表` + `__EVENTTARGET=btn_SonNew`。
   【新增产品】的副作用就是**先把主表存下来**（生成 `BDBH` 与 `lblID`），再弹出产品子页。
   ✅ 实测得到 `BJXQ261111006/007/008`。
2. **产品明细子页**：`GET Add_CPZL.aspx?PID=<lblID>&ID=-1&...`，填产品档案字段 + 必填链 +
   **产品数量**，`POST btnSave`。✅ 明细行与数量都能落库。

第 3 步是**只读回读校验**（不再发任何 POST）：重新打开单据，`CPMC` 为空 → `status=partial`；
再打开产品行编辑页读 `txtCPSL`，与预期不一致 → 也 `partial`。绝不会把「被拒绝」当成 `created`。

```jsonc
{ "status": "created", "ok": true, "record_id": "...", "bdbh": "BJXQ261111007",
  "detail_saved": true, "detail_error": "",
  "steps": [ {"step":"create_header","ok":true},            // 主表
             {"step":"save_product","ok":true},             // 明细行
             {"step":"verify","ok":true,
              "row_created":true,"qty_saved":true} ] }
```

### 明细行（已创建成功）与两个坑

产品明细行**是能写进去的**：实测主表回读能得到 `CPMC=512961503 INDIVIDUAL BOX…`。
之前得出「不落库」的结论是**误判**，真正原因是两个 bug：

1. **报错没被识别**：ERP 把校验提示写在 `$.ligerDialog.error('…')` 里，早先只匹配 `alert(`，
   所以「被拒绝」被当成了「成功」。现在 `_find_validation_message` 会同时匹配两者。
2. **主表重存覆盖明细**：第三步用 `btn_SonNew` 之前的旧快照再 POST 一次 `btnSave`，
   会把刚写入的明细行整行覆盖掉。已删掉这一步（`btn_SonNew` 已经保存过主表）。

### 产品数量：`txtCPSL` 才是真字段（已破，2026-11-11）

明细行会建，但早先 `CPSL1` 一直为空。真正的原因不是「必须走【产品数量】弹窗」，
而是**发错了字段**：

| 控件 | 作用 | 服务端是否校验 |
| --- | --- | --- |
| `txtCPSL`（隐藏） | **汇总数量**，页面「产品数量：」框算出来的那个 | ✅ 校验 + 落库都是它 |
| `txtCPSL1..10`（可见） | 「每款一行」的展示框（款数=1 时只有 `txtCPSL1`） | ❌ 单独发它一定被挡 |
| `txtCPSLID1..10` | 多语言数量明细行的 GUID，未建时为 `-1` | — |

只发 `txtCPSL1` 会永远卡在 `【产品数量】不能为空！`；补上 `txtCPSL` 后立刻通过，
并且重开行编辑页能回读到 `txtCPSL=3000`、主表列显示 `3000个`。

- `create()`：`sub_body["txtCPSL1"] = sub_body["txtCPSL"] = qty`（两个都发，兼容款数>1）。
- 校验：打开行编辑页读 `txtCPSL`（`_product_qty_readback`），不依赖明细格子。
- 注意：**汇总数量能写，多语言/多款明细表（`CPSLID`）不会自动建**，详见「已知限制」。

### 另一个必踩坑：`form_snapshot()` 原本不收 textarea

子页的「配件描述 / 修改记录 / 检测项目 / 产品描述」都是 `<textarea>`。
`form_snapshot()` 以前只收 `input` + `select`，于是**任何重新保存都会丢这几个字段**，
被 ERP 挡在 `【配件描述】不能为空！`（表现为 `update_product` 永远失败）。
已改为同时快照 textarea（取 `area.string`）。

### 实测的必填链（按服务端提示顺序）

```
产品类别 → 装订方式 → 款数 → 语言数 → 产品数量 → 包装方式 → 台板类型
→ 送货方式 → 交货日期 → 配件描述 → 产品属性 → 产品类型 → 预计下单日期
→ 产品描述 → 修改记录 / 检测项目
```

这些字段已通过 `SUB_FIELD_MAP` / `create(products=[{...}])` 对外开放：
`binding / packing / pallet / delivery / delivery_date / expected_date /
parts_desc / modify_log / inspection / product_desc / attribute / product_type /
cases / languages`。


## 删除单据（已实现并验证）

```
GET  /Admin/BaoJia/BJ_KHBJXQ/Add.aspx?id=<记录ID>&Option=3&QXID=876&MenuTree=1&LoginUserID=<uid>
POST 同上 URL，表单快照 + __EVENTTARGET=btnDelete
```

- `Option=3` 是「只读打开」，这个状态下 `[删除]` 是可用（黑色）的；`[取消]/[保存]` 则是 disabled。
- 判定删除成功：**再打开一次，`lblID` 为空**。
- 工具：`erp_quote_delete(record_id, confirm=false)`。`confirm=false` 只检查存在性并回单号，绝不发 POST。
- 实测删除 `BJXQ261111006 / 007 / 008` 均成功（删除后立刻回读为空）。

## 修改单据：为什么要先点 [修改]

工具栏的三个动作是三个独立的回发事件：

| 动作 | 事件 | 说明 |
| --- | --- | --- |
| [修改] | `__doPostBack('btnModify','')` | 把当前单据切到可编辑状态 |
| [保存] | `__doPostBack('btnSave','')` | 只有 `btnModify` 之后才是 enabled |
| [删除] | `__doPostBack('btnDelete','')` | 与编辑状态无关，`Option=3` 下直接可用 |

页面里 `<a id="btnSave" disabled="disabled">` 就是「未进入修改态」的证据。

### 表头修改（已实现并实测通过）

`QuoteCreateService.update_quote()` / MCP `erp_quote_update`：

```
GET  Add.aspx?id=<记录ID>&Option=3&QXID=876
POST btnModify           → 返回页里 id="btnSave" disabled 消失
POST btnSave（原表单快照 + 新字段）
再打开一次记录，逐字段比对 changed vs verified
```

可改字段：备注 `txtRemark`、联系人 `txtLXR`、电话 `txtLXDH`、手机 `txtSJ`、传真 `txtCZ`、
折扣率 `txtZKL`、产品描述 `txtCPMX`、业务员 `txtYGXM`、跟单员 `txtGDYXM`（后两个传姓名，
内部用页面自己的下拉解析成 GUID）。

实测：临时单据改备注/联系人/折扣率 → `status=updated`，三个字段回读全部一致。

### 产品明细行（`OpenInputCPZLData` 的真相）

明细区里点产品名称，其实**不是开新窗口**，而是：

```js
OpenInputCPZLData('<CPID>', '<PID>', '0')   // 0=修改, 1=删除
  → lblSelectCPZLID.value = CPID
  → lblSelectCPZLPID.value = PID
  → btnSelectCPZL.click()          // 提交主表
```

主表回发的响应里带一段脚本，打开
`Add_CPZL.aspx?PID=<PID>&ID=<CPID 或 产品GUID>&ZKL=..&QXID=873&SetPageID=872_1_&KHID=..&Modify=1`。

- `erp_quote_details`：只读，解析每次 `OpenInputCPZLData(...,'0')` 拿到 `cpid`（`'1'` 的删除按钮会被忽略）。
- `erp_quote_update_product`：按 `ID=<cpid>` 打开编辑页 → 改字段 → `POST btnSave` → 回读名称与数量复核。

✅ **产品行保存已跑通**（2026-11-11 实测）：

```
update_qty:  updated  readback=4200
update_name: updated  readback=512961503 MCP改动名
```

两处必须自己补，否则保存会被拒：

1. **textarea 要回传**（见上文 `form_snapshot` 的坑）；
2. **产品类别要补回**：`txtCPLB` 是 ComponentArt 树形下拉，显示文字由前端 JS 写进
   `txtCPLB_Input`，服务端渲染出来的 HTML 里只有 `<--请选择-->`。
   `_restore_product_category()` 会按产品名回查【产品档案】，把 `txtCPLB_Input` /
   `txtLBID` / `txtLBBM` 填回去，否则报 `请选择【产品类别】的数据！`。


## 已知限制（2026-11-11 定稿）

| 限制 | 影响 | 现状 |
| --- | --- | --- |
| **产品类别（树形下拉）写不进库** | 存下来的明细行「产品类别」为空，重开行编辑页 `txtCPLB_Input` 又回到 `<--请选择-->` | 只影响该字段本身；每次保存前用 `_restore_product_category()` 从产品档案临时补回以通过校验，**ERP 侧仍为空** |
| **多语言/多款数量明细（`CPSLID`）未建** | 汇总数量 `3000个` 正确，但点「…（编辑产品数量）」的多语言明细表是空的 | 汇总值已满足 ERP 校验与报价使用；明细表要走 `btn_CPSLZL` 弹窗，而服务的校验顺序（先验数量再开弹窗）在 HTTP 上闭合成环，需浏览器抓一次弹窗提交体才能定协议 |
| **保存成功也可能带一句旧提示** | `POST` 返回页里可能残留 `请输入【产品数量】…` | 所以**不能**用提示文案判成败，一律以回读为准（`steps[].verify`） |
| **一次只支持 1 条产品明细** | `create` 传多条会返回 `status=unsupported` | 多条请分次调用 |
| **`erp_quote_update_product` 的 `spec` 只改 `txtGG` 文本** | 不改长/宽/高三个分段框 | 需要精确尺寸请同时改产品档案 |

## 早期探测记录（保留作参考，结论已被上文取代）

`btn_SonNew`（新增产品明细行）是纯 `__doPostBack`，但实测：

```
POST __EVENTTARGET=btn_SonNew
→ HTTP 200，74628 字符（原文 74863）
→ td#tdCPZLLIst 内 input 数仍为 0
```

即**回发不产生明细行**。要落库必须先搞清：

1. 明细行到底由哪个事件生成（是否要求先选客户 / 先选产品模板）；
2. 生成后各行字段的 `input name`；
3. `btnSave` 的成功标志与新单号来源。

在上述三点验证之前，本模块**不会调用 `btnSave`**，`erp_quote_prepare` 固定返回
`submit_supported: false`，避免把「预览」误当成「已创建」。

> 2026-11-11 更新：上面三点已经全部验证完毕，`btnSave` 链路已开通
> （`submit_supported: true`，真正落库用 `erp_quote_create(confirm=true)`）。
> 本节仅留作排查历史。

## Python 调用

```python
from erp_tools.tools.quote_create import QuoteCreateService

svc = QuoteCreateService(auth)
svc.describe_form()
svc.search_customers("HAS", limit=10)
svc.prepare(customer="HAS10", products=[{"name": "彩盒", "spec": "210*148", "qty": 5000}])

# 真正写入（会落库，建议先 confirm=False 预览）
created = svc.create(customer="HAS10", sales="陈纯", confirm=True, products=[{
    "name": "512961503", "spec": "75*28*298", "qty": 3000, "unit": "个",
    "category": "STD彩盒", "binding": "胶装/PAD书", "packing": "纸箱包装",
    "pallet": "07", "delivery": "FOB(NGV)",
    "delivery_date": "2026/11/20", "expected_date": "2026/11/20",
    "parts_desc": "无", "modify_log": "无", "inspection": "无",
    "product_desc": "MCP数量验证", "attribute": "三岁以上",
    "product_type": "新产品", "cases": 1, "languages": 1,
}])
cpid = svc.list_details(created["record_id"])["details"][0]["cpid"]
svc.update_product(created["record_id"], cpid, qty=4200, confirm=True)
svc.delete_quote(created["record_id"], confirm=True)
```
