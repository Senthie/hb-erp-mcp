# MCP 服务（api-key 网关 + Streamable HTTP）

给「多人共用一个部署」和「AI 客户端调用」用。**一个进程同时提供 HTTP 认证和 MCP**，
与 CLI 共用同一份 `core/` + `tools/` 页面逻辑。

- 本机跑批 → [CLI](cli.md)
- 嵌进别的程序 → [Python API](api.md)

```
            ┌───────────────── erp serve（一个进程）────────────────┐
CLI ────────┤ core/*  + tools/*  ← 同一份请求 / 解析 / 并发逻辑        │
            │                                                     │
curl ──────►│ /v1/auth/login  → 换取 api-key                       │
MCP 客户端 ─►│ /mcp            → Streamable HTTP（必须带 Bearer）    │
            └─────────────────────────────────────────────────────┘
```

## 1. 安装与启动

```powershell
uv sync --extra server                 # mcp + uvicorn；只用 CLI 不需要
.\.venv\Scripts\erp.exe serve          # 默认 127.0.0.1:8765
```

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `--bind` | `127.0.0.1:8765` | 监听地址；非回环必须配 TLS 或 `--allow-plaintext` |
| `--db` | `<数据目录>/auth.db` | api-key 库（SQLite） |
| `--global-concurrency` | `6` | 全局 ERP 并发闸门（实测 4~8 饱和） |
| `--tls-cert` / `--tls-key` | 空 | TLS 证书/私钥（团队模式必填，成对出现） |
| `--allow-host` | 空 | MCP DNS-rebinding 白名单，可重复，写 `host:port` |
| `--trust-proxy` | 关 | 信任 `X-Forwarded-For`（放在反向代理后面时开） |
| `--allow-plaintext` | 关 | 显式允许「非回环 + 明文 HTTP」，**只在 TLS 由上游终止时用** |

对应环境变量：`ERPSRV_BIND` / `ERPSRV_DB` / `ERPSRV_CONCURRENCY` / `ERPSRV_TLS_CERT` /
`ERPSRV_TLS_KEY` / `ERPSRV_ALLOW_HOST` / `ERPSRV_TRUST_PROXY` / `ERPSRV_ALLOW_PLAINTEXT`。

## 2. 换 api-key

**密码是每个使用者自己的，不进 `.env`、不进容器、不落盘。** 谁要用，谁用自己的
ERP 工号/密码换一把自己的 key：

```powershell
# 推荐：交互式输入密码（只用于这一次请求，不会留在 shell 历史里）
erp key --server http://127.0.0.1:8765 --user 21146
# → stdout 只输出 erp_xxx

# 自签证书没装进系统信任时，指向 CA 或临时 --insecure
erp key --server https://erp-tools.lan:8443 --cacert certs\erp-tools.crt
```

也可以直接打 HTTP：

```powershell
$r = Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8765/v1/auth/login `
     -ContentType application/json `
     -Body '{"username":"21146","password":"***"}'
$r.api_key      # erp_xxx —— 只显示这一次，库里只存 sha256
```

生命周期（重要）：

| 事件 | 结果 |
| --- | --- |
| 签发 | **不设过期时间** |
| **ERP 会话过期** | api-key **一并作废**（`401 erp_session_expired`），需重新登录 |
| 服务进程重启 | 仍有效（会话从 `cookies/<工号>.json` 恢复） |
| `POST /v1/auth/logout` | 只撤当前这把，同账号其它设备/AI 不受影响 |

- **密码不落盘**：只在登录请求的调用栈里存在，成功后立刻丢弃；`auth.db` 里只有
  key 的 sha256 与工号，**没有密码列**。
- 多个人/多个 AI 客户端 = 多把 key，可分别撤销；同一 ERP 账号的多把 key 共用一份 ERP 会话。
- `ERP_USERNAME` / `ERP_PASSWORD` 环境变量**只给 CLI 用**；`erp serve` 完全不读，
  配了会打印一条提示建议删掉。

## 3. MCP 客户端配置

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

`erp-mcp` 是 `erp serve` 的别名，方便被客户端直接拉起当入口。

## 4. 工具清单

14 个工具，全部要求 `Authorization: Bearer <api-key>`。
列表类结果默认 50 条、上限 500 条，超出时 `truncated=true`。

| 工具 | 写库 | 说明 |
| --- | --- | --- |
| `erp_whoami` | — | 当前身份 / 会话状态；`erp_alive=false` 表示会话过期 |
| `erp_capabilities` | — | 当前账号能访问哪些页面（`missing_pages` 为空即齐全） |
| `erp_quote_list` | — | 报价需求列表查询（日期区间 + 一个关键字，其余条件本地过滤） |
| `erp_quote_detail` | — | 单条报价需求详情（ERP 订单需求字段） |
| `erp_attendance` | — | 月度考勤明细 |
| `erp_quote_new_form` | — | 空白页结构：表头字段、只读标记、三个下拉、textarea 列表 |
| `erp_quote_options` | — | 业务员 / 跟单员 / 单价体系选项 + `employees` 合并名单 |
| `erp_quote_customer_search` | — | 客户检索（返回 `customer_id` 与该客户默认业务员/折扣率） |
| `erp_quote_prepare` | — | 校验并预览将要提交的字段（不会落库） |
| `erp_quote_create` | ✅ | 新建单据：主表 + 1 条产品明细（**含产品数量**） |
| `erp_quote_details` | — | 列明细行，拿 `cpid`（改/删该行要用） |
| `erp_quote_update` | ✅ | 改表头（备注/联系人/电话/手机/传真/折扣率/产品描述/业务员/跟单员） |
| `erp_quote_update_product` | ✅ | 改产品行（名称/规格/数量） |
| `erp_quote_delete` | ✅ | 删单据（不可恢复） |

**写入工具全部默认 `confirm=false` 只预演**，必须显式 `confirm=true` 才落库，
并且都会**写后回读校验**，不符时返回 `status=partial` / `failed`，不会假报成功。

典型写入链路：

```
erp_quote_prepare            预览校验
  → erp_quote_create         落库（回读 CPMC + txtCPSL）
  → erp_quote_details        拿 cpid
  → erp_quote_update(_product) 改
  → erp_quote_delete         删（回读 lblID 为空）
```

字段细节与踩坑记录见
[quote_create/README.md](../src/erp_tools/tools/quote_create/README.md)。

## 5. 让其他设备访问（团队模式，必须 TLS）

非回环地址若缺 TLS（且没开 `--allow-plaintext`）或缺 `--allow-host`，**直接拒绝启动**。

### 5.1 生成证书

```powershell
# 自动收集本机 IPv4；也可显式指定要写进 SAN 的 IP / 域名
.\.venv\Scripts\python.exe scripts\make-cert.py `
    --host 192.168.1.226 --host erp-tools.lan --out-dir certs
```

产出 `certs/erp-tools.crt` + `certs/erp-tools.key`（PEM，无口令）。
证书自签且带 `CA:TRUE`，可直接当「受信任的根证书」分发。
**SAN 必须写全客户端实际使用的地址**，否则 Node 客户端报 `ERR_TLS_CERT_ALTNAME_INVALID`。

### 5.2 启动

```powershell
erp serve --bind 0.0.0.0:8765 `
          --tls-cert certs\erp-tools.crt --tls-key certs\erp-tools.key `
          --allow-host 192.168.1.226:8765 `
          --allow-host erp-tools.lan:8765 `
          --db auth.db
```

`--allow-host` 是 MCP 的 Host 白名单，**逐个写 `host:port`**，客户端可能用的每种写法都要列。

### 5.3 放行防火墙

```powershell
# 管理员 PowerShell；-Profile Private = 只在「专用网络」生效
New-NetFirewallRule -DisplayName "erp-tools MCP 8765" -Direction Inbound `
  -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private

Get-NetTCPConnection -LocalPort 8765 -State Listen
Test-NetConnection -ComputerName 192.168.1.226 -Port 8765   # 在**另一台**设备上跑
```

### 5.4 在别的设备上验证

```powershell
# 1) 健康检查（无需 api-key，属 PUBLIC_PATH）
curl.exe --cacert erp-tools.crt https://192.168.1.226:8765/healthz
# → {"status":"ok","version":"...","keys":0,"global_concurrency":6}

# 2) 换一把 api-key
curl.exe --cacert erp-tools.crt -X POST https://192.168.1.226:8765/v1/auth/login `
  -H "content-type: application/json" `
  -d "{\"username\":\"21146\",\"password\":\"<ERP密码>\"}"

# 3) 用 key 调 MCP
curl.exe --cacert erp-tools.crt -X POST https://192.168.1.226:8765/mcp `
  -H "Authorization: Bearer erp_xxx" `
  -H "content-type: application/json" `
  -H "accept: application/json, text/event-stream" `
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"curl\",\"version\":\"0\"}}}"
```

### 5.5 让客户端信任自签证书

不装证书也能用，但每次都得关校验（`curl -k`、`NODE_TLS_REJECT_UNAUTHORIZED=0`），**只适合临时调试**。

| 环境 | 做法 |
| --- | --- |
| Windows | 管理员：`Import-Certificate -FilePath erp-tools.crt -CertStoreLocation Cert:\LocalMachine\Root` |
| macOS | 钥匙串 → 系统 → 拖入 crt → 双击设为「始终信任」 |
| Linux | `sudo cp erp-tools.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates` |
| Node 系客户端（VS Code MCP / Cursor） | `setx NODE_EXTRA_CA_CERTS "C:\path\erp-tools.crt"`，然后**重启**客户端 |
| Python 客户端（requests/httpx） | `REQUESTS_CA_BUNDLE` 或 `SSL_CERT_FILE` 指向 crt |

### 5.6 放在反向代理后面

```powershell
erp serve --bind 127.0.0.1:8765 --trust-proxy `
          --allow-host erp-tools.lan:8443        # 对外访问用的 host:port
```

- 回环地址不需要 `--tls-*`，也不需要 `--allow-plaintext`（TLS 在代理层）。
- 但 **`--allow-host` 仍然要填**：MCP 拿 `Host` 逐字比对白名单，不填会退化成
  「只认 `localhost:*` / `127.0.0.1:*`」，外部域名访问会收到 `421`。
- 如果监听的不是回环地址（例如容器里 `--bind 0.0.0.0:8765`），必须显式加
  `--allow-plaintext`（`ERPSRV_ALLOW_PLAINTEXT=1`），启动时打印一条 WARN。
- `--trust-proxy` 不开的话，限流会把所有客户端都当成代理的 IP。

### 5.7 排错对照

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 起不来：必须同时提供 `--tls-cert` / `--tls-key` | bind 非回环但没给 TLS | 按 5.1 生成证书，或显式 `--allow-plaintext` |
| 起不来：必须显式给出 `--allow-host` | 团队模式缺白名单 | `--allow-host <host>:<port>`，IP/域名各一条 |
| 另一台设备连不上 | 防火墙 / 不同网段 / 绑了 127.0.0.1 | 放行 8765；`--bind 0.0.0.0` |
| `421 Invalid Host header` | 请求的 `Host` 不在白名单 | 把客户端实际用的 `host:port` 加进去（带正确 key 时才走到这一步） |
| `self signed certificate` / `ERR_TLS_CERT_ALTNAME_INVALID` | 没装 CA 或 SAN 不含该地址 | 装 `erp-tools.crt`；重签时补 `--host` |
| `401 missing_bearer` / `invalid_api_key` | 没带 key / key 传错 | 先 `POST /v1/auth/login` 拿 key |
| `401 erp_session_expired` | 该 key 的 ERP 会话过期 | 重新登录换新 key（服务端不存密码） |
| `502 erp_unreachable` | 网关连不上 ERP | 查 `ERP_HOST` 与网络；这**不是**账号问题，不消耗锁定次数 |

## 6. Docker + nginx

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | 两阶段构建（uv 装依赖 → slim 运行时），非 root，`/data` 挂卷 |
| `.dockerignore` | 排除 `.env` / `cookies` / `certs` / 业务数据，**凭据不进镜像** |
| `docker-compose.yml` | `app`（MCP）+ `nginx`（TLS 终止） |
| `docker/nginx.conf` | 80→443 跳转 + 443 SSL 反代（SSE 不缓冲、Host 原样透传） |
| `docker/nginx-http.conf` | 纯 HTTP 内网版（临时验证用，**明文传 api-key**） |

```mermaid
flowchart LR
    C["MCP 客户端<br/>Bearer erp_xxx"] -->|HTTPS 8443| N["nginx<br/>TLS 终止"]
    N -->|HTTP 8765<br/>docker 内网| A["erp-tools 网关<br/>/mcp + /v1/*"]
    A -->|HTTP| E["ERP<br/>192.168.1.199:8889"]
```

```powershell
# 1) .env 只写连接信息（ERP_PASSWORD 请不要写）
Copy-Item .env.example .env
#    ERP_HOST=http://192.168.1.199:8889
#    ERP_PUBLIC_HOST=erp-tools.lan:8443

# 2) 证书 + 起容器
.\.venv\Scripts\python.exe scripts\make-cert.py --host erp-tools.lan --host 192.168.1.226 --out-dir certs
docker compose up -d --build

# 3) 每个人 / 每台 AI 各自的 key
.\.venv\Scripts\erp.exe key --server https://erp-tools.lan:8443 --user 21146

# 4) 验证
curl.exe --cacert certs\erp-tools.crt https://localhost:8443/healthz
```

**为什么容器里没有密码**

- compose **不用 `env_file`**，只显式传 `ERP_HOST` 这类非敏感项；
  `.env` 里的 `ERP_PASSWORD` 不会被注入容器。
- 登录发生在 `/v1/auth/login` 那一刻，密码只存在于该请求的调用栈。

**必须对齐的一处配置**

```ini
ERP_PUBLIC_HOST=erp-tools.lan:8443     # = 客户端实际访问的 host:port
```

compose 会注入成 `ERPSRV_ALLOW_HOST`。nginx 用 `proxy_set_header Host $http_host;`
原样透传（**带端口**），应用侧逐字比对：

- 用 `https://192.168.1.226:8443/mcp` 访问 → 白名单里也要有它，
  多个地址逗号分隔：`ERP_PUBLIC_HOST=erp-tools.lan:8443,192.168.1.226:8443`。
- 写成 `$host` 会丢端口 → 客户端一律收到 `421`。

**其它要点**

- **数据卷** `erpdata:/data` 存 `auth.db`、`cookies/`、`qxid-<工号>.json`；删容器不丢登录态。
- **不发布 app 端口**：`app` 只 `expose: 8765`，宿主机只能通过 nginx 访问。
- **`ERPSRV_TRUST_PROXY=1` 必须开**（镜像里已设），否则限流按 nginx 的 IP 统计。
- **换纯 HTTP**：`.env` 加 `NGINX_CONF=docker/nginx-http.conf`，并把
  `ERP_PUBLIC_HOST` 改成 `erp-tools.lan:8080`。
- **端口冲突**：改 `ERP_HTTP_PORT` / `ERP_HTTPS_PORT`（默认 8080 / 8443）。

## 7. 错误码

| 状态 | `error` | 含义 |
| --- | --- | --- |
| 400 | `bad_request` | 请求体缺字段 |
| 401 | `invalid_credentials` | 账号或密码错（统一文案，不区分原因） |
| 401 | `missing_bearer` / `invalid_api_key` / `api_key_revoked` | key 缺失 / 不存在 / 已撤销 |
| 401 | `erp_session_expired` | ERP 会话过期，key 已一并作废 |
| 423 | `locked` | 同一账号连续失败 10 次，锁 15 分钟 |
| 429 | `rate_limited` | 登录限流（5 次/分钟） |
| 502 | `erp_unreachable` | 连不上 ERP（网络问题，不消耗锁定次数） |

安全设计：登录失败统一文案 + 恒定耗时哈希比对；限流按 **IP + 账号** 双维度；
api-key 只存 sha256，日志经 `SensitiveFilter` 脱敏。

## 8. 已知限制

- **api-key 无过期时间**：寿命等于绑定的 ERP 会话；主动作废用 `POST /v1/auth/logout`。
- **ERP 会话过期后无法自动续期**：服务端不保存密码，只能重新登录。
- **限流与锁定是进程内内存态**：重启即清零，当前不支持多实例横向部署（需要时换 Redis）。
- **全局并发默认 6**：进程级闸门，所有用户共享；调大只会让单请求变慢。
- **一个进程一套会话池**：同一 ERP 账号的多个 key 共用一份 ERP 会话。
- 写入能力的两处缺口（产品类别、多语言数量明细）见
  [quote_create/README.md](../src/erp_tools/tools/quote_create/README.md) 的「已知限制」。
- 同一个 ERP 可能有多**端口实例**（实测 `8889` 与 `8890` 数据不互通），
  所有读写都落在 `ERP_HOST` 指向的那个实例上。
