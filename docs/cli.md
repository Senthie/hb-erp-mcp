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
| `erp quote` | 报价需求【详情】→ ERP 订单需求 JSON |
| `erp attendance` | 月度考勤明细 → Markdown / JSONL |
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
