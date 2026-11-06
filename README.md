# erp-tools — 华博印刷 ERP 工具聚合

把过去散落在各脚本里的 ERP 逆向成果（登录、权限号、报价需求页、考勤页）收敛成一个
**可扩展的工具聚合项目**：CLI + Python API 双入口，新增工具只需加一个目录。

```
src/erp_tools/
├─ core/           通用能力（与业务无关）
│  ├─ config.py      环境/.env 配置；主机端口、并发、QXID 全部可配，无硬编码
│  ├─ session.py     线程内 Session、Retry、Connection:close、Cookie 快照共享
│  ├─ limiter.py     实例级全局限流（并发上限 + 最小间隔）
│  ├─ auth.py        登录、验证码、Cookie 持久化、会话校验、QXID 自动探测
│  ├─ aspnet.py      __VIEWSTATE 提取、PostBack 组装、ScriptManager delta 拆解
│  ├─ htmlutil.py    「标签→textarea」、td 取值、数量解析等页面工具
│  └─ errors.py      可重试/不可重试的异常体系
├─ tools/          业务工具（每个目录内都有 README.md：命令 / 选项 / 输出格式）
│  ├─ base.py        Tool 契约：options() + run() -> 记录流
│  ├─ registry.py    注册表（新增工具只改这一处）
│  ├─ quote_index/   列表查询 + CSV 导出（BJ_KHBJXQ/Index.aspx）
│  ├─ quote_request/ 报价需求详情（BJ_KHBJXQ/Add.aspx）
│  └─ attendance/    考勤（RS_KQDA_M/Add.aspx）
├─ output/         jsonl（断点续跑）+ csv + markdown
├─ cli.py          `erp <工具> [选项]`
└─ api.py          库入口

packaging/        PyInstaller 配置 + Inno Setup 安装器脚本（见 packaging/README.md）
scripts/build.ps1 一键打包：测试 → exe → 安装包
```

仓库根：

```
customer_info/
├─ src/erp_tools/       源码（见上）
├─ tests/fixtures/      解析器回归用真实页面
├─ archive/raw-pages/   历史抓包（仅协议对照，不参与运行）
├─ orders.csv           报价采集输入（18496 条）
├─ data/                旧版脚本产出（每条一个 JSON 文件，保留）
└─ cookies/             登录态缓存（不进版本库）
```

## 快速开始

```powershell
uv sync --extra dev                                   # 核心依赖（已包含验证码自动识别）
Copy-Item packaging\config.example.yaml config.yaml  # 填 ERP 地址 / 工号 / 密码

.\.venv\Scripts\erp.exe setup                       # 或跑向导：生成配置 + 缓存 cookie
```

配置也可以直接写在 `config.yaml`：

```yaml
erp:
  host: http://192.168.1.199:8889   # 内网；外网用 http://14.12.0.172:8889
  captcha_host: ""                # 留空 = host 的 8887 端口
  username: "21146"
  password: ""                    # 也可用环境变量 ERP_PASSWORD
runtime:
  max_concurrency: 8
  page_size_max: 5000
qxid:
  quote_index: ""                 # 留空自动探测
```

**配置优先级（高 → 低）**：命令行 `--host/--user/--password` > 真实环境变量 > `config.yaml`
> 旧版 `.env` > 内置默认值。`config.yaml` 查找顺序：用户数据目录 `%APPDATA%\erp-tools`
→ 程序目录（随包携带的那份）→ 当前目录。用 `erp config` 可随时确认生效来源。

```powershell
.\.venv\Scripts\erp.exe login              # 登录 + 缓存 cookie + 探测 QXID
.\.venv\Scripts\erp.exe config             # 查看配置来源、数据目录、cookie 路径
.\.venv\Scripts\erp.exe qxid               # 查看 页面 -> QXID 映射
.\.venv\Scripts\erp.exe tools              # 列出工具与选项

# 报价需求【列表】查询 + 导出 CSV（默认查本月；--from/--to 控日期）
.\.venv\Scripts\erp.exe query --out out\list.csv

# 便捷条件：客户 / 业务员 / 报价状态
.\.venv\Scripts\erp.exe query --customer IBL --status 需求审批 --out out\ibl.csv

# --where 本地过滤（逗号分隔，支持 = != ~ >= <= > <）
.\.venv\Scripts\erp.exe query --from 2026-01-01 --to 2026-11-06 `
    --where "制单人!=李东员,数量描述>=5000" --columns "表单编号,客户名称,产品名称,数量描述,记录ID" --out out\q.csv

# 只看命中数，不写文件
.\.venv\Scripts\erp.exe query --where "报价状态~需求审批" --dry-run

# 导出的 CSV 可直接喂回 quote（已兼容 `记录ID` 列）拉取完整详情
.\.venv\Scripts\erp.exe quote --csv out\q.csv --out data\detail.jsonl

# 只知道【表单编号】时，一步拿详情（自动用单号里的日期反查记录ID）
.\.venv\Scripts\erp.exe quote --bdbh BJXQ261105013 --out data\one.jsonl
.\.venv\Scripts\erp.exe quote --bdbh BJXQ261105013,BJXQ261105012 --out data\two.jsonl

# 报价需求：从 CSV 全量采集（18496 条，约 2 小时，可中断续跑）
.\.venv\Scripts\erp.exe quote --csv orders.csv --out data\quote.jsonl

# 报价需求：抽样验证
.\.venv\Scripts\erp.exe quote --csv orders.csv --limit 50 --out data\sample.jsonl

# 考勤：某月、多人
.\.venv\Scripts\erp.exe attendance --month 2026-11 --emp 21146 --out out\att.md
```

Python 里用：

```python
from erp_tools.api import connect, fetch_quote, fetch_quote_by_bdbh, fetch_attendance, query_quotes

auth = connect(username="21146", password="***")
rows = query_quotes(auth, from_date="2026-11-01", where="报价状态~需求审批")
only_by_code = fetch_quote_by_bdbh(auth, "BJXQ261105013")   # 用表单编号取详情
data = fetch_quote(auth, "33e3099b-f9bc-4c36-925d-4b10fff7284a")
att = fetch_attendance(auth, "2026-11")
```

## 分发给别人（打包 / 安装包）

```powershell
.\scripts\build.ps1                  # 测试 → dist\erp-tools\erp.exe → dist\erp-tools-0.3.0-setup.exe
.\scripts\build.ps1 -SkipInstaller   # 只出可执行目录，不编安装包
```

- **PyInstaller** 只负责把 Python 打成 `erp.exe`（onedir，约 32 MB），
  「选择安装目录 + 快捷方式 + 卸载」是 **Inno Setup**（`packaging/installer.iss`）做的，
  没装 Inno 时脚本会自动跳过并提示 `winget install -e --id JRSoftware.InnoSetup`。
- 安装器默认 `PrivilegesRequired=lowest`（免 UAC），默认目录 `%LocalAppData%\Programs\erp-tools`，
  向导里可自选任意目录。
- **数据不写安装目录**：打包运行（`sys.frozen`）时 `.env`、`cookies/`、QXID 缓存全部落到
  `%APPDATA%\erp-tools`，所以装到 `Program Files` 也能量存登录态；`erp config` 可查实际路径。
- **随包携带配置**：`packaging/config.example.yaml` 会被打成 `dist\erp-tools\config.yaml`，
  安装后就在 exe 旁边；想预填账号密码再分发，就在构建前放一份 `packaging\config.local.yaml`
  （已 gitignore），构建脚本会优先用它。用户级 `%APPDATA%\erp-tools\config.yaml` 优先级更高，
  用户可自行改而不影响安装目录。
- 首次使用：安装包完成后自动弹 `erp.exe setup` 向导（地址/工号/密码），登录一次即缓存 cookie。

详细说明（目录结构、体积、代码签名、升级安装）见 [packaging/README.md](packaging/README.md)。

## 常见问题（登录失败 / 500）

先跑自检，它会打印实际配置来源、端口连通性、登录页/验证码状态与 cookie 有效性：

```powershell
erp doctor
```

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `500 回发或回调参数无效…事件验证` | 登录 POST 少了 `__EVENTVALIDATION`（已修复） | 升级到当前版本 |
| `Max retries exceeded … too many 500 error responses` | 旧版把 500 当可重试错误，真正原因被吞了 | 同上；新版不再重试 500，会直接显示服务端报错 |
| 验证码识别失败 | OCR 模型未随包或图片异常 | `erp doctor` 会打印识别结果；失败时自动转为人工输入 |
| `登录被拒绝（HTTP 500）` | 账号密码/ERP 地址不对，或账号被锁 | `erp config` 核对 host/账号；用 `--host` 临时试另一台 |
| `无法获取用户 ID（cookie 不完整）` | 登录没真正生效（302 不代表成功），或服务端未下发 `LoginUserID` | 0.2.2 起会校验会话；拿不到 uid 时 QXID 自动回退默认值（873/876/2553），业务页靠会话照常工作 |
| 考勤查不到人 | 账号数据权限只到本部门 | 换有权限的账号：`erp attendance --emp 工号:密码` |

登录协议固定携带的隐藏字段（缺一不可）：`__VIEWSTATE`、`__VIEWSTATEGENERATOR`、`__EVENTVALIDATION`。

## 工具文档

| 工具 | 命令 | 说明 | 文档 |
| --- | --- | --- | --- |
| `query` | `erp query` | 客户报价需求列表查询 / 导出 CSV | [quote_index/README.md](src/erp_tools/tools/quote_index/README.md) |
| `quote` | `erp quote` | 报价需求详情 → ERP 订单需求 JSON | [quote_request/README.md](src/erp_tools/tools/quote_request/README.md) |
| `attendance` | `erp attendance` | 月度考勤明细 → Markdown / JSONL | [attendance/README.md](src/erp_tools/tools/attendance/README.md) |

每份文档都包含：运行命令、全部选项（含默认值）、输出格式与字段说明、Python 调用方式、已知限制。
新增工具时请在对应目录下同样补一份 `README.md`。

## 设计要点（都是踩坑换来的）

| 决策 | 原因 |
| --- | --- |
| **认证统一前置** | 报价需求页目前**不带 Cookie 也能读**（仅靠 URL 里的 `LoginUserID`），但一旦 ERP 收紧校验就会全线失效，所以统一先登录。 |
| **QXID 运行时探测** | 权限号跟账号角色绑定，写死 `876/2553` 换个人就错。登录后 GET `indexAjax.ashx?FunName=GetMenuTree` 解析菜单，取「页面路径 → QXID」。注意该接口**必须用 GET**，POST 返回空。 |
| **全局限流** | 实测 ERP 约 4~8 并发即饱和（8/16 线程时单请求从 ~0.95s 涨到 ~1.6s，吞吐不再上升）。并发上限属于「ERP 实例」，不能每工具各开一个线程池。 |
| **JSONL + 主键续跑** | 每条记录一个 JSON 文件会产生 18496 个目录，重跑时无法判断完成情况；JSONL 追加写 + 按 `id` 去重，天然支持中断续跑。 |
| **夹具回归测试** | 页面改版过去只能靠跑很久才发现。现在 `tests/fixtures/` 存真实页面，解析器改动先跑单测。 |
| **`Connection: close`** | 老旧 IIS 7.5 上 Keep-Alive 极易导致连接池超时。 |

## 实测协议速查

**登录**：`GET /Admin/Login.aspx` → 验证码在**另一个端口**（8889 → 8887）→ `POST` → 302 即成功。
Cookie 值是「带前缀」的：`LoginUserID=LoginUserID=2474`，真实 ID 要 `split("=")` 取右半段。

**报价需求（详情）**：`/Admin/BaoJia/BJ_KHBJXQ/Add.aspx?id=<GUID>&Option=3&QXID=873&MenuTree=1`
（`873` = 新增/查看页；`876` 是列表页 `Index.aspx` 的权限号）

**报价需求（列表）**：`/Admin/BaoJia/BJ_KHBJXQ/Index.aspx?QXID=876&IsAdd=1&LoginUserID=<uid>`
- 表单不硬编码：先 GET 页面，把整张 form1 快照下来再覆盖条件（日期/关键字/页大小）。
- 结果在 `div#gViewDiv > table#gridView`：第 1 行 `tr.FixedTitleRow` 为 38 列表头，与数据行一一对应。
- **主记录 ID** 在 `<tr ondblclick="OnDBClickOpenData('<ID>','876',3)">`；行内还有子表 GUID，
  不能按「第一个 GUID」取（实测第一个是子表 ID，打不开详情页）。
- 分页可用 `__EVENTTARGET=btnNext` 全量回发（本页实测有效），页大小调大通常一页取全。
- 服务端只支持「日期 + 两个关键字」，多条件用 `--where` 在本地过滤。
- **日期范围必填**：把 `txtKSRQ/txtJZRQ` 留空时，服务端会自己套默认区间并**忽略关键字**
  （实测传空日期 + 任意单号都返回 68 条近期数据）。所以按单号查必须先定日期；
  单号本身编码了制单日期（`BJXQ` + `YYMMDD` + 流水号），`--bdbh` 就是据此自动定位。
- 查不到时 ERP 会返回**一行全空占位**（`OnDBClickOpenData('')`），解析时已丢弃。

**考勤**：`/Admin/RenShi/RS_KQDA_M/Add.aspx?QXID=2553&MenuTree=1&LoginUserID=<uid>`
- `txtBMMC_Data` 必须提交**二次编码**后的 `%3Cr%3E%3Cc%3E%3C%2Fc%3E%3C%2Fr%3E`；
  直接提交 `<r><c></c></r>` 会被 ASP.NET request validation 判为「潜在危险的 Request.Form 值」→ 500。
- `lblgVQXBH` 必须等于当前页 QXID。
- 结果在 `div#gViewDiv2 > table#gridView`；`lblRowsCount / lblPageCount / lblCurrentPage` 给出总数与分页。

## 翻页

两层策略：

1. **首选**（已实测）：查询时把 `txtEvenPageDataSize` 直接设大（默认 5000），一次拿全 → `lblPageCount == 1`。
2. **兜底**：若仍多页，用 `__EVENTTARGET=btnNext` 逐页回发；该分支在部分环境返回
   `500 未将对象引用设置到对象的实例`，此时会明确抛错（而不是静默截断），提示调大页大小。

## 已知限制

- 报价需求页解析依赖中文标签定位；ERP 若改文案需同步更新 `tools/quote_request/parser.py` 的 `PRODUCT_FIELD_LABELS` / `TEXTAREA_LABELS`，并补夹具。
- 考勤页面的「查询范围」受账号数据权限限制（当前账号默认只看到本部门/本人），不是工具问题。
- 列表页只能查到**库里仍在**的单据：实测 `BJXQ210818002-02`（2021-08）在 2021-08-18 查不到，
  该日期只返回空占位行；`orders.csv` 里 2021~2022 的单号可能已被归档，不能当测试样本。
- 旧脚本（`erp_script/`、根目录 `main.py`）已删除；仅保留三份原始抓包在 `archive/raw-pages/`，用于协议对照：
  `attendance-add-page.html`（考勤页 DOM）、`attendance-curl.txt`（完整回发表单）、`attendance-sample-output.md`（输出样例）。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

夹具来源：

| 文件 | 内容 |
| --- | --- |
| `tests/fixtures/quote_page.html` | 真实报价需求单页面 |
| `tests/fixtures/attendance_page.html` | 真实考勤查询结果（5 行、单页） |
| `tests/fixtures/menu_tree.js` | 真实菜单接口响应（QXID 探测样本） |
| `tests/fixtures/quote_list_page.html` | 列表页真实表头 + 3 行数据（裁剪掉 VIEWSTATE） |
