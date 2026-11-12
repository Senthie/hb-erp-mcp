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

17 个工具，全部要求 `Authorization: Bearer <api-key>`。
列表类结果默认 50 条、上限 500 条，超出时 `truncated=true`。

| 工具 | 写库 | 说明 |
| --- | --- | --- |
| `erp_whoami` | — | 当前身份 / 会话状态；`erp_alive=false` 表示会话过期 |
| `erp_capabilities` | — | 当前账号能访问哪些页面（`missing_pages` 为空即齐全） |
| `erp_quote_list` | — | 报价需求列表查询（日期区间 + 一个关键字，其余条件本地过滤） |
| `erp_price_list` | — | **客户报价列表**（`BJ_KHBJ`，一行=一个报价产品行）：日期区间可选作用于制单/交货/离厂 + 服务端下拉过滤 + 本地 `where` |
| `erp_price_detail` | — | **客户报价详情**：表头 + 产品字段 + 数量明细 + 选配项目 + 部件明细(材料/尺寸/工序/辅料) + 费用 + 产品描述 + 制单审核 |
| `erp_price_node` | — | **子数据**：`quantities` 数量档 / `part` 部件 / `part_materials` 部件辅料 / `fees` 每个数量档的费项明细（纸张/辅料/印前/印刷/版费/印后/其他） |
| `erp_quote_detail` | — | 单条报价需求详情（ERP 订单需求字段） |
| `erp_attendance` | — | 月度考勤明细 |
| `erp_quote_new_form` | — | 空白页结构：表头字段、只读标记、三个下拉、textarea 列表 |
| `erp_quote_product_form` | — | **产品明细子页字段清单**（205 个控件，其中 51 个可上传）+ `upload` JSON Schema + `categories` 类别树 |
| `erp_quote_options` | — | 业务员 / 跟单员 / 单价体系选项 + `employees` 合并名单 |
| `erp_quote_customer_search` | — | 客户检索（返回 `customer_id` 与该客户默认业务员/折扣率） |
| `erp_quote_create` | ✅ | 新建单据：主表 + 1 条产品明细（**含产品数量**）。`confirm=false` 即预览 |
| `erp_quote_details` | — | 列明细行，拿 `cpid`（改/删该行要用） |
| `erp_quote_update` | ✅ | 改表头（备注/联系人/电话/手机/传真/折扣率/产品描述/业务员/跟单员） |
| `erp_quote_update_product` | ✅ | 改产品行（名称/规格/数量） |
| `erp_quote_delete` | ✅ | 删单据（不可恢复） |

**写入工具全部默认 `confirm=false` 只预演**，必须显式 `confirm=true` 才落库，
并且都会**写后回读校验**，不符时返回 `status=partial` / `failed`，不会假报成功。

典型写入链路：

```
erp_quote_product_form       看明细子页能填什么（控件名/别名/下拉值）
  → erp_quote_create(confirm=false)   预览校验（返回 ok/errors/warnings/header）
  → erp_quote_create(confirm=true)    落库（回读 CPMC + txtCPSL）
  → erp_quote_details        拿 cpid
  → erp_quote_update(_product) 改
  → erp_quote_delete         删（回读 lblID 为空）
```

### 参数 schema 怎么告诉模型

`tools/list` 的 `inputSchema` 由函数注解推导，所以参数的格式与示例**写在代码里**
（`apps/mcp/tools.py` 的 `Annotated[..., Field(...)]`），模型看到的就是这些信息。

* `products[]` 不是自由 dict：它引用 `apps/mcp/schemas.py` 里的 `QuoteProductInput`，
  后者又引用 `QuoteProductFields`——**51 个明细字段的类型/title/示例全部由
  `quote_create/fields.py` 的注册表生成**，与 `erp_quote_product_form` 返回的
  `upload.json_schema` 同源，不会两套漂移。
* 下拉选项写成 `examples` 而不是 `enum`：注册表里的 `options` 只是离线已知值，
  写死成枚举会把实际能通过的值在客户端判死。
* `inputSchema` 里会出现 `$defs` / `$ref`（MCP 用 JSON Schema 2020-12，嵌套模型标准写法）。
  若某个客户端不解析 `$defs`，可在 `register_tools()` 之后把 `$ref` 就地展开。

字段细节与踩坑记录见
[quote_create/README.md](../src/erp_tools/tools/quote_create/README.md)。

## 5. 让其他设备访问（团队模式，必须 TLS）

非回环地址若缺 TLS（且没开 `--allow-plaintext`）或缺 `--allow-host`，**直接拒绝启动**。

### 5.1 生成证书

```bash
# 最省事：什么都不用给，SAN 自动取自 .env 的 ERP_PUBLIC_HOST
bash scripts/gen-cert.sh

# 也可以显式指定（优先级最高）
bash scripts/gen-cert.sh --host 192.168.1.10 --host erp-tools.lan

# 幂等模式：证书已存在且 7 天内不过期就跳过（up.sh / deploy.sh 内部就是这么调的）
bash scripts/gen-cert.sh --ensure
```

来源优先级：`--host` 参数 > 同目录 `.env` 的 `ERP_PUBLIC_HOST`（逗号分隔，自动去掉 `:端口`）
> 自动探测本机 IPv4。无论哪种都会补上 `localhost` / `127.0.0.1`，保证宿主机自检能过。
输出目录取 `$ERP_CERT_DIR`（也会从 `.env` 读），再默认 `./certs`。

脚本按可用性自动挑后端，**不需要往项目里装 Python 依赖、也不会在仓库里留 venv**：

| 顺序 | 后端 | 说明 |
| --- | --- | --- |
| 1 | 系统 `openssl`（≥ 1.1.1） | 绝大多数 Linux 服务器自带，最省事，不联网 |
| 2 | `docker` | 拉 `alpine:3.20` 现场 `apk add openssl` 再签（主机没 openssl 时） |
| 3 | `python3` + `cryptography` | 交给 `scripts/make-cert.py` |

> ⚠️ **不要用 nginx 镜像签证书**：`nginx:*-alpine` 只带 `libssl`，**没有 `openssl` 命令**，
> 直接 `docker run nginx ... openssl` 会报 `exec: line 47: openssl: not found`。
> 脚本已改用 alpine 后端。

产出 `erp-tools.crt` + `erp-tools.key`（PEM，无口令，自签且 `CA:TRUE`，可直接当受信任根分发）。

**SAN 必须写全客户端实际使用的地址**：用 IP 访问就写 IP，用域名就写域名，否则 Node 客户端报
`ERR_TLS_CERT_ALTNAME_INVALID`。`--ensure` 模式会检查现有证书的 SAN，缺了会打 WARN 但不会动你的证书。

> 想自己控制细节也可以直接调 `python scripts/make-cert.py --host ... --out-dir ...`
> （需要本机有 `cryptography`），它和脚本的 python 分支是同一份实现。

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

**一条命令起栈**

```bash
# 1) 只写 .env（ERP_PASSWORD 请不要写），证书 SAN 直接从这里取
cp .env.example .env
#    ERP_HOST=http://192.168.1.199:8889
#    ERP_PUBLIC_HOST=erp-tools.lan:8443      # 客户端实际访问的 host:port
#    ERP_CERT_DIR=./certs                    # 服务器上建议放仓库外，见 6.1

# 2) 起栈：缺证书就自签 → 构建 → 启动 → 健康检查
bash scripts/up.sh
```

`up.sh` 做的事：

| 步骤 | 说明 |
| --- | --- |
| 前置 | `docker info` 通；`.env` 存在且 `ERP_HOST` 非空（否则直接报错退出） |
| 证书 | `gen-cert.sh --ensure`：**已有证书且 7 天内不过期就跳过**，缺失才签；SAN 取自 `ERP_PUBLIC_HOST` |
| 启动 | `docker compose up -d --build --remove-orphans` |
| 自检 | 用 `docker compose port nginx 443` 拿真实端口，`curl --cacert` 打 `/healthz`，最多等 40s |

可选参数：`--no-cert`（证书由外部提供）、`--no-health`（跳过自检）。

**等价的手动步骤**（想逐步看输出时用）：

```bash
bash scripts/gen-cert.sh --ensure        # 幂等；也可以显式 --host 覆盖 SAN
docker compose up -d --build
erp key --server https://erp-tools.lan:8443 --user 21146   # 在客户端机器上执行
curl --cacert certs/erp-tools.crt https://localhost:8443/healthz
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

- **不发布 app 端口**：`app` 只 `expose: 8765`，宿主机只能通过 nginx 访问。
- **`ERPSRV_TRUST_PROXY=1` 必须开**（镜像里已设），否则限流按 nginx 的 IP 统计。
- **换纯 HTTP**：`.env` 加 `NGINX_CONF=docker/nginx-http.conf`，并把
  `ERP_PUBLIC_HOST` 改成 `erp-tools.lan:8080`。
- **端口冲突**：改 `ERP_HTTP_PORT` / `ERP_HTTPS_PORT`（默认 8080 / 8443）。

### 6.1 数据持久化：重建堆栈什么会丢、什么不会

compose 里有两类挂载，**重建（`up -d --build`）都不会丢**，区别在于删得快不快：

| 挂载 | 类型 | 内容 | 会不会丢 |
| --- | --- | --- | --- |
| `erpdata:/data` | **命名卷**（默认名 `erp-tools-erpdata`） | `auth.db`（api-key 的 sha256）、`cookies/<工号>.json`、`qxid-<工号>.json` | `up -d` / `restart` / `down` 都保留；只有 **`down -v`** 或 `docker volume rm` 才删 |
| `${ERP_CERT_DIR:-./certs}` | 绑定挂载 | nginx 的 `.crt` / `.key` | 与容器无关，宿主机文件在就一直在 |
| `./${NGINX_CONF:-docker/nginx.conf}` | 绑定挂载 | nginx 站点配置 | 来自仓库，**跟着 git 更新** |

要点：

- **卷名被显式固定**成 `erp-tools-erpdata`（`volumes.erpdata.name`），所以换目录 clone、
  或用 `-p` 指定别的项目名，都不会「凭空多出一个空卷」导致所有人 key 失效。
- `docker compose down` 只删容器和网络；**永远不要**图省事敲 `down -v`，
  那等于让所有人重新 `erp key` 一次。
- 备份只需要这一个卷：

  ```bash
  docker run --rm -v erp-tools-erpdata:/data -v "$PWD":/backup alpine \
      tar czf /backup/erpdata-$(date +%F).tar.gz -C /data .
  ```

- 证书与 `.env` **不在 git 里**（`.gitignore` 已排除）。如果服务器是「重新 clone 再重建」，
  把这两样放到仓库外，用变量指过去：

  ```ini
  # /srv/erp-tools/deploy.env（或 compose 同目录的 .env）
  ERP_CERT_DIR=/srv/erp-tools/certs
  ```

### 6.2 定时拉取 + 自动重建

仓库自带 `scripts/deploy.sh`，逻辑是「**有变化才重建**」：

```bash
bash scripts/deploy.sh           # auto：远端有新提交才重建
bash scripts/deploy.sh --force   # 强制重建一次（改了 .env 想让它生效时用）
bash scripts/deploy.sh --check   # 只检查：退出码 0=最新，1=有更新（cron 里很好用）
```

它做的事与几个安全阀：

| 行为 | 说明 |
| --- | --- |
| `git status --porcelain` 非空 → **退出 3** | 服务器上有人手改过代码，不自动覆盖 |
| `git merge --ff-only` | 只快进，不产生 merge 提交 |
| 本地有未推送提交 / 已分叉 → **退出 4** | 不自作主张往回退 |
| `docker compose build --pull` + `up -d --remove-orphans` | **必须带 `--build`**：只用 `up -d` 不会重新构建镜像，代码改了也不会生效 |
| `docker image prune -f` | 只清悬空镜像，不动数据卷 |
| `bash scripts/gen-cert.sh --ensure` | 构建前补签缺失的证书（已有有效证书则跳过），避免 nginx 因缺证书起不来 |
| 成功后写 `.deployed-sha` | 记录当前部署的提交（已 gitignore） |

**cron（每 10 分钟检查一次）**

```cron
*/10 * * * * cd /srv/hb-erp-mcp && bash scripts/deploy.sh >> /var/log/erp-deploy.log 2>&1
```

**systemd timer（更可控，能看日志和状态）**

```ini
# /etc/systemd/system/erp-tools-deploy.service
[Unit]
Description=erp-tools auto deploy
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/srv/hb-erp-mcp
ExecStart=/usr/bin/bash scripts/deploy.sh
```

```ini
# /etc/systemd/system/erp-tools-deploy.timer
[Unit]
Description=检查 erp-tools 是否有新版本

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Unit=erp-tools-deploy.service

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now erp-tools-deploy.timer
systemctl list-timers erp-tools-deploy.timer
journalctl -u erp-tools-deploy.service -n 50 --no-pager
```

**服务器首次部署顺序**

```bash
git clone git@github.com:Senthie/hb-erp-mcp.git /srv/hb-erp-mcp
cd /srv/hb-erp-mcp

# 1) 仓库外的配置与证书（不进 git）
cp .env.example .env && vi .env          # 只填 ERP_HOST / ERP_PUBLIC_HOST / ERP_CERT_DIR
bash scripts/gen-cert.sh --host 192.168.1.10 --host erp-tools.lan
#    证书默认就落到 $ERP_CERT_DIR；不需要 python、不需要额外装包

# 2) 首次构建
docker compose up -d --build
curl --cacert /srv/erp-tools/certs/erp-tools.crt https://localhost:8443/healthz

# 3) 之后交给 deploy.sh（cron / systemd timer）
```

> 重启容器**不会**让已有的 api-key 失效：会话从卷里的 `cookies/<工号>.json` 恢复。
> 只有 ERP 会话本身过期时才需要重新 `erp key`。


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

## 9. 附录：局域网服务器部署清单（按顺序照做）

假设：服务器是 Linux，装好 Docker；仓库放 `/srv/hb-erp-mcp`，
证书放 `/srv/erp-tools/certs`（仓库外），对外地址 `erp-tools.lan:8443`。
**顺序不能颠倒**：先确认网络 → 再拉代码 → 再备证书/配置 → 再构建 → 再放行端口 → 最后发 key。

### 0. 前置检查（在服务器上）

```bash
docker version --format '{{.Server.Version}}'     # ≥ 24
docker compose version                            # v2 即可（用到了 name: 顶层键）

# 到 ERP 的两个端口都要通（主站 + 验证码在另一个端口）
curl -sI http://192.168.1.199:8889/Admin/Login.aspx   | head -1   # 期望 200/302
curl -sI http://192.168.1.199:8887/ValidateCode.aspx  | head -1   # 期望 200

sudo timedatectl set-timezone Asia/Shanghai
date -u
```

- 验证码端口规则：默认把 `ERP_HOST` 的端口换成 **8887**，可用 `ERP_CAPTCHA_HOST` 覆盖。
- 拉私有仓库需要凭据：部署密钥（推荐只读）或 `https://<PAT>@github.com/...`。

### 1. 拉代码

```bash
sudo mkdir -p /srv && sudo chown "$USER" /srv
git clone git@github.com:Senthie/hb-erp-mcp.git /srv/hb-erp-mcp
cd /srv/hb-erp-mcp
git log --oneline -1
```

### 2. 准备仓库外的资产

```bash
sudo mkdir -p /srv/erp-tools/certs /srv/erp-tools/backup
sudo chown -R "$USER" /srv/erp-tools
cd /srv/hb-erp-mcp
cp .env.example .env
```

`.env` 必须改这四项（其余保持注释即可）：

```ini
ERP_HOST=http://192.168.1.199:8889
ERP_PUBLIC_HOST=erp-tools.lan:8443        # ★ 等于客户端实际访问的 host:port
ERP_CERT_DIR=/srv/erp-tools/certs         # 证书放仓库外，重新 clone 也不丢
# 可选：ERP_CAPTCHA_HOST / ERP_HTTPS_PORT / ERP_HTTP_PORT / ERPSRV_CONCURRENCY
```

证书二选一：

```powershell
# A) 在自己的 Windows 上签好再传（服务器上什么都不用装）
.\.venv\Scripts\python.exe scripts\make-cert.py `
    --host erp-tools.lan --host 192.168.1.226 --out-dir certs
scp certs\erp-tools.crt certs\erp-tools.key user@server:/srv/erp-tools/certs/
```

```bash
# B) 直接在服务器上签（推荐）：SAN 自动取自 .env 的 ERP_PUBLIC_HOST，无需装包
cd /srv/hb-erp-mcp
bash scripts/gen-cert.sh
#    输出目录跟 $ERP_CERT_DIR 走（即 /srv/erp-tools/certs）
```

> `--host` 要把客户端**所有**访问写法都列上（域名 + IP）。SAN 缺了哪个，
> 客户端就报哪个 `ERR_TLS_CERT_ALTNAME_INVALID`。

### 3. 渲染检查（不启动也能验配置）

```bash
docker compose config | grep -E 'ERP_HOST|ERPSRV_ALLOW_HOST|ERP_CERT_DIR|source:'
```

必须看到：`ERP_HOST` 已渲染出真实地址（没渲染会直接报 `请在 .env 里设置 ERP_HOST`）、
`ERPSRV_ALLOW_HOST` 是 `erp-tools.lan:8443`、certs 的 source 指向 `/srv/erp-tools/certs`。

### 4. 首次构建与启动

```bash
bash scripts/up.sh                # 缺证书就自签 → build → up → 健康检查（推荐）

# 或者手动三步：
bash scripts/gen-cert.sh --ensure
docker compose up -d --build
docker compose ps                 # app 应为 healthy，nginx 应为 running/healthy
```

自检（不经过客户端证书信任也能验）：

```bash
curl --cacert /srv/erp-tools/certs/erp-tools.crt https://localhost:8443/healthz
# → {"status":"ok","version":"0.3.0","keys":0,"global_concurrency":6}
```

### 5. 放行端口（只开对外那一个）

```bash
sudo ufw allow 8443/tcp          # HTTPS 入口（nginx）
# sudo ufw allow 8080/tcp        # 仅当用纯 HTTP 版
sudo ufw status numbered
```

**不要**放行 8765：`app` 只 `expose` 在 docker 网内，宿主机的 8765 根本没监听。

### 6. 换 api-key（在**客户端**机器上做，不必登服务器）

```bash
erp key --server https://erp-tools.lan:8443 --user 21146 \
        --cacert /path/to/erp-tools.crt        # Windows 用 --cacert certs\erp-tools.crt
# → stdout 输出 erp_xxx
```

每个人/每台 AI 各自执行一次，各拿各的 key；密码只在那一次请求里用。

### 7. 客户端接入 + 冒烟测试

```json
{ "servers": { "erp": {
  "type": "http",
  "url": "https://erp-tools.lan:8443/mcp",
  "headers": { "Authorization": "Bearer erp_xxx" } } } }
```

Node 系客户端要信任自签 CA（否则握手失败）：`setx NODE_EXTRA_CA_CERTS "<绝对路径>\erp-tools.crt"`，然后**重启客户端**。

不装客户端也能验 MCP 本身：

```bash
curl --cacert erp-tools.crt -X POST https://erp-tools.lan:8443/mcp \
  -H "Authorization: Bearer erp_xxx" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# → 应返回 15 个工具名
```

### 8. 交给自动部署

```bash
sudo tee /etc/systemd/system/erp-tools-deploy.service >/dev/null <<'EOF'
[Unit]
Description=erp-tools auto deploy
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/srv/hb-erp-mcp
ExecStart=/usr/bin/bash scripts/deploy.sh
EOF

sudo tee /etc/systemd/system/erp-tools-deploy.timer >/dev/null <<'EOF'
[Unit]
Description=检查 erp-tools 是否有新版本

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Unit=erp-tools-deploy.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now erp-tools-deploy.timer
systemctl list-timers erp-tools-deploy.timer
```

### 9. 日常运维

```bash
# 备份（只有这一个卷有状态）
docker run --rm -v erp-tools-erpdata:/data -v /srv/erp-tools/backup:/backup alpine \
    tar czf /backup/erpdata-$(date +%F).tar.gz -C /data .

# 手动升级 / 强制重建（改了 .env 必须加 --force，否则代码没变不会重建）
bash scripts/deploy.sh
bash scripts/deploy.sh --force

# 回滚：先停自动部署，再退代码重建
sudo systemctl stop erp-tools-deploy.timer
git log --oneline -5 && git reset --hard <上一个提交>
docker compose up -d --build
sudo systemctl start erp-tools-deploy.timer

# 看日志
docker compose logs -f --tail=100 app nginx
```

### 10. 卡住了先看这里

| 卡在第几步 | 现象 | 处理 |
| --- | --- | --- |
| 0 | `curl` 到 ERP 超时 | 服务器与 ERP 不在同一网段 / 有防火墙；先在服务器上 `ping`/`curl` 通 |
| 1 | `Permission denied (publickey)` | 服务器没配部署密钥；改用 `https://<PAT>@github.com/...` |
| 3 | `请在 .env 里设置 ERP_HOST` | `.env` 没建或变量名拼错（compose 只从同目录 `.env` 读） |
| 4 | `421 Invalid Host header` | `ERP_PUBLIC_HOST` 与客户端实际用的 `host:port` 不一致（含端口） |
| 4 | 容器 healthy 但 `curl` 502 | 容器内到 ERP 不通，或 `ERP_HOST` 写错；`docker compose exec app python -c "import urllib.request;print(urllib.request.urlopen('http://192.168.1.199:8889/Admin/Login.aspx').status)"` |
| 4 | nginx 起不来 / `cannot load certificate` | `ERP_CERT_DIR` 下没有 `erp-tools.crt`/`.key`，或文件名不符 |
| 6 | `SSLCertVerificationError` | `--cacert` 指向的证书不对，或 SAN 不含该地址 |
| 6 | `401 invalid_credentials` | 工号密码错；连续 10 次会锁 15 分钟 |
| 7 | 客户端连不上但 `curl` 正常 | 客户端没信任自签 CA（Node 要 `NODE_EXTRA_CA_CERTS` 并重启） |
| 8 | `deploy.sh` 退出 3 | 仓库里有未提交改动（多半是 `.env` 被误改进了仓库目录并被跟踪） |

