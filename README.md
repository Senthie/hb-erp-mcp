# erp-tools — 华博印刷 ERP 工具聚合

把过去散落在各脚本里的 ERP 逆向成果（登录、权限号、报价需求页、考勤页）收敛成一个
**可扩展的工具聚合项目**。同一份页面逻辑（`core/` + `tools/`）对外提供三种入口：

| 入口 | 用法 | 适用场景 |
| --- | --- | --- |
| **CLI** | `erp <工具> [选项]` | 本机跑批、导出 CSV / JSONL |
| **Python API** | `from erp_tools.api import ...` | 嵌进别的程序 |
| **MCP 服务** | `erp serve` → `/mcp` | AI 客户端（VS Code / Cursor）调用 |

新增一个业务工具只需在 `tools/` 下加一个目录，三种入口自动都能用上。

```
src/erp_tools/
├─ core/           通用能力（与业务无关）
│  ├─ config.py      环境/.env 配置；主机端口、并发、QXID 全部可配，无硬编码
│  ├─ session.py     线程内 Session、Retry、Connection:close、Cookie 快照共享
│  ├─ limiter.py     实例级全局限流（并发上限 + 最小间隔）
│  ├─ auth.py        登录、验证码、Cookie 持久化、会话校验、QXID 自动探测
│  ├─ aspnet.py      __VIEWSTATE 提取、PostBack 组装、ScriptManager delta 拆解
│  ├─ htmlutil.py    「标签→textarea」、td 取值、数量解析等页面工具
│  ├─ errors.py      可重试/不可重试的异常体系
│  ├─ security.py    api-key 生成/校验（sha256）+ 日志脱敏
│  └─ ratelimit.py   令牌桶（每 IP / 每账号）+ 登录失败锁定
├─ tools/          业务工具（每个目录内都有 README.md：命令 / 选项 / 输出格式）
│  ├─ base.py        Tool 契约：options() + run() -> 记录流
│  ├─ registry.py    注册表（新增工具只改这一处）
│  ├─ quote_index/   列表查询 + CSV 导出（BJ_KHBJXQ/Index.aspx）
│  ├─ quote_request/ 报价需求详情（BJ_KHBJXQ/Add.aspx）
│  ├─ quote_create/  新增报价需求（读表单/下拉/客户/预览 + 写新增/改表头/改产品行/删除）
│  └─ attendance/    考勤（RS_KQDA_M/Add.aspx）
├─ runtime/        服务端运行时
│  ├─ auth_store.py   api-key 的 SQLite 存储（只存哈希，无密码列）
│  ├─ sessions.py     每用户会话池 + 全局并发闸门
│  └─ context.py      contextvars：把当前身份传给工具
├─ apps/           服务端入口
│  ├─ server/         认证网关（/healthz、/v1/*）+ 挂载 /mcp
│  └─ mcp/            MCP 工具注册（14 个工具，Streamable HTTP）
├─ output/         jsonl（断点续跑）+ csv + markdown
├─ cli.py          `erp <工具> [选项]`
└─ api.py          库入口

packaging/        PyInstaller 配置 + Inno Setup 安装器脚本（见 packaging/README.md）
scripts/build.ps1 一键打包：测试 → exe → 安装包
```

仓库根：

```
octpus-erp/
├─ src/erp_tools/       源码（见上）
├─ tests/fixtures/      解析器回归用真实页面
├─ archive/raw-pages/   历史抓包（仅协议对照，不参与运行）
├─ packaging/           PyInstaller + Inno Setup 打包脚本
├─ scripts/build.ps1    一键打包：测试 → exe → 安装包
├─ scripts/make-cert.py 生成自签 TLS 证书（团队模式 / Docker 用）
├─ Dockerfile           容器镜像（app：CLI + MCP）
├─ docker-compose.yml   编排：app + nginx（TLS 终止）
├─ docker/              nginx 反代配置（HTTPS / 纯 HTTP 两套）
├─ certs/               make-cert.py 输出（已 gitignore）
├─ orders.csv           报价采集输入（18496 条）
├─ data/                旧版脚本产出（每条一个 JSON 文件，保留）
├─ auth.db              服务端 api-key 库（运行时生成，已 gitignore）
└─ cookies/             登录态缓存（不进版本库）
```

## 快速开始

```powershell
uv sync --extra dev --extra server                   # 核心依赖（含验证码自动识别）
Copy-Item packaging\config.example.yaml config.yaml  # 填 ERP 地址 / 工号 / 密码

.\.venv\Scripts\erp.exe setup                       # 或跑向导：生成配置 + 缓存 cookie
```

`--extra server` 只在要用 MCP 服务时需要（会装 `mcp` + `uvicorn`）；只用 CLI 可以省掉。

装好后建议先跑这两步确认环境：

```powershell
.\.venv\Scripts\erp.exe doctor     # 端口 / 登录页 / 验证码 / OCR / cookie 有效性
.\.venv\Scripts\erp.exe login      # 登录并缓存 cookie + 探测 QXID
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

## 服务端模式（api-key + MCP）

给「多人共用一个部署」和「AI 客户端调用」用。**一个进程同时提供 HTTP 认证和 MCP**，
三个入口（CLI / HTTP / MCP）共用同一份 `core/` + `tools/` 页面逻辑。

```
            ┌───────────────── erp serve（一个进程）────────────────┐
CLI ────────┤ core/*  + tools/*  ← 同一份请求 / 解析 / 并发逻辑        │
            │                                                     │
curl ──────►│ /v1/auth/login  → 换取 api-key                       │
MCP 客户端 ─►│ /mcp            → Streamable HTTP（必须带 Bearer）    │
            └─────────────────────────────────────────────────────┘
```

```powershell
uv sync --extra server                  # 只用 CLI 不需要装
.\.venv\Scripts\erp.exe serve          # 默认 127.0.0.1:8765
```

### 1. 换 api-key

**密码是每个使用者自己的，不进 `.env`、不进容器、不落盘。** 谁要用，谁用自己的
ERP 工号/密码换一把自己的 key：

```powershell
# 推荐：交互式输入密码（密码只用于这一次请求，不会留在 shell 历史里）
erp key --server http://127.0.0.1:8765 --user 21146
# → 打印 erp_xxx

# 自签证书没装进系统信任时，指向 CA 或临时 --insecure
.\.venv\Scripts\erp.exe key --server https://erp-tools.lan:8443 --cacert certs\erp-tools.crt
```

也可以直接打 HTTP：

```powershell
$r = Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8765/v1/auth/login `
     -ContentType application/json `
     -Body '{"username":"21146","password":"***"}'
$r.api_key      # erp_xxx —— 只显示这一次，库里只存 sha256
```

所以 `ERP_USERNAME` / `ERP_PASSWORD` 这两个环境变量**只给 CLI 自己用**
（`erp login/query/attendance` 直连 ERP 时需要）；`erp serve` 完全不读它们，
配了也只会收到一条提示「服务端不会使用它，建议删掉」。

生命周期（重要）：

| 事件 | 结果 |
|---|---|
| 签发 | **不设过期时间** |
| **ERP 会话过期** | api-key **一并作废**（`401 erp_session_expired`），需重新登录 |
| 服务进程重启 | 仍有效（会话从 `cookies/<工号>.json` 恢复） |
| `POST /v1/auth/logout` | 只撤当前这把，同账号其它设备不受影响 |

**密码不落盘**：登录只在这一个请求的调用栈里使用密码，成功后立刻丢弃；
所以 ERP 会话过期后无法自动续期，只能重新登录。

### 2. MCP 客户端（Streamable HTTP）

VS Code `mcp.json`：

```json
{
  "servers": {
    "erp": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp",
      "headers": { "Authorization": "Bearer erp_xxx" }
    }
  }
}
```

查询类工具：`erp_whoami` / `erp_capabilities` / `erp_quote_list` / `erp_quote_detail` / `erp_attendance`。
结果默认 50 条、上限 500 条，超出时 `truncated=true`。
`erp-mcp` 是这个服务的别名（等价于 `erp serve`），方便直接当 MCP 入口被客户端拉起。

**新增单据**：`erp_quote_new_form` / `erp_quote_options` / `erp_quote_customer_search` /
`erp_quote_prepare` 是只读的；`erp_quote_create` **会写入 ERP**（默认 `confirm=false` 只校验）。
主表与产品明细行（**含产品数量**）均可稳定写入；`create` 会在写后回读校验
（`CPMC` + `txtCPSL`），不符时返回 `status=partial` 并给出 `detail_error`，不假报成功。

修改与删除：`erp_quote_details`（列明细行拿 `cpid`）、`erp_quote_update`（改表头）、
`erp_quote_update_product`（改产品行）、`erp_quote_delete`（删单据）——**都默认 `confirm=false` 只预演**。
细节见 [quote_create/README.md](src/erp_tools/tools/quote_create/README.md)。

### 3. 让其他设备访问（团队模式，必须 TLS）

非回环地址若缺 TLS 或缺 `--allow-host`，**直接拒绝启动**——避免 api-key 明文裸奔。

#### 3.1 生成证书（自签，已内置脚本）

```powershell
# 自动收集本机 IPv4；也可以显式指定要写进 SAN 的 IP / 域名
.\.venv\Scripts\python.exe scripts\make-cert.py `
    --host 192.168.1.226 --host erp-tools.lan --out-dir certs
```

产出 `certs/erp-tools.crt` + `certs/erp-tools.key`（PEM，无口令）。
证书是自签的且带 `CA:TRUE`，所以可以直接当「受信任的根证书」分发。
**SAN 必须写全客户端实际使用的地址**：用 IP 访问就写 IP，用域名就写域名，
否则 Node 客户端会报 `ERR_TLS_CERT_ALTNAME_INVALID`。

#### 3.2 启动

```powershell
erp serve --bind 0.0.0.0:8765 `
          --tls-cert certs\erp-tools.crt --tls-key certs\erp-tools.key `
          --allow-host 192.168.1.226:8765 `
          --allow-host erp-tools.lan:8765 `
          --db auth.db
```

`--allow-host` 是 MCP 的 DNS-rebinding 白名单，**逐个写 `host:port`**，
要把客户端可能用的每一种写法都列上（IP 和域名各一条）。

也可以用环境变量代替命令行（写进 `.env` 即可）：

```ini
ERPSRV_BIND=0.0.0.0:8765
ERPSRV_TLS_CERT=certs\erp-tools.crt
ERPSRV_TLS_KEY=certs\erp-tools.key
ERPSRV_ALLOW_HOST=192.168.1.226:8765,erp-tools.lan:8765   # 逗号或分号分隔
```

#### 3.3 放行防火墙

```powershell
# 管理员 PowerShell；-Profile Private 表示只在「专用网络」生效
New-NetFirewallRule -DisplayName "erp-tools MCP 8765" -Direction Inbound `
  -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
```

网卡所在网络必须是「专用网络」，否则 `Private` 规则不生效。
确认监听与连通性：

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen
Test-NetConnection -ComputerName 192.168.1.226 -Port 8765   # 在**另一台**设备上跑
```

#### 3.4 在别的设备上验证

```powershell
# 1) 健康检查（无需 api-key，PUBLIC_PATH）
curl.exe --cacert erp-tools.crt https://192.168.1.226:8765/healthz
# → {"status":"ok","version":"...","keys":0,"global_concurrency":6}

# 2) 换一把 api-key（登录是唯一无需 key 的写接口）
curl.exe --cacert erp-tools.crt -X POST https://192.168.1.226:8765/v1/auth/login `
  -H "content-type: application/json" `
  -d "{\"username\":\"21146\",\"password\":\"<ERP密码>\"}"
# → {"api_key":"erp_xxx", ...}

# 3) 用 key 调 MCP
curl.exe --cacert erp-tools.crt -X POST https://192.168.1.226:8765/mcp `
  -H "Authorization: Bearer erp_xxx" `
  -H "content-type: application/json" `
  -H "accept: application/json, text/event-stream" `
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"curl\",\"version\":\"0\"}}}"
```

客户端 `mcp.json` 换成对端地址：

```json
{
  "servers": {
    "erp": {
      "type": "http",
      "url": "https://192.168.1.226:8765/mcp",
      "headers": { "Authorization": "Bearer erp_xxx" }
    }
  }
}
```

#### 3.5 让客户端信任自签证书

不装证书也能用，但每次都得关校验（`curl -k`、`NODE_TLS_REJECT_UNAUTHORIZED=0`），
**只适合临时调试**。正式做法是把 `erp-tools.crt` 装进各设备的受信任根：

| 环境 | 做法 |
| --- | --- |
| Windows | 管理员：`Import-Certificate -FilePath erp-tools.crt -CertStoreLocation Cert:\LocalMachine\Root` |
| macOS | 钥匙串 → 系统 → 拖入 crt → 双击设为「始终信任」 |
| Linux | `sudo cp erp-tools.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates` |
| Node 系客户端（VS Code MCP / Cursor） | `setx NODE_EXTRA_CA_CERTS "C:\path\erp-tools.crt"`，然后**重启**客户端 |
| Python 客户端（requests/httpx） | `REQUESTS_CA_BUNDLE` 或 `SSL_CERT_FILE` 指向 crt |

#### 3.6 排错对照

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 服务起不来，提示「必须同时提供 --tls-cert / --tls-key」 | bind 非回环但没给 TLS | 按 3.1 生成证书 |
| 服务起不来，提示「必须显式给出 --allow-host」 | 团队模式缺白名单 | `--allow-host <host>:<port>`，IP/域名各一条 |
| 另一台设备 `Test-NetConnection` 失败 | 防火墙 / 不同网段 / 绑了 127.0.0.1 | 放行 8765；`--bind 0.0.0.0` |
| `421 Invalid Host header` | 请求的 `Host` 不在 `--allow-host` 里 | 把客户端实际使用的 `host:port` 加进白名单（带正确 key 时才会走到这一步检查） |
| `self signed certificate` / `ERR_TLS_CERT_ALTNAME_INVALID` | 没装 CA，或证书 SAN 不含该地址 | 装 `erp-tools.crt`；重签时补 `--host` |
| `401 missing_bearer` / `invalid_api_key` | 没带 key / key 传错 | 先 `POST /v1/auth/login` 拿 key |
| `401 erp_session_expired` | 该 key 的 ERP 会话过期 | 重新登录换新 key（服务端不存密码，无法自动续期） |
| 放在 nginx/Caddy 后面，日志里 IP 全是 127.0.0.1 | 没开 `--trust-proxy` | 加 `--trust-proxy`（否则限流会按代理 IP 统计） |

放在反向代理后面时（代理做 TLS，应用只监听回环）：

```powershell
erp serve --bind 127.0.0.1:8765 --trust-proxy `
          --allow-host erp-tools.lan:8443        # 对外访问用的 host:port
```

- 回环地址不需要 `--tls-*`，也不需要 `--allow-plaintext`（TLS 在代理层）。
- 但 **`--allow-host` 仍然要填**：MCP 会拿 `Host` 逐字比对白名单，不填就退化成
  「只认 localhost:*/127.0.0.1:*」，外部域名访问会收到 `421`。
- 如果监听的不是回环地址（例如容器里 `--bind 0.0.0.0:8765`），必须显式加
  `--allow-plaintext`（`ERPSRV_ALLOW_PLAINTEXT=1`），启动时会打印一条 WARN。

### 4. Docker + nginx 转发

仓库自带一套可直接用的容器化配置：

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | 两阶段构建（uv 装依赖 → slim 运行时），非 root 运行，`/data` 挂卷 |
| `.dockerignore` | 排除 `.env`/`cookies`/`certs`/业务数据，**凭据不进镜像** |
| `docker-compose.yml` | `app`（MCP）+ `nginx`（TLS 终止）两个服务 |
| `docker/nginx.conf` | 80→443 跳转 + 443 SSL 反代（含 SSE 不缓冲、Host 原样透传） |
| `docker/nginx-http.conf` | 纯 HTTP 内网版（临时验证用，**明文传 api-key**） |

```mermaid
flowchart LR
    C["MCP 客户端<br/>Bearer erp_xxx"] -->|HTTPS 8443| N["nginx<br/>TLS 终止"]
    N -->|HTTP 8765<br/>docker 内网| A["erp-tools 网关<br/>/mcp + /v1/*"]
    A -->|HTTP| E["ERP<br/>192.168.1.199:8889"]
```

**三步启动**

```powershell
# 1) 根目录 .env 里**只写连接信息**（ERP_PASSWORD 请不要写，见下）
#    ERP_HOST=http://192.168.1.199:8889
#    ERP_PUBLIC_HOST=erp-tools.lan:8443
Copy-Item .env.example .env

# 2) 生成证书 + 起容器
.\.venv\Scripts\python.exe scripts\make-cert.py --host erp-tools.lan --host 192.168.1.226 --out-dir certs
docker compose up -d --build

# 3) 每个人（或每台 AI）用自己的工号密码换自己的 key
.\.venv\Scripts\erp.exe key --server https://erp-tools.lan:8443 --user 21146
```

**为什么容器里没有密码**

- compose **不用 `env_file`**，只显式传 `ERP_HOST` 这类非敏感项；
  `.env` 里的 `ERP_PASSWORD` 不会被注入容器。
- 登录发生在 `/v1/auth/login` 那一刻，密码只存在于该请求的调用栈；
  `auth.db` 里只有 api-key 的 sha256 与工号，没有密码列。
- 多个人/多个 AI 客户端就多把 key，各自可用 `POST /v1/auth/logout` 单独撤销；
  同一个 ERP 账号的多个 key 共用一份 ERP 会话（各自 key 独立）。

**验证**

```powershell
curl.exe --cacert certs\erp-tools.crt https://localhost:8443/healthz
# → {"status":"ok", ...}
```

客户端地址：`https://erp-tools.lan:8443/mcp`（自签证书要装进客户端信任，见 3.5）。

**必须对齐的一处配置**

```ini
ERP_PUBLIC_HOST=erp-tools.lan:8443     # = 客户端实际访问的 host:port
```

compose 会把它注入成 `ERPSRV_ALLOW_HOST`。nginx 用 `proxy_set_header Host $http_host;`
原样透传（**带端口**），应用侧逐字比对：

- 用 `https://192.168.1.226:8443/mcp` 访问 → 白名单里也要有 `192.168.1.226:8443`，
  多个地址用逗号分隔：`ERP_PUBLIC_HOST=erp-tools.lan:8443,192.168.1.226:8443`。
- 写成 `$host` 会丢端口 → 客户端一律收到 `421 Invalid Host header`。

**其它要点**

- **数据卷**：`erpdata:/data` 存 `auth.db`、`cookies/`、`qxid-<工号>.json`。
  删容器不丢登录态；备份这一个卷即可。
- **不发布 app 端口**：compose 里 `app` 只 `expose: 8765`，宿主机只能通过 nginx 访问。
- **`ERPSRV_TRUST_PROXY=1` 必须开**，否则限流会把所有客户端都当成 nginx 的 IP。
  镜像内 `ERPSRV_ALLOW_PLAINTEXT=1` 也已设好（TLS 在 nginx 上）。
- **换纯 HTTP**：在 `.env` 里加 `NGINX_CONF=docker/nginx-http.conf`，然后
  `ERP_PUBLIC_HOST=erp-tools.lan:8080`。
- **端口冲突**：改 `ERP_HTTP_PORT` / `ERP_HTTPS_PORT` 即可（默认 8080/8443，
  避开宿主机 80/443 的占用）。
- **镜像不含凭据**：`ERP_PASSWORD` 从 `.env` 注入；证书从 `./certs` 只读挂载。

### 5. 错误码

| 状态 | `error` | 含义 |
|---|---|---|
| 400 | `bad_request` | 请求体缺字段 |
| 401 | `invalid_credentials` | 账号或密码错（统一文案，不区分原因） |
| 401 | `missing_bearer` / `invalid_api_key` / `api_key_revoked` | key 缺失 / 不存在 / 已撤销 |
| 401 | `erp_session_expired` | ERP 会话过期，key 已一并作废 |
| 423 | `locked` | 连续失败 10 次，锁 15 分钟 |
| 429 | `rate_limited` | 登录限流（5 次/分钟） |
| 502 | `erp_unreachable` | 连不上 ERP |

### 6. 全局并发

默认 **6**（实测该 ERP 4~8 并发即饱和）。这是**进程级**闸门，所有用户共享；
调大只会让单请求变慢，可用 `--global-concurrency` 覆盖。

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

| 工具 | 命令 | MCP 工具名 | 说明 | 文档 |
| --- | --- | --- | --- | --- |
| `query` | `erp query` | `erp_quote_list` | 客户报价需求列表查询 / 导出 CSV | [quote_index/README.md](src/erp_tools/tools/quote_index/README.md) |
| `quote` | `erp quote` | `erp_quote_detail` | 报价需求详情 → ERP 订单需求 JSON | [quote_request/README.md](src/erp_tools/tools/quote_request/README.md) |
| `attendance` | `erp attendance` | `erp_attendance` | 月度考勤明细 → Markdown / JSONL | [attendance/README.md](src/erp_tools/tools/attendance/README.md) |
| （仅 MCP） | — | `erp_quote_new_form` / `erp_quote_options` / `erp_quote_customer_search` / `erp_quote_prepare` / `erp_quote_create` / `erp_quote_details` / `erp_quote_update` / `erp_quote_update_product` / `erp_quote_delete` | 新增报价需求：页面结构 / 下拉选项 / 客户检索 / 校验预览 / 新建（含数量）/ 列明细 / 改表头 / 改产品 / 删除 | [quote_create/README.md](src/erp_tools/tools/quote_create/README.md) |

MCP 另有 `erp_whoami`（身份/会话状态）与 `erp_capabilities`（当前账号可访问的页面）两个元信息工具。

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

**报价需求（新增/修改/删除）**：`Add.aspx?QXID=873`（不带 QXID → HTTP 500）；
（`873` = 新增/查看页；`876` 是列表页 `Index.aspx` 的权限号）
明细子页 `Add_CPZL.aspx?PID=<lblID>&ID=-1|<cpid>&ZKL=..&QXID=873&KHID=..&Modify=1`
- 【新增产品】`btn_SonNew` 的副作用是**先保存主表**（返回 `lblID` + `BDBH`），再打开明细子页。
- 明细子页必填链：产品类别 → 装订方式 → 款数 → 语言数 → **产品数量** → 包装方式 → 台板类型
  → 送货方式 → 交货日期 → 配件描述 → 产品属性 → 产品类型 → 预计下单日期 → 产品描述 → 修改/检测。
- **产品数量认 `txtCPSL`（隐藏汇总值），不是 `txtCPSL1`**：只发 `txtCPSL1` 会永远报
  `【产品数量】不能为空！`；`txtCPSL1..10` 只是「每款一行」的展示框。
- `form_snapshot` **必须连 textarea 一起快照**：配件描述/检测项目/产品描述都是 textarea，漏了就会被
  `【配件描述】不能为空！` 挡住（这也是 `update_product` 之前一直失败的真因）。
- 校验提示写在 `$.ligerDialog.error('…')` 里，不是 `alert(`；且**保存成功也可能残留旧提示**，
  判成败一律以回读为准。
- `txtCPLB` 是 ComponentArt 树形下拉，值由前端 JS 写进 `txtCPLB_Input`，服务端渲染只有占位符 →
  重新保存前需要从产品档案补回（否则报 `请选择【产品类别】的数据！`）。
- 修改表头：`Option=3` 只读打开 → `POST __EVENTTARGET=btnModify`（解锁 [保存]）→ `POST btnSave`。
- 删除：同 `Option=3` 页 → `POST __EVENTTARGET=btnDelete`；成功标志 = 再打开 `lblID` 为空。

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
- **ERP 会话过期后无法自动续期**：服务端不保存密码（安全取舍），只能重新调用 `/v1/auth/login`。
- **api-key 无过期时间**：它的寿命就等于绑定的 ERP 会话；要主动作废用 `POST /v1/auth/logout`。
- **cookie 以明文保存在 `cookies/<工号>.json`**：建议限制目录 ACL 为仅当前用户可读；更强的方案是接 DPAPI，暂未实现。
- **限流与锁定是进程内内存态**：重启即清零；当前不支持多实例横向部署（需要时会顶替为 Redis）。
- **新增/修改/删除报价需求已全部跑通**（2026-11-11 实测闭环：新建→改数量→改名称→删除）：
  主表、产品明细行、产品数量（`txtCPSL` 汇总值）都能落库，且写后一律回读校验。
- **产品类别（`txtCPLB` 树形下拉）写不进库**：显示文字由前端 JS 维护，服务端渲染只有占位符，
  重新保存时需要工具从产品档案临时补回才能通过校验；ERP 侧该字段仍为空。
- **多语言/多款数量明细（`CPSLID1..10`）不会自动建**：汇总数量正确，但行内「编辑产品数量」
  的多语言明细表为空。该明细要走 `btn_CPSLZL` 弹窗，而 ERP 的校验顺序（先验数量再开弹窗）
  在纯 HTTP 下闭合成环，需浏览器抓一次弹窗提交体才能定协议。
- **同一 ERP 可能有多端口实例**（实测 `8889` 与 `8890` 数据**不互通**）。所有写入/删除都会落在
  `ERP_HOST` 指向的那个实例上，切环境前先 `erp config` 确认。
- **字段级权限差异**：不同账号的页面上控件/列可能不存在。现有实现用「表单快照（列表页）+ 标签语义定位 + 多作用域回退」兜住常见差异；字段级 `_diagnostics` 诊断尚未输出（规划中），取不到时表现为该字段为 `null`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q     # 当前 137 项，全部离线（不连真实 ERP）
```

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_security.py` / `test_ratelimit.py` | api-key 生成校验、日志脱敏、令牌桶、登录锁定 |
| `test_auth_store.py` | api-key 签发 / 校验 / 撤销 / 重启后持久化 |
| `test_sessions.py` | 会话池复用、存活探测缓存、网络抖动不误判、全局并发闸门 |
| `test_server_auth.py` | `/v1/auth/*` 端到端：401/423/429/502、会话过期连带作废 key |
| `test_mcp_server.py` | MCP 握手、工具清单与 inputSchema、身份传递、撤销后 401 |
| `test_quote_create_parser.py` | 空白页/客户页/明细行解析、`prepare` 组装、下拉选项缓存、删除协议 |
| `test_quote_create_qty.py` | 产品数量回读（`txtCPSL` 优先、`txtCPSL1` 兜底）、单位后缀容错、改数量必带两个字段 |
| 其余 | 各页面解析器 + CLI 配置 + CSV 输出 |

夹具来源：

| 文件 | 内容 |
| --- | --- |
| `tests/fixtures/quote_page.html` | 真实报价需求单页面 |
| `tests/fixtures/attendance_page.html` | 真实考勤查询结果（5 行、单页） |
| `tests/fixtures/menu_tree.js` | 真实菜单接口响应（QXID 探测样本） |
| `tests/fixtures/quote_list_page.html` | 列表页真实表头 + 3 行数据（裁剪掉 VIEWSTATE） |
