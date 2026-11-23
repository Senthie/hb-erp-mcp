# CLI 使用手册（`erp` 命令）

命令行入口，适合**本机跑批、导出 CSV / JSONL、临时查数**。
所有命令都复用同一份 `core/` + `tools/` 页面逻辑，与 MCP 服务行为一致。

- 想嵌进别的程序 → [api.md](api.md)
- 想给 AI 客户端用 → [mcp.md](mcp.md)

## 1. 安装

```powershell
uv sync --extra dev        # 核心依赖（含验证码自动识别 ddddocr）
.\.venv\Scripts\erp.exe --version
```

只用 CLI **不需要** `--extra server`（那是 MCP 服务端依赖 `mcp` + `uvicorn`）。

## 2. 配置

优先级（高 → 低）：

```
命令行 --host/--user/--password  >  真实环境变量  >  config.yaml  >  旧版 .env  >  内置默认值
```

`config.yaml` 查找顺序：用户数据目录 `%APPDATA%\erp-tools` → 程序目录（随包携带）→ 当前目录。
`erp config` 可随时确认**最终生效的来源**。

```powershell
Copy-Item packaging\config.example.yaml config.yaml
.\.venv\Scripts\erp.exe setup          # 向导：地址 / 工号 / 密码，并缓存 cookie
```

```yaml
erp:
  host: http://192.168.1.199:8889   # 内网；外网用 http://14.12.0.172:8889
  captcha_host: ""                  # 留空 = host 的 8887 端口
  username: "21146"
  password: ""                      # 也可用环境变量 ERP_PASSWORD
runtime:
  max_concurrency: 8                # 实测该 ERP 4~8 并发即饱和
  page_size_max: 5000
qxid:
  quote_index: ""                   # 留空自动探测
```

常用环境变量（等价于 `.env`）：

| 变量 | 说明 |
| --- | --- |
| `ERP_HOST` | ERP 地址，如 `http://192.168.1.199:8889` |
| `ERP_CAPTCHA_HOST` | 验证码服务地址；留空 = 主机 + 8887 端口 |
| `ERP_USERNAME` / `ERP_PASSWORD` | **只给 CLI 用**（`erp serve` 完全不读，见 [mcp.md](mcp.md)） |
| `ERP_MAX_CONCURRENCY` / `ERP_REQUEST_DELAY` | 并发上限 / 最小请求间隔（秒） |
| `COOKIE_DIR` | 登录态缓存目录，默认 `./cookies` |
| `ERP_QXID_*` | 手工指定 QXID；留空则登录后从菜单自动探测 |

## 3. 命令总览

| 命令 | 作用 |
| --- | --- |
| `erp setup` | 首次配置向导（写 `config.yaml` + 缓存 cookie） |
| `erp doctor` | 自检：配置来源、端口连通、登录页/验证码、OCR、cookie 有效性 |
| `erp login` | 登录并缓存 cookie、探测 QXID |
| `erp config` | 打印生效配置、数据目录、cookie 路径 |
| `erp qxid` | 打印「页面路径 → QXID」映射 |
| `erp tools` | 列出业务工具与全部选项 |
| `erp query` | 报价需求【列表】查询 / 导出 CSV |
| `erp price` | 客户报价【列表】查询 / 导出 CSV（`BJ_KHBJ`，一行=一个报价产品行） |
| `erp price-detail` | 客户报价【详情】（表头+产品+数量+部件+费用+描述） |
| `erp price-node` | 客户报价【子数据】（数量档 / 部件 / 工序 / 辅料 / 费项 / 面板，共 22 个节点） |
| `erp quote` | 报价需求【详情】→ ERP 订单需求 JSON |
| `erp quote-attachment` | 报价需求【附件】：列表 / 上传（`--file` / `--text`）/ 删除（`--delete`）/ 下载 |
| `erp pages` | 四个新模块的**数据页面清单**（列表 / 详情 / 弹窗子页） |
| `erp costing-list` | 产品核价【列表】（`BJ_KHBJ` QXID=1281，一行=一个核价产品行） |
| `erp production-list` | 生产施工单【列表】（`SC_SCSGD` QXID=740，一行=一张施工单） |
| `erp production-detail` | 生产施工单【详情】（字段 + 部件表 + 送货表 + 子页清单） |
| `erp production-node` | 生产施工单【子数据】（20 个弹窗子页） |
| `erp production-row` | 生产施工单【列表行内点击事件】（电子稿袋/跟进信息/材料清单/打印…） |
| `erp purchase-list` | 采购订单【列表】（`CG_CGDD` QXID=266，一行=一条材料明细） |
| `erp purchase-detail` | 采购订单【详情】（表头 + 材料明细网格 33 列） |
| `erp purchase-node` | 采购订单子页面（供应商档案选择等弹窗） |
| `erp sales-order-list` | 客户订单【列表】（`DD_KHDD` QXID=687，一行=一个订单产品行） |
| `erp sales-order-detail` | 客户订单【详情】（46 字段 + 3 个网格 + 8 个文本域） |
| `erp sales-order-node` | 客户订单【子数据】（20 个弹窗子页） |
| `erp sales-order-attachment` | 客户订单【附件】（表单编号左击调出的文件，可下载到目录） |
| `erp attendance` | 月度考勤明细 → Markdown / JSONL |
| `erp menu` | 菜单 → 接口对照：抓菜单树 / 覆盖率 / 生成 `docs/erp-menu.md` |
| `erp key` | 向网关换取 api-key（见 [mcp.md](mcp.md)） |
| `erp serve` | 启动 api-key 网关 + MCP（见 [mcp.md](mcp.md)） |

## 4. 命令详解

### `erp setup` / `erp doctor` / `erp login` / `erp config` / `erp qxid`
```powershell
erp setup        # 交互式向导
erp doctor       # 排障第一条命令，会打印实际 host/账号/验证码识别结果
erp login        # 缓存 cookie；--force 强制重新登录
erp config       # 看配置到底从哪来
erp qxid         # 页面 -> QXID；--json 输出 JSON
```

`erp login` 输出成功后会列出探测到的页面与 QXID（`873` 报价需求页、`876` 列表页、`2553` 考勤页）。

### `erp query` — 报价需求列表

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `--from` / `--to` | 本月 1 号 ~ 今天 | 日期区间（**服务端必填**，留空会被服务器套默认区间并忽略关键字） |
| `--key` / `--field` | 空 | 服务端关键字与字段（`--field` 用下拉里的中文名） |
| `--customer` / `--sales` / `--status` | 空 | 便捷条件，等价于 `--where` |
| `--where` | 空 | **本地**过滤，逗号分隔，支持 `=` `!=` `~` `>=` `<=` `>` `<` |
| `--columns` | 全部 38 列 | 只输出这些列 |
| `--out` | 空 | 写 CSV；空则打印到 stdout |
| `--dry-run` | — | 只报命中数，不写文件 |

```powershell
erp query --out out\list.csv
erp query --customer IBL --status 需求审批 --out out\ibl.csv
erp query --from 2026-01-01 --to 2026-11-06 `
    --where "制单人!=李东员,数量描述>=5000" `
    --columns "表单编号,客户名称,产品名称,数量描述,记录ID" --out out\q.csv
erp query --where "报价状态~需求审批" --dry-run
```

- 服务端只支持「日期 + 两个关键字」，多条件一律本地过滤。
- 查不到时 ERP 会返回**一行全空占位**，解析时已丢弃。
- 列名与含义见 [quote_index/README.md](../src/erp_tools/tools/quote_index/README.md)。

### `erp price` — 客户报价列表

客户报价（`BaoJia/BJ_KHBJ/Index.aspx`，QXID=**860**）与报价需求（`BJ_KHBJXQ`）是两个模块。
本页**一行 = 一个报价产品行**（56 列）：同一个表单编号会有多行，形如
`KHBJ261112003-01`（末尾 `-NN` 是行号），用 `记录ID` 定位单行。

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `--from` / `--to` | 本月 1 号 ~ 今天 | 日期区间 |
| `--date-type` | `制单日期` | 区间作用于哪个日期：`制单日期` / `交货日期` / `离厂日期` |
| `--key` / `--field` | 空 | 服务端关键字（`--field` 用下拉里的中文名：表单编号/客户名称/产品名称…） |
| `--key1` / `--field1` | 空 | 第二套关键字 |
| `--filter` | 空 | **服务端**下拉过滤，可重复：`--filter 审批状态=已全审批` |
| `--customer` / `--sales` / `--status` | 空 | 便捷条件，等价于 `--where` |
| `--where` | 空 | **本地**过滤，语法同 `erp query` |
| `--columns` | 全部 53 列 + `记录ID` | 只输出这些列（前 3 个操作列默认剔除） |
| `--page-size` | `ERP_PAGE_SIZE_MAX` | 调大一次取全；不够则用 `btnNext` 续翻 |

`--filter` 的键可以写中文别名或控件名：审批状态(`txtCheck`)、报废状态(`txt_SFBF`)、
确认情况(`txtQDQK`)、签订单(`txtXKHDD`)、查询方式(`txtFXCX`)、客户属性(`txtKHSX`)、
模板(`txtMBF`)、预计报价月份(`txtBetRQ`)、报价分类(`txtCFWZ`)。值用下拉的显示文字或提交值都行。

```powershell
erp price --out out\price.csv
erp price --filter 审批状态=已全审批 --filter 报废状态=未报废 --out out\ok.csv
erp price --date-type 交货日期 --from 2027-01-01 --to 2027-12-31 --limit 20
erp price --key KHBJ261112003 --field 表单编号 --dry-run
```

- 页面的 `搜索` 是 `<input type="submit" name="btn_Search">`，**必须把它自己的 name/value
  一起回发**（代码里从页面读，别写死文案）；只发 `.x/.y` 会让 ERP 走默认绑定，条件全被忽略。
- 查不到时同样返回一行全空占位 + 一行「合计」，解析时都已丢弃。

### `erp price` — 客户报价列表

客户报价（`BaoJia/BJ_KHBJ/Index.aspx`，QXID=**860**）与报价需求（`BJ_KHBJXQ`）是两个模块。
本页**一行 = 一个报价产品行**（56 列）：同一个表单编号会有多行，形如
`KHBJ261112003-01`（末尾 `-NN` 是行号），用 `记录ID` 定位单行。

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `--from` / `--to` | 本月 1 号 ~ 今天 | 日期区间 |
| `--date-type` | `制单日期` | 区间作用于哪个日期：`制单日期` / `交货日期` / `离厂日期` |
| `--key` / `--field` | 空 | 服务端关键字（`--field` 用下拉里的中文名：表单编号/客户名称/产品名称…） |
| `--key1` / `--field1` | 空 | 第二套关键字 |
| `--filter` | 空 | **服务端**下拉过滤，可重复：`--filter 审批状态=已全审批` |
| `--customer` / `--sales` / `--status` | 空 | 便捷条件，等价于 `--where` |
| `--where` | 空 | **本地**过滤，语法同 `erp query` |
| `--columns` | 全部 53 列 + `记录ID` | 只输出这些列（前 3 个操作列默认剔除） |
| `--page-size` | `ERP_PAGE_SIZE_MAX` | 调大一次取全；不够则用 `btnNext` 续翻 |

`--filter` 的键可以写中文别名或控件名：审批状态(`txtCheck`)、报废状态(`txt_SFBF`)、
确认情况(`txtQDQK`)、签订单(`txtXKHDD`)、查询方式(`txtFXCX`)、客户属性(`txtKHSX`)、
模板(`txtMBF`)、预计报价月份(`txtBetRQ`)、报价分类(`txtCFWZ`)。值用下拉的显示文字或提交值都行。

```powershell
erp price --out out\price.csv
erp price --filter 审批状态=已全审批 --filter 报废状态=未报废 --out out\ok.csv
erp price --date-type 交货日期 --from 2027-01-01 --to 2027-12-31 --limit 20
erp price --key KHBJ261112003 --field 表单编号 --dry-run
```

- 页面的 `搜索` 是 `<input type="submit" name="btn_Search">`，**必须把它自己的 name/value
  一起回发**（代码里从页面读，别写死文案）；只发 `.x/.y` 会让 ERP 走默认绑定，条件全被忽略。
- 查不到时同样返回一行全空占位 + 一行「合计」，解析时都已丢弃。

### `erp price-detail` — 客户报价详情

按 `记录ID`（或表单编号）打开 `BJ_KHBJ/Add.aspx?id=<GUID>&Option=3`（QXID=**857**，只读），
解析成一个 JSON：`header` / `product` / `quantities` / `optionals` / `parts` / `fees` / `audit` / `description`。
（`optionals` 是「选配项目列表」，只有部分报价单才有，没有时是空列表。）

| 选项 | 说明 |
| --- | --- |
| `--id GUID` | 列表页的 `记录ID` |
| `--bdbh 单号` | 表单编号；可以只给前缀（如 `KHBJ261112003` 命中 `-01/-02` 全部行） |
| `--row N` | 配合 `--bdbh`：取第 N 行（1 起） |
| `--out 路径` | 写 JSON（`.jsonl` 结尾则写 JSONL） |
| `--no-description` | 不输出产品描述长文本 |

```powershell
erp price-detail --id 89ad5e46-4872-4b26-83f5-18cab4fe2fe4 --out out\d.json
erp price-detail --bdbh KHBJ261112003 --row 2 --out out\row2.json
```

- 详情页是 `Option=3` 只读态：真正的值在**单元格里没有 name 的 `<INPUT readonly VALUE>`** 上，
  所以按「标签格 → 右邻格」读，不能按控件名取（`txtBJ*`/`txtKJ*` 那些是弹窗编辑器的暂存控件，都是空的）。
- `汇率/发票税率/折扣率` 没有独立的标签格（和 币种/发票类别 挤在同一格），按控件名取。
- `--bdbh` 先按单号里的日期查（`KHBJ` + YYMMDD），查不到再退到「本月」「近一年」——
  实测有单号的日期与制单日期不一致的（如 `KHBJ261016002-01` 的制单日期在 11 月）。
- 数量明细表**只有第一组**表头写「产品数量」，后面写「数量」；空值用 `.` 占位（已归一成空串）。
- 部件表里跟在部件行后面的「印前工序/印刷机台/印后工序/部件辅料/部件备注」会挂到该部件上。

### `erp price-node` — 客户报价子数据（点开才有）

详情页上那些“单击可调出…明细”的格子，实际是**同一个 `Add.aspx` 发一次 POST**：
把 GUID 写进 `lblSelect*` 隐藏字段 + 回发 `btnSelect*` 按钮，响应里
`showModalDialog('xxx.aspx?...')` 才是子页 URL。工具会帮你做完这两步再解析。

| `--node` | 子页 | 内容 |
| --- | --- | --- |
| `quantities` | `Add_CPSL.aspx` | 数量档：数量/单价/金额/折扣率/折后单价/折后金额/建议利润/单款数量 |
| `part` | `Add_BJZL.aspx` | 单个部件（`detail_row` 是父页的真实部件行；子页在只读态只回 `txtID`） |
| `part_materials` | `Add_BJFL.aspx` | 部件辅料：部件名称/材料编码/材料名称/单价/材料长宽/指定数量/系数 |
| `part_process` | `Add_BJGXZL.aspx` | 部件工序（27 列：工序单价/起版费/版材/加放/工序正数…） |
| `part_machines` | `Add_BJJTZL.aspx` | 部件机台（30 列：机台名称/正反面色/版材/加放）+ 机台字段 |
| `product_check` | `Add_CPSHZL.aspx` | 送货/审核资料（28 列送货明细 + 客户/送货字段） |
| `fees` | `Brow_FXMX.aspx` | 每个数量档的费项：纸张/辅料/印前/印刷/版费/印后/其他 + 公式与单价 |
| `other_fees` | `Add_QTFX.aspx` | 其他费用项目（费用项目/计价公式/税点/金额） |
| `cp` | `Add_CPZL.aspx` | 产品资料：整张产品表单（类别/装订/尺寸/金额拆分/纸箱/报关…） |
| `cp_process` | `Add_CPGXZL.aspx` | 产品级工序（29 列） |
| `cp_materials` | `Add_CPFL.aspx` | 产品级辅料（8 列） |
| `feedback` | `Add_KHBJXQFK.aspx` | 客户报价需求反馈正文（textarea） |
| `sgkb` | `Add_SGKB.aspx` | 手工开版资料附件 |
| `split_price` | `KHBJ_SetJEXSMS.aspx` | 拆分价格设置（待分配 / 已分配两棵树） |

折叠面板不弹窗、是**整页回发**（`__EVENTTARGET=Lbtn_Xxx`），内容在 `div#Pan_gridView*`：

| `--node` | 面板 | 内容 |
| --- | --- | --- |
| `panel:description` | 【产品描述】 | 数量档 / 选配项目 / 部件明细（17 列）/ 送货明细 |
| `panel:material_list` | 【材料清单】 | 需求数量/库存/报价单价/反馈单价 |
| `panel:freight` | 【运费清单】 | 重量/装箱/总 CBM/运费 |
| `panel:manual_process` | 【手工工序】 | 手工工序单价 |
| `panel:change_log` | 【修改日志】 | 修订记录 |
| `panel:process_list` | 【工序清单】 | 工序汇总 |
| `panel:part_list` | 【部件列表】 | 部件汇总 |
| `panel:price_mode` | 【单价模式】 | 单价模式 |

```powershell
erp price-node --id <记录ID> --node quantities --out out\q.json
erp price-node --id <记录ID> --node part --index 2 --out out\bj2.json
erp price-node --id <记录ID> --node part_machines --out out\jt.json
erp price-node --id <记录ID> --node panel:description --out out\desc.json
erp price-node --id <记录ID> --node list          # 列出全部节点
```

- `--cpid` / `--bjid` 不传时自动从详情页取（`bjid` 取第一个部件，`--index` 可选第几个）。
- `--node list` 打印 22 个节点（含 8 个面板）；`--node all` 一次取完整棵树
  （1 次详情 GET + 14 次子页 + 8 次面板，实测约 370 KB / 20 秒；单节点失败会记到 `errors` 不影响其它）。
- 节点名可用中文别名：`产品工序` / `部件工序` / `其他费用` / `产品描述` …
- `fees` 一页约 1 MB（10 个数量档 × 7 类费项），MCP 里默认**不返回公式**（`include_formula=true` 才给）。
- 子页里的列表和左侧编辑表单**行宽往往相同**，所以解析器找到表头后会锁定在那张表里取行；
  运费清单那种 colspan 排版抓不到“网格”，会退回按「首行标签 / 次行值」配对后放进 `fields`。

### `erp pages` — 四个模块的数据页面清单

```powershell
erp pages                              # 四个模块全列
erp pages --module production          # 只看生产施工单
erp pages --module sales_order         # 只看客户订单
```

打印每个模块的列表页/QXID、详情页、服务端过滤下拉、日期字段和**全部弹窗子页**
（子页名 + 打开它的按钮 + 中文说明）。代码侧见
`src/erp_tools/tools/grid_module/README.md`。

### `erp costing-list` / `erp production-list` / `erp purchase-list` / `erp sales-order-list` — 四个列表

参数与 `erp price` 一致：`--from/--to/--date-type/--key/--field/--where/--filter/
--columns/--out/--jsonl/--limit/--page-size/--max-pages/--dry-run`。

```powershell
erp costing-list --from 2026-11-01 --to 2026-11-18 --filter 审批情况=已全审批 --out out\cphj.csv
erp production-list --filter 工单状态=已审批 --date-type 交货日期 --out out\sgd.csv
erp purchase-list --where 表单编号=CGDD261118031 --out out\cgdd.csv
erp sales-order-list --date-type 订单日期 --filter 订单状态=编辑中 --out out\khdd.csv
```

- `costing-list` 与 `price` 是**同一张 aspx**，只是 QXID 不同（1281 vs 860），
  核价页多出核价单价/利润率/各项成本列。
- `purchase-list` 没有 `--date-type`（该页固定按制单日期），且一行=一条材料明细。
- `sales-order-list` 的 `--date-type` 可选 `制单日期` / `订单日期` / `离厂日期` / `交货日期`，
  一行=一个订单产品行（同一 `KHDD` 单号可能多行）。

### `erp production-detail` / `erp purchase-detail` / `erp sales-order-detail` — 详情

```powershell
erp production-detail --id <记录ID> --out out\sgd.json
erp production-detail --no SGD2611180011          # 按生产单号反查
erp purchase-detail --no CGDD261118031 --no-texts
erp sales-order-detail --no KHDD26110043 --no-texts
```

返回 `fields`（标签→值）、`grids`（内联网格）、`texts`（备注长文本）、
`nodes`（这一单能点开的子页）、`self_check`（页面里的记录 ID 与请求是否一致）。

### `erp production-node` / `erp purchase-node` / `erp sales-order-node` — 子数据

```powershell
erp production-node --id <记录ID> --node list            # 这一单能开哪些子页
erp production-node --id <记录ID> --node part_materials --out out\fl.json
erp production-node --id <记录ID> --node 部件辅料          # 中文别名也行
erp sales-order-node --id <记录ID> --node quantities --out out\sl.json
erp sales-order-node --id <记录ID> --node 需求反馈 --out out\fk.json
```

### `erp production-row` — 列表行内点击事件

列表页每一行上的图标/链接（不是详情页的弹窗节点）：

```powershell
erp production-row --id <记录ID>                     # 列出这一行能点什么
erp production-row --id <记录ID> --action 材料清单    # clqd
erp production-row --id <记录ID> --action print --out out\rp.json
erp production-row --id <记录ID> --action esign --cpid <产品ID>
```

动作：`esign` 电子稿袋 / `gjqk` 产品跟进信息 / `zhzy` 做货指引 / `wjqk` 文件跟进情况 /
`zszm` 专色油墨 / `clqd` 临时材料清单 / `print` 打印报表列表；
`info`、部件备注、核价否 在 ERP 侧不可读（会直接告诉原因）。

### `erp sales-order-attachment` — 附件下载

```powershell
erp sales-order-attachment --no KHDD26110042                  # 只打印直链
erp sales-order-attachment --no KHDD26110041 --out-dir out\fj  # 下载（多附件会展开）
erp sales-order-attachment --from 2026-11-01 --to 2026-11-20 --out-dir out\fj --out out\fj.json
```

附件挂在 ERP 静态目录 `/Admin/FuJian/DD_KHDD/Picture/<guid>.<ext>`（**:8887** 端口），
直链 **HTTP GET 即可下载，不需要 cookie**；重名文件自动追加 GUID 前缀，不覆盖。

### `erp quote` — 报价需求详情

| 选项 | 说明 |
| --- | --- |
| `--id` / `--csv` / `--bdbh` | 三选一：记录 GUID / 含 `记录ID` 列的 CSV / 表单编号（可逗号分隔多个） |
| `--out` | JSONL 输出路径（追加写 + 按 `id` 去重，天然支持中断续跑） |
| `--limit` | 只跑前 N 条（抽样验证用） |
| `--workers` | 并发数，默认取配置 |
| `--no-resume` | 忽略已有 JSONL，从头跑 |

```powershell
erp quote --csv out\q.csv --out data\detail.jsonl
erp quote --bdbh BJXQ261105013 --out data\one.jsonl
erp quote --csv orders.csv --limit 50 --out data\sample.jsonl
erp quote --csv orders.csv --out data\quote.jsonl     # 18496 条，约 2 小时
```

`--bdbh` 会用单号里编码的制单日期（`BJXQ` + `YYMMDD` + 流水号）自动定位记录 ID，
不需要先跑列表。字段说明见 [quote_request/README.md](../src/erp_tools/tools/quote_request/README.md)。

### `erp attachment` — 通用附件（五模块复用）

客户报价需求 / 客户报价·产品核价 / 生产施工单 / 采购订单 / 客户订单共用同一套
`*_FJ.aspx` 附件页，用 `--module` 切换；附件页路径会自动发现。

| 选项 | 说明 |
| --- | --- |
| `--module` | `quote_request`（默认）/ `price` / `production` / `purchase` / `sales_order` |
| `--id` | 单据记录 ID（**必填**，单据必须先保存） |
| `--file` | 本机文件，可重复或逗号分隔（⚠ **单个 ≤10 MB**，ERP 硬限；更大请切分卷分别上传） |
| `--text` / `--name` | 直接传文本内容 + ERP 里的附件名称 |
| `--desc` / `--remark` | 附件描述 / 备注 |
| `--urls` | 列表时一并取下载直链（同时给出 `proxy_url`：网关转发，跨网段可下） |
| `--download` | 按名称（或文件 ID）下载一个附件 |
| `--delete` / `--yes` | 删除指定名称（或文件 ID）的附件；不加 `--yes` 只预览 |
| `--out-dir` | 下载目录（不传则输出 base64） |

```powershell
erp attachment --module sales_order --id <记录ID> --urls
erp attachment --module quote_request --id <记录ID> --file out\报价单.pdf --name "客户报价单"
erp attachment --module production --id <记录ID> --download "图纸.pdf" --out-dir out\fj
erp attachment --module purchase --id <记录ID> --delete "旧附件.pdf"          # 只预览
erp attachment --module purchase --id <记录ID> --delete "旧附件.pdf" --yes     # 真删
```

`erp quote-attachment` 是 `--module quote_request` 的兼容别名。

> 直链是 ERP 内网地址，换网段不可达；`--urls` 打出的第二行 `代理 …` 是网关签名链接，
> 任意网段都能下（详见 [api/erp_attachment.md](api/erp_attachment.md)）。

> MCP 侧对应 `erp_attachment`（`--module quote_request`）；`erp_quote_create(attachments=[...])`
> 会在建单成功后自动调用它。协议、各模块差异与限制见
> [api/erp_attachment.md](api/erp_attachment.md)。

### `erp attendance` — 月度考勤

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `--month` | 必填 | 形如 `2026-11` |
| `--from` / `--to` | `01` / `30` | **ERP 页面只有 01–30**，传 31 会被收敛（否则只会回 1 号一行） |
| `--emp` | 当前账号 | 工卡号，可重复；支持 `工号:密码` 换账号 |
| `--out` / `--jsonl` | 空 | Markdown / JSONL 输出 |

```powershell
erp attendance --month 2026-11 --emp 21146 --out out\att.md
```

查不到人通常是**账号数据权限**只到本部门，不是工具问题。
字段说明见 [attendance/README.md](../src/erp_tools/tools/attendance/README.md)。

### `erp menu` — 菜单 → 接口对照

ERP 左侧菜单树（`GET /Admin/indexAjax.ashx?FunName=GetMenuTree`）是**按账号权限**渲染的，
这里把它抓成快照，再把「每个菜单页上已经实现了哪些操作、对应哪个接口和文档」拼成一张表。

| 选项 | 说明 |
| --- | --- |
| `--capture` | 重新抓菜单树 → `docs/menu.tree.json`（需要登录，会记录账号与时间） |
| `--tree` | 打印树，带 QXID 与 ✅/⬜ 接入标记 |
| `--coverage` | 覆盖率统计（**默认行为**）：模块 / 菜单页 / 已接入 / 未接入 |
| `--write` | 生成 `docs/erp-menu.md` |
| `--check` | 校验生成物与代码一致（CI 用，不一致退出码 1） |
| `--root` | 仓库根目录（默认自动向上找 `pyproject.toml`） |

```powershell
erp menu                    # 看覆盖率
erp menu --capture --write  # 抓菜单 + 重新生成对照表
erp menu --tree             # 看完整菜单树
```

产出：

- `docs/erp-menu.md` —— **自动生成，勿手改**。文首是覆盖率表，正文是按菜单层级排的
  完整树，每个页面后面跟「动作 → 接口 → 文档」，没接入的标 ⬜。
- `docs/menu.tree.json` —— 菜单树快照（账号 / 时间 / 来源 / 树）。

追加能力的流程：在 `src/erp_tools/menu_catalog.py` 的 `PAGE_CAPABILITIES` 里给对应页面
加一行（动作 + 接口名 + `docs/api/<接口名>.md`），然后 `erp menu --write`。
忘了重新生成、或接口名/文档路径写错时，`tests/test_menu_docs.py` 会失败。

菜单树里有三个要注意的地方：`url=''` 的纯分组节点（系统管理）、同一路径挂在多个菜单下
（`KC_CLCRKSQ` 既是采购申请 1036 又是部门领料申请 331）、以及 `text` 里夹着 ERP 自己
HTML 换行留下的空格（`业务需 求`），解析后统一去掉。

## 5. 翻页策略

1. **首选**：把 `txtEvenPageDataSize` 直接调大（默认 5000），一次拿全 → `lblPageCount == 1`。
2. **兜底**：仍多页时用 `__EVENTTARGET=btnNext` 逐页回发。该分支在部分环境返回
   `500 未将对象引用设置到对象的实例`，此时会**明确抛错**（不会静默截断），提示调大页大小。

## 6. 常见问题

先跑自检，它会打印实际配置来源、端口连通性、登录页/验证码状态与 cookie 有效性：

```powershell
erp doctor
```

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `500 回发或回调参数无效…事件验证` | 登录 POST 少了 `__EVENTVALIDATION`（已修复） | 升级到当前版本 |
| `Max retries exceeded … too many 500 error responses` | 旧版把 500 当可重试错误，真正原因被吞了 | 新版不再重试 500，会直接显示服务端报错 |
| 验证码识别失败 | OCR 模型未随包或图片异常 | `erp doctor` 会打印识别结果；失败时自动转人工输入 |
| `登录被拒绝（HTTP 500）` | 账号密码/ERP 地址不对，或账号被锁 | `erp config` 核对 host/账号；`--host` 临时试另一台 |
| `无法获取用户 ID（cookie 不完整）` | 登录没真正生效（302 不代表成功），或服务端未下发 `LoginUserID` | 拿不到 uid 时 QXID 自动回退默认值（873/876/2553），业务页靠会话照常工作 |
| 考勤查不到人 | 账号数据权限只到本部门 | 换有权限的账号：`erp attendance --emp 工号:密码` |
| `ERP 连不上 / 502` | 网络或端口 | 登录阶段取验证码失败会归类为**网络错误**，不会算作「密码错」而消耗锁定次数 |

登录协议固定携带的隐藏字段（缺一不可）：`__VIEWSTATE`、`__VIEWSTATEGENERATOR`、`__EVENTVALIDATION`。

## 7. 页面协议速查（排查用）

**登录**：`GET /Admin/Login.aspx` → 验证码在**另一个端口**（8889 → 8887）→ `POST` → 302 即成功。
Cookie 值是「带前缀」的：`LoginUserID=LoginUserID=2474`，真实 ID 要 `split("=")` 取右半段。

**报价需求（列表）**：`/Admin/BaoJia/BJ_KHBJXQ/Index.aspx?QXID=876&IsAdd=1&LoginUserID=<uid>`

- 表单不硬编码：先 GET 页面，把整张 `form1` 快照下来再覆盖条件。
- 结果在 `div#gViewDiv > table#gridView`，第 1 行 `tr.FixedTitleRow` 为 38 列表头。
- **主记录 ID** 在 `<tr ondblclick="OnDBClickOpenData('<ID>','876',3)">`；行内还有子表 GUID，
  不能按「第一个 GUID」取。
- 日期区间留空时服务端会套默认区间并**忽略关键字**（实测返回 68 条近期数据）。

**报价需求（详情）**：`/Admin/BaoJia/BJ_KHBJXQ/Add.aspx?id=<GUID>&Option=3&QXID=873&MenuTree=1`
（`873` = 新增/查看页；`876` = 列表页）

**考勤**：`/Admin/RenShi/RS_KQDA_M/Add.aspx?QXID=2553&MenuTree=1&LoginUserID=<uid>`

- `txtBMMC_Data` 必须提交**二次编码**后的 `%3Cr%3E%3Cc%3E%3C%2Fc%3E%3C%2Fr%3E`；
  裸 `<r><c></c></r>` 会被 ASP.NET request validation 判为「潜在危险的 Request.Form 值」→ 500。
- `lblgVQXBH` 必须等于当前页 QXID。
- 结果在 `div#gViewDiv2 > table#gridView`。

**QXID 探测**：登录后 `GET /Admin/indexAjax.ashx?FunName=GetMenuTree&Value1=<uid>&Value2=`
（**必须 GET**，POST 返回空），响应是 JS 字面量，按 `url:'...'` 正则取路径 → QXID。

## 8. 已知限制（CLI 相关）

- 解析依赖中文标签定位，ERP 改文案需同步改 `tools/*/parser.py` 并补夹具。
- 列表页只能查到**库里仍在**的单据：`orders.csv` 里 2021~2022 的单号多已归档，不能当测试样本。
- 考勤页的「查询范围」受账号数据权限限制。
- 同一个 ERP 可能存在**多端口实例**（实测 `8889` 与 `8890` 数据不互通），切环境前先 `erp config`。
- 打包运行时 `.env` / `cookies/` / QXID 缓存落在 `%APPDATA%\erp-tools`（安装目录不可写）。
