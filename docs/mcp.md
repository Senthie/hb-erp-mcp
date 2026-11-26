# MCP 服务（api-key 网关 + Streamable HTTP）

给「多人共用一个部署」和「AI 客户端调用」用。**一个进程同时提供 HTTP 认证和 MCP**，
与 CLI 共用同一份 `core/` + `tools/` 页面逻辑。

- 本机跑批 → [CLI](cli.md)
- 嵌进别的程序 → [Python API](api.md)

```
            ┌───────────────── erp serve（一个进程）────────────────┐
CLI ────────┤ core/*  + tools/*  ← 同一份请求 / 解析 / 并发逻辑        │
            │                                                     │
浏览器 ────►│ /ui             → React 登录 / 服务注册页             │
curl ──────►│ /v1/auth/login  → 换取 api-key                       │
服务端 ────►│ /v1/mcp/register → 登记 MCP 服务信息（PG）            │
MCP 客户端 ─►│ /mcp            → Streamable HTTP（必须带 Bearer）    │
            └─────────────────────────────────────────────────────┘
```

## 1. 安装与启动

```powershell
cd api
uv sync --extra dev --extra server        # mcp + uvicorn + psycopg + cryptography
.\.venv\Scripts\erp.exe init --mode mcp   # 只问 4 项，生成 config.yaml
.\.venv\Scripts\erp.exe serve             # 默认 127.0.0.1:8765
```

`erp init --mode mcp` 只问这些（回车取默认）：**ERP 地址**、**监听地址**、
**存储（sqlite/postgres）**、**第一个管理员**。其余全部自动：

| 配置项 | 生成方式 |
| --- | --- |
| `server.tls: auto` | 非回环监听时，首次启动自动签发自签证书到 `<数据目录>/tls/` |
| `server.allow_hosts: auto` | 由 `bind`（通配地址时用本机 IPv4 / 127.0.0.1）与 `public_host` 派生 |
| `server.register_secret: auto` | 首次启动随机生成到 `<数据目录>/register.secret` |
| `server.pg_dsn` | postgres 模式由 host/port/user/password/db 直接拼好（密码自动 percent-encoding） |
| `mcp.json` | 生成 MCP 客户端配置模板（token 待申请后自动写入） |

生成的 `config.yaml`（`mode: mcp`）大致长这样：

```yaml
mode: mcp
erp:
  host: http://192.168.1.199:8889
server:
  bind: 127.0.0.1:8765
  storage: sqlite            # 生产改成 postgres + pg_dsn
  sqlite_path: auth.db
  admin_users: [root]
  tls: auto
  allow_hosts: auto
  register_secret: auto
  concurrency: 6
```

`erp serve` 的命令行参数仍可**临时覆盖**配置文件里的对应项：

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `--bind` | 取 `server.bind`（`127.0.0.1:8765`） | 非回环自动签 TLS；或 `--tls-cert/--tls-key` 用现成证书 |
| `--pg-dsn` / `--db` | 取 `server.storage` | 临时改用 PostgreSQL / SQLite |
| `--register-secret` | auto | 覆盖自动生成的注册密钥（留空字符串＝关闭校验） |
| `--tls auto\|off` | auto | `off` = 明确不加密（配合 `--allow-plaintext`） |
| `--allow-host` | auto | Host 白名单，可重复 |
| `--public-host` | 取 `server.public_host` | 反向代理 / docker 的对外地址 |
| `--global-concurrency` | `6` | 全局 ERP 并发闸门（实测 4~8 饱和） |
| `--trust-proxy` | 取配置 | 信任 `X-Forwarded-For` |
| `--allow-plaintext` | 取配置 | 显式允许「非回环 + 明文 HTTP」（TLS 由上游终止时） |

> 存储默认 `sqlite`（单机试跑）；生产把 `server.storage` 改成 `postgres` 并填 `pg_dsn`。
> 用 `--pg-dsn`/`--db` 都没给、配置里也没有时直接拒绝启动，
> 避免「以为在用 PG，其实写进了本地文件」。表结构启动时自动建。

## 2. 换 api-key 与平台账号

有两条路，按使用者是「命令行/脚本」还是「人」来选：

| 方式 | 入口 | 适用 |
| --- | --- | --- |
| **平台账号 → Token 管理**（推荐给所有人） | `/ui` 网页或 `/v1/platform/*` | 浏览器里注册登录、绑定 ERP、申请/撤销 MCP token |
| ERP 工号直连换 key（兼容保留） | `POST /v1/auth/login` / `erp key` | 本机 CLI、脚本、CI |

两条路签出来的都是同一种 `erp_xxx` token，`: /mcp` 用法完全一样。
平台账号签发的 token 额外记录了 owner，可以在网页里按账号列出与撤销。

### 2.1 可视化界面（`/ui`）：平台账号 → Token 管理

前端在 [`web/`](../web)（React 19 + Tailwind 4），构建产物落进后端包目录，由 `erp serve` 托管：

```powershell
cd web
npm install
npm run dev      # 开发：http://127.0.0.1:5173/ui/（自动把 /v1、/healthz 代理到 8765）
npm run build    # 产物 → api/src/erp_tools/apps/server/static
```

访问 <http://127.0.0.1:8765/ui/>，页面分三层：

1. **平台登录 / 注册** —— 这是 HB-MCP 自己的账号（用户名 + 密码，scrypt 存储），
   **不是 ERP 工号**。会话只放在 HttpOnly cookie 里，7 天有效。
2. **Token 管理** —— 绑定 ERP 账号（**一个平台账号只能绑一个工号**，密码只用于验证、
   不落盘）；换绑到别的工号会**自动撤销**该账号下所有旧 token。
3. **申请 Token** —— 在「我的 Token」右上角点**新增**，弹窗里填写备注（必填）后点创建；
   成功后**同一个弹窗内**展示结果：**上方是 API Key、下方是使用指南**（`mcp.json` 配置 +
   可粘贴给 AI 的提示），三者都能一键复制。结果态必须点「完成」才关闭（Esc/点遮罩不会关，
   避免手滑丢掉只显示一次的 token）；关掉后列表只保留 id 与状态，可随时撤销。

关闭自助注册：`--no-signup`，或把配置里的 `server.allow_signup` 改成 `false`。

### 2.2 平台账号接口（`/v1/platform/*`，cookie 会话）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/v1/platform/register` | `{username, password}` → 建号并登录（201） |
| POST | `/v1/platform/login` | `{username, password}` → 登录，下发 `hb_session` cookie |
| POST | `/v1/platform/logout` | 注销当前会话（204） |
| GET | `/v1/platform/me` | 当前账号 + ERP 绑定 + token 列表 |
| POST | `/v1/platform/erp/bind` | `{username, password}`＝**ERP 工号密码**，验证后绑定 |
| GET | `/v1/platform/tokens` | 我的 token（只给 id 前缀，不含明文） |
| POST | `/v1/platform/tokens` | `{label}` → 签发 token，返回明文 + `mcp` 配置/提示 |
| POST | `/v1/platform/tokens/{id}/revoke` | 撤销一把 token |

用户名 3~32 位（字母/数字/`.`/`_`/`-`，会转小写），密码至少 8 位；
登录失败连续 10 次锁定 15 分钟，登录/注册每 IP 10 次/分钟。

### 2.3 管理员后台（`/v1/admin/*`，需要 admin 角色）

把用户名写进配置的 `server.admin_users`（或 `erp init` 时填「第一个管理员」，也可用
`--admin-user`）后，该账号注册/登录时自动获得 `admin` 角色；
登录后页面顶部会出现「管理后台」，也可以直接用 cookie 会话调接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/v1/admin/overview` | 用户 / token 统计 + 可用动作清单 |
| GET | `/v1/admin/users?query=&limit=&offset=` | 账号列表（含 token 数、会话数） |
| GET | `/v1/admin/users/{id}` | 账号详情 + 最近 20 条审计 + 最近 20 条 MCP 调用（含 `call_count`） |
| POST | `/v1/admin/users/{id}/update` | `{role?, disabled?}`；**禁用会踢掉会话并撤销全部 token** |
| POST | `/v1/admin/users/{id}/unbind-erp` | 解绑 ERP（同时撤销该账号 token） |
| POST | `/v1/admin/users/{id}/revoke-tokens` | 撤销该账号全部 token |
| DELETE | `/v1/admin/users/{id}` | 删除账号（会话删除、token 撤销，保留审计） |
| GET | `/v1/admin/audit?action=&actor=&result=&since=&until=&limit=&offset=` | 审计日志 |
| GET | `/v1/admin/calls?tool=&actor=&status=&since=&until=&limit=&offset=` | **MCP 工具调用日志** |

安全约束：管理员不能禁用 / 降级 / 删除**自己**（409 `cannot_modify_self` /
`cannot_delete_self`），避免把自己锁在门外。

### 2.4 审计日志（PostgreSQL `audit_logs`）

所有关键动作都会落一行，字段：`created_at` / `action` / `result` / `actor_id` /
`actor_name` / `actor_type` / `target_type` / `target_id` / `status_code` / `ip` /
`user_agent` / `detail`（JSONB）。

- 覆盖：平台注册 / 登录（含失败）/ 登出、绑定 ERP、申请 / 撤销 token、ERP 直连换 key
  （`/v1/auth/login`）、MCP 服务登记、管理员改用户 / 删用户 / 解绑 / 撤销 token。
- **不写敏感信息**：没有密码、没有 api-key 明文、没有 cookie；`detail` 只放工号、备注、
  token id 前缀这类定位信息。
- 写日志失败只告警，绝不影响业务请求。

### 2.5 MCP 调用日志（PostgreSQL `mcp_call_logs`）

每个 MCP 工具调用（`tools/call`）单独一行——这是「谁通过 MCP 访问了 ERP 的哪个页面」
的答案；账号 / 管理动作仍走上面的 `audit_logs`，两者分表：

| 字段 | 说明 |
| --- | --- |
| `created_at` | 调用完成时间 |
| `tool` | 工具名，如 `erp_quote_list` |
| `status` | `success` / `failure`（工具抛错或返回 `isError`） |
| `duration_ms` | 耗时 |
| `actor_id` / `actor_name` / `actor_type` | 平台账号 id、用户名 / ERP 工号、`api_key` |
| `token_id` | 用的是哪把 token（前缀；**不存明文 key**） |
| `erp_username` | 实际访问 ERP 的身份 |
| `ip` / `user_agent` | 调用方来源（配置里 `trust_proxy: true` 时取 `X-Forwarded-For`） |
| `error` | 失败原因（截断 500 字，已打码） |
| `arguments` | 定位用入参摘要：最多 12 个键、每值 160 字，`password`/`token`/`cookie` 等置 `***` |

- 后台「管理后台 → MCP 调用」按工具名 / 用户 / 结果筛选，概览里有累计调用数。
- 接口：`GET /v1/admin/calls`，返回 `{limit, offset, has_more, items[]}`，`items[].owner_name`
  会补上平台账号名（库里只存 `actor_id`）。
- 写入失败同样只告警，工具本身照常返回。

### 2.6 ERP 工号直连换 key（兼容 / CLI）

**密码是每个使用者自己的，不进配置文件、不进容器、不落盘。** 谁要用，谁用自己的
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

- **密码不落盘**：只在登录请求的调用栈里存在，成功后立刻丢弃；`api_keys` 表里只有
  key 的 sha256 与工号，**没有密码列**。
- 多个人/多个 AI 客户端 = 多把 key，可分别撤销；同一 ERP 账号的多把 key 共用一份 ERP 会话。
- `ERP_USERNAME` / `ERP_PASSWORD` 环境变量**只给 CLI 用**；`erp serve` 完全不读，
  配了会打印一条提示建议删掉。

### 2.7 注册 MCP 服务信息（`POST /v1/mcp/register`）

登记的是**服务本身**（本项目即 HB-MCP）：名称、版本、访问地址、传输方式、环境、负责人、描述。
不涉及任何 ERP 账号，所以无需 api-key；按 `name` **幂等**（同名再提交＝更新元数据）。

```powershell
curl.exe -X POST http://127.0.0.1:8765/v1/mcp/register `
  -H "content-type: application/json" `
  -d '{\"name\":\"HB-MCP\",\"version\":\"0.3.0\",\"endpoint\":\"https://erp-tools.lan:8443/mcp\",
      \"transport\":\"streamable-http\",\"environment\":\"production\",\"owner\":\"陈小美\",
      \"description\":\"华博 ERP 工具网关\"}'
# → 201 {"status":"registered","service":{...}}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | ✓ | 服务名，唯一键（≤64 字符） |
| `version` | | 版本（≤32 字符） |
| `endpoint` | | `http(s)://` 地址；`stdio` 传输可留空 |
| `transport` | | `streamable-http`（默认）/ `sse` / `websocket` / `stdio` |
| `environment` | | `production`（默认）/ `staging` / `development` / `test`（`prod`/`dev` 等别名可用） |
| `owner` / `description` | | 负责人 / 服务描述 |
| `tools` | | 工具数量（整数） |
| `metadata` | | 任意 JSON 对象（≤8KB），存进 `JSONB` |

查询已登记服务（公开、只读）：

```powershell
curl.exe "http://127.0.0.1:8765/v1/mcp/services?limit=20"
```

防护：每 IP **10 次/分钟**限流；`register_secret: auto`（默认）时首次启动会生成
`<数据目录>/register.secret`，请求头必须带 `X-Register-Secret`（否则 `403 register_secret_invalid`）。
不需要校验时把它改成空字符串 `""`。

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

24 个工具，全部要求 `Authorization: Bearer <api-key>`。
列表类结果默认 50 条、上限 500 条，超出时 `truncated=true`。

接口与 ERP 菜单的对应关系（哪个菜单页已接入、各页能做什么）见
[erp-menu.md](erp-menu.md)，单接口细节见 [api/](api/)。

| 工具 | 写库 | 说明 |
| --- | --- | --- |
| `erp_whoami` | — | 当前身份 / 会话状态；`erp_alive=false` 表示会话过期 |
| `erp_capabilities` | — | 当前账号能访问哪些页面（`missing_pages` 为空即齐全） |
| `erp_menu` | — | **菜单 → 接口对照**：哪些菜单页已接入、各页能做什么、对应哪个工具与文档 |
| `erp_quote_list` | — | 报价需求列表查询（日期区间 + 一个关键字，其余条件本地过滤） |
| `erp_price_list` | — | **客户报价列表**（`BJ_KHBJ`，一行=一个报价产品行）：日期区间可选作用于制单/交货/离厂 + 服务端下拉过滤 + 本地 `where` |
| `erp_price_detail` | — | **客户报价详情**：表头 + 产品字段 + 数量明细 + 选配项目 + 部件明细(材料/尺寸/工序/辅料) + 费用 + 产品描述 + 制单审核 |
| `erp_price_node` | `record_id` + `node`（可选 `cpid`/`bjid`/`index`/`cpslid`/`include_formula`） | **子数据 22 个节点**：`quantities` 数量档 / `part` 部件 / `part_materials` 部件辅料 / `part_process` 部件工序 / `part_machines` 部件机台 / `product_check` 送货资料 / `fees` 费项明细 / `other_fees` 其他费项 / `cp` 产品资料 / `cp_process` 产品工序 / `cp_materials` 产品辅料 / `feedback` 需求反馈 / `sgkb` 手工开版 / `split_price` 拆分价格；`panel:description`/`material_list`/`freight`/… 8 个折叠面板；`list` 列出节点；`all` 一次取整棵树 |
| `erp_quote_detail` | — | 单条报价需求详情（ERP 订单需求字段） |
| `erp_page_catalog` | — | **四个模块的数据页面清单**：列表页 / 详情页 / 每个弹窗子页（子页名 + 打开按钮 + 说明） |
| `erp_costing_list` | — | **产品核价列表**（`BJ_KHBJ/Index.aspx`，QXID 1281，一行=一个核价产品行，74 列含核价单价/利润率等成本列）；详情与子数据用 `erp_price_detail` / `erp_price_node` |
| `erp_production_list` | — | **生产施工单列表**（`SC_SCSGD/Index.aspx`，QXID 740，一行=一张施工单） |
| `erp_production_detail` | — | 施工单详情：表头字段 + 部件表(17 列) + 送货表(21 列) + 备注文本 + 可点子页清单 |
| `erp_production_node` | `record_id` + `node` | **施工单 18 个弹窗子页**：`cp` 产品资料 / `cp_process` 产品工序 / `cp_materials` 产品辅料 / `part` 部件 / `part_new` 新增部件 / `part_materials` 部件辅料 / `part_process` 部件工序 / `part_machines` 部件机台 / `product_check` 送货资料 / `part_pick` 部件明细 / `product_bg` 产品变更 / `feedback` 需求反馈 / `sgkb` 手工开版 / `template_product` 产品模板 / `by_template` 按模板 / `binding` 装订D10 / `package_content` 包装内容 / `stock` 生产库存；`list` 列出节点 |
| `erp_production_row` | `record_id` 或 `number` + `action` | **列表行内点击事件**：`esign` 电子稿袋（文件浏览）/ `gjqk` 产品跟进信息 / `zhzy` 做货指引 / `wjqk` 文件跟进情况 / `zszm` 专色油墨 / `clqd` 临时材料清单 / `print` 打印报表列表；`actions`（默认）列出这一行能点什么。返回子页 `{title, tables, links, texts}`；`info`/部件备注/核价否 在 ERP 侧不可读 |
| `erp_purchase_list` | — | **采购订单列表**（`CG_CGDD/Index.aspx`，QXID 266，一行=一条材料明细） |
| `erp_purchase_detail` | — | 采购订单详情：表头字段 + 材料明细内联网格（`gridView2` 33 列） |
| `erp_purchase_node` | `record_id` + `node` | 采购订单子页面（只有供应商档案选择等弹窗；明细在详情页网格里） |
| `erp_sales_order_list` | — | **客户订单列表**（`DingDan/DD_KHDD/Index.aspx`，QXID 687，一行=一个订单产品行，92 列；日期字段可选制单/订单/离厂/交货） |
| `erp_sales_order_detail` | — | 客户订单详情：46 个字段 + 3 个页内网格（数量档13列 / 部件16列 / 送货 `gridView` 28列）+ 8 个文本域 + 子页清单 |
| `erp_sales_order_node` | `record_id` + `node` | **客户订单 20 个弹窗子页**：`cp` 产品资料 / `cp_process` 产品工序 / `cp_materials` 产品辅料 / `part` 部件 / `part_new` 新增部件 / `part_materials` 部件辅料 / `part_process` 部件工序 / `part_machines` 部件机台 / `product_check` 送货资料 / `quantities` 数量档 / `fees` 费项明细 / `other_fees` 其他费项 / `feedback` 需求反馈 / `sgkb` 手工开版 / `product_bg` 产品变更 / `template_product` 产品模板 / `by_template` 按模板 / `product_guide` 产品指引 / `extra_info` 附加信息 / `stock` 生产库存；`list` 列出节点 |
| `erp_sales_order_attachment` | `record_id` / `number`（可选日期区间、`where`、`filters`） | **客户订单附件**：列表页「表单编号」左击调出的文件；返回文件名 + 静态直链（`/Admin/FuJian/…`，8887 端口，**不带 cookie 也能 GET**）。多附件自动展开成多个 |
| `erp_attendance` | — | 月度考勤明细 |
| `erp_quote_new_form` | — | 空白页结构：表头字段、只读标记、三个下拉、textarea 列表 |
| `erp_quote_product_form` | — | **产品明细子页字段清单**（205 个控件，其中 51 个可上传）+ `upload` JSON Schema + `categories` 类别树 |
| `erp_quote_options` | — | 业务员 / 跟单员 / 单价体系选项 + `employees` 合并名单 |
| `erp_quote_customer_search` | — | 客户检索（返回 `customer_id` 与该客户默认业务员/折扣率） |
| `erp_quote_create` | ✅ | 新建单据：主表 + 1 条产品明细（**含产品数量**）。`confirm=false` 即预览；传 `attachments` 时建单成功后**自动上传附件** |
| `erp_quote_details` | — | 列明细行，拿 `cpid`（改/删该行要用） |
| `erp_quote_update` | ✅ | 改**表头**（备注/联系人/电话/手机/传真/折扣率/表头产品描述/业务员/跟单员）；⚠ 改不了明细 |
| `erp_quote_update_product` | ✅ | 改**产品明细**：名称/规格/数量 + **产品描述 `product_desc`（CPMS）** + `fields`（配件描述/修改记录/检测项目…）；保存后逐个回读复核 |
| `erp_attachment` | ✅ | **通用附件**（报价需求 / 客户报价·核价 / 施工单 / 采购订单 / 客户订单）：传 `files` = 上传，传 `delete`/`file_id` = 删除（`confirm=true`），传 `download` = 取回 base64，都不传 = 列表。附件页路径自动发现；上传后**下载回来比字节数**（`size_ok`），大文件用 `files[].chunk_index` 分片 |
| `erp_attachment_modules` | — | 列出有附件功能的模块、各自附件页与删除方式 |
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
[quote_create/README.md](../api/src/erp_tools/tools/quote_create/README.md)。

## 5. 让其他设备访问（团队模式，必须 TLS）

非回环地址若既没 TLS 又没开 `allow_plaintext`，**直接拒绝启动**；
`allow_hosts` 同样自动派生，不再需要逐个手写。

### 5.1 证书：默认自动签发

`erp init --mode mcp` 生成 `tls: auto`，所以**什么都不用准备**：

```yaml
server:
  bind: 0.0.0.0:8765
  tls: auto            # 首次启动签 <数据目录>/tls/erp-tools.crt|key
  allow_hosts: auto    # 由 bind（通配时用本机 IPv4/127.0.0.1）派生
```

首次 `erp serve` 会打印 `已自动签发自签证书：<数据目录>/tls/erp-tools.crt`。
SAN 自动包含 `localhost` / `127.0.0.1` / 本机 IPv4 / `public_host` 里的域名，
客户端用 IP 或域名访问都能过校验；证书持久化在数据目录，重启不会重新签。

需要**正式证书**（公司 CA / Let's Encrypt）时，把 `tls` 换成路径即可：

```yaml
  tls_cert: /etc/erp-tools/certs/erp-tools.crt
  tls_key:  /etc/erp-tools/certs/erp-tools.key
```

> 仍想用脚本签一大批 SAN（老部署 / 给 nginx 用）：
> `bash scripts/gen-cert.sh --host 192.168.1.10 --host erp-tools.lan`。
> 它按 `openssl` → `docker` → `python cryptography` 顺序挑后端，产出 `erp-tools.crt|key`。
> ⚠️ 别用 `nginx:*-alpine` 签：镜像里没有 `openssl` 命令。

**SAN 必须写全客户端实际使用的地址**：用 IP 访问就写 IP，用域名就写域名，否则 Node 客户端报
`ERR_TLS_CERT_ALTNAME_INVALID`。用 `tls: auto` 时这一步已经自动完成。

### 5.2 启动

```powershell
erp serve --bind 0.0.0.0:8765 --public-host 192.168.1.226:8765
# 或直接把 bind / public_host 写进 config.yaml，然后只敲 erp serve
```

`allow_hosts` 由上面的两项自动派生（`192.168.1.226:8765` + 本机其它 IPv4 + `127.0.0.1`），
不用再一条条 `--allow-host`。

> 还是**同一个进程**，不是再开一个——把平时那条启动命令换成这条即可（同时跑两个会抢 8765 端口）。
> 只访问回环、又要给别人用附件代理时，可以继续 `bind: 127.0.0.1:8765`，
> 只把 `public_host` 指到 nginx/隧道对外地址。

想让它常驻（不开着终端也在跑），三选一：

```powershell
# A. 后台窗（最简单，注销/重启后要再跑一次）
Start-Process api\.venv\Scripts\erp.exe -WindowStyle Hidden `
  -ArgumentList "serve","--bind","0.0.0.0:8765","--allow-host","192.168.1.226:8765"

# B. 计划任务（开机自启，仍以当前用户身份跑）
schtasks /Create /TN "erp-tools MCP" /SC ONSTART /RU $env:USERNAME /RL LIMITED `
  /TR "\"$PWD\api\.venv\Scripts\erp.exe\" serve --bind 0.0.0.0:8765 --allow-host 192.168.1.226:8765"

# C. Docker（见第 6 节，nginx 终止 TLS，最省心）
docker compose up -d
```

> **重启网关不用重新换 api-key**：key 存在 PostgreSQL，ERP 会话靠 `cookies/` 里的 cookie 自动恢复。
> 只有 ERP 会话真的过期（`401 erp_session_expired`）才需要重新 `erp key` 登录。

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

- 回环地址不需要 `tls`，也不需要 `allow_plaintext`（TLS 在代理层）。
- 但 **`allow_hosts` 仍然要覆盖外部域名**（写 `auto` 时会带上 `public_host`）：
  MCP 拿 `Host` 逐字比对白名单，缺了会退化成「只认 `localhost:*` / `127.0.0.1:*`」，
  外部域名访问会收到 `421`。
- 如果监听的不是回环地址（例如容器里 `bind: 0.0.0.0:8765`），必须显式写
  `allow_plaintext: true`，启动时打印一条 WARN。
- `trust_proxy` 不开的话，限流会把所有客户端都当成代理的 IP。

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
| 附件链接打得开但**下载不到** | ERP 直链是内网地址，跨网段不可达 | 用工具返回的 `proxy_url`（走 `/v1/files`），见 5.8 |
| 浏览器打开附件地址是**一页 404 / 没反应** | ERP 弹窗给的端口不对（如 `:8887`），文件实际在 `:8889` | 用列表里的 `url`（已探测）或 `proxy_url`；原值在 `url_erp` |

### 5.8 附件下载代理（跨网段）

ERP 附件的直链是内网静态目录 `http://<ERP_HOST>:8887/Admin/FuJian/...`，
只有同网段能 GET。网关提供转发端点，链接里带签名、**不需要 api-key**：

```
GET /v1/files?path=/Admin/FuJian/<模块>/Picture/<guid>.zip&t=<exp>.<hmac>
```

- `erp_attachment(with_urls=true)` / `erp_sales_order_attachment` 的每一行都同时给
  `url`（ERP 直链）与 `proxy_url`（网关链接）；AI 侧只用 `proxy_url` 即可。
- 签名把 **路径 + 过期时间** 绑定，默认 1800 秒（`ERP_FILE_PROXY_TTL`），
  所以链接可以贴进浏览器/IM/日志，过期就重新调工具拿。
- 只代理 `/Admin/FuJian/` 前缀；目标主机固定取 `ERP_HOST`（再回退验证码端口），
  不接受调用方指定 host → 不会被拿来当 SSRF 跳板。
- 基地址优先级：`ERP_PUBLIC_BASE` → 当前请求的 `Host` → `http://127.0.0.1:8765`。
  客户端从 `https://erp-tools.lan` 连进来，代理链接就是同一个域名。
- 签名密钥在运行时数据目录的 `proxy.secret`（首次启动自动生成；SQLite 模式下与库同目录，
  PostgreSQL 模式下就是 `ERP_DATA_DIR`），网关与 CLI 共用；**换机器要一起拷**，
  否则旧链接全部失效（重新调工具即可）。

```powershell
# 拿到带 proxy_url 的清单
erp attachment --module quote_request --id <记录ID> --urls

# 其它网段直接下（curl 会按 content-disposition 命名）
curl.exe -OJ "https://erp-tools.lan/v1/files?path=/Admin/FuJian/...&t=1750000000.ab12cd34"
```

排错：`403 path_not_allowed` = 不是附件目录；`403 bad_signature` = 链接过期或被改过；
`502 upstream_failed` = ERP 取不到（附件可能已被删）。`GET /v1/me` 的 `file_proxy`
字段能看到当前端点、有效期和基地址。

## 6. Docker + nginx

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | 两阶段构建（uv 装依赖 → slim 运行时），非 root，`/data` 挂卷 |
| `.dockerignore` | 排除 `cookies` / `certs` / 业务数据，**凭据不进镜像** |
| `docker-compose.yml` | `app`（读 `./config.yaml`）+ `db` + `nginx`（TLS 终止） |
| `docker/env.example` | docker 部署只需要这几个值（复制成 `.env`） |
| `docker/nginx.conf` | 80→443 跳转 + 443 SSL 反代（SSE 不缓冲、Host 原样透传） |
| `docker/nginx-http.conf` | 纯 HTTP 内网版（临时验证用，**明文传 api-key**） |

```mermaid
flowchart LR
    C["MCP 客户端<br/>Bearer erp_xxx"] -->|HTTPS 8443| N["nginx<br/>TLS 终止"]
    N -->|HTTP 8765<br/>docker 内网| A["erp-tools 网关<br/>/mcp + /v1/*"]
    A -->|HTTP| E["ERP<br/>192.168.1.199:8889"]
```

**一条命令起栈**

前置（Ubuntu/Debian 之类，租一台内网 Linux 也能跑）：

```bash
docker --version && docker compose version    # 需要 Docker 23+ 与 compose v2 插件
sudo usermod -aG docker "$USER"              # 免 sudo；改完重新登录一次
# 若服务器上没有 openssl（签证书用它），脚本会自动退回 docker 里的 alpine+openssl
```

```bash
git clone git@github.com:Senthie/hb-erp-mcp.git /srv/hb-erp-mcp
cd /srv/hb-erp-mcp

# 1) .env 只有 3~4 个值
cp docker/env.example .env
#    ERP_HOST=http://192.168.1.199:8889
#    ERP_PUBLIC_HOST=erp-tools.lan:8443      # 客户端实际访问的 host:port
#    POSTGRES_PASSWORD=change-me             # 只用 URL 安全字符
#    ERP_ADMIN_USER=root                     # 可选：第一个管理员

# 2) 起栈：生成 config.yaml → 缺证书就自签 → 构建 → 启动 → 健康检查
bash scripts/up.sh

# 3) 放行端口后打开网页注册（详见下方各节）
sudo ufw allow 8443/tcp
#    https://erp-tools.lan:8443/ui
```

`up.sh` 做的事：

| 步骤 | 说明 |
| --- | --- |
| 前置 | `docker info` 通；`.env` 存在且 `ERP_HOST` 非空（否则直接报错退出） |
| 应用配置 | **按 `.env` 生成 `config.yaml`**（`mode: mcp` + postgres DSN 拼好，`chmod 644` 让容器 uid 10001 能读）；已存在且带 `server:` 段则沿用，否则备份成 `.bak` 重生成 |
| 证书 | `gen-cert.sh --ensure`：**已有证书且 7 天内不过期就跳过**，缺失才签；SAN 取自 `ERP_PUBLIC_HOST` |
| 启动 | `docker compose up -d --build --remove-orphans` |
| 自检 | 用 `docker compose port nginx 443` 拿真实端口，`curl --cacert` 打 `/healthz`，最多等 40s |

可选参数：`--no-cert`（证书由外部提供）、`--no-health`（跳过自检）。

**等价的手动步骤**（想逐步看输出时用）：

```bash
erp init --mode mcp --bind 0.0.0.0:8765 --storage postgres \
    --pg-host db --pg-password "$POSTGRES_PASSWORD" \
    --public-host erp-tools.lan:8443 --admin-user root   # 生成 config.yaml
bash scripts/gen-cert.sh --ensure                        # nginx 用的证书
docker compose up -d --build
erp key --server https://erp-tools.lan:8443 --write-mcp-json ./mcp.json
curl --cacert certs/erp-tools.crt https://localhost:8443/healthz
```

**为什么容器里没有密码**

- compose **不用 `env_file`**，`.env` 只服务于 compose 自身（PG 密码 / 对外地址），
  应用只读挂进去的 `config.yaml`；`ERP_PASSWORD` 没有位置可写。
- 登录发生在 `/v1/auth/login` 那一刻，密码只存在于该请求的调用栈。

### 6.1 Docker 模式下不用敲 `erp serve`

容器里跑的就是同一个 `erp serve`，配置**全部在 `./config.yaml`（挂到 `/app/api/config.yaml`）**，
你只需要编辑 `.env` 然后 `bash scripts/up.sh`：

| 需求 | 改哪里 |
| --- | --- |
| 对外暴露 | nginx 的 `ports:`（`ERP_HTTP_PORT` / `ERP_HTTPS_PORT`），app 只 `expose: 8765` |
| bind / 明文 | `config.yaml` 的 `server.bind: 0.0.0.0:8765` + `allow_plaintext: true`（TLS 在 nginx） |
| 数据库 | `server.storage: postgres` + `pg_dsn`（up.sh 按 `POSTGRES_*` + `db:5432` 拼好） |
| 服务注册密钥 | `server.register_secret: auto`（首次启动生成到卷里 `register.secret`） |
| Host 白名单 | `allow_hosts: auto` + `public_host`（客户端实际写的 `host:port`） |
| 限流 IP | `trust_proxy: true`（确实有 nginx 才开；up.sh 生成的配置已带） |
| **附件代理基地址** | `ERP_PUBLIC_BASE` 环境变量（留空 = 自动用 `X-Forwarded-Proto` + `Host`） |
| 附件大小 | `docker/nginx.conf` 的 `client_max_body_size`（默认 24m） |

所以 **A/B 两种玩法在 docker 里对应的是改 `config.yaml`，而不是在宿主机上再起一个进程**：

- 只要附件代理给外部用 → 不用改任何东西，`proxy_url` 会自动是
  `https://<ERP_PUBLIC_HOST>/v1/files?...`（nginx 的 `location /` 已经代理 `/v1/*`）。
- 要让外部 MCP 客户端连 → 把 `public_host` 设成对外地址、证书 SAN 写全，
  `allow_hosts: auto` 会自动跟着它走。

> 数据分两处：api-key 与 MCP 服务注册信息在 PostgreSQL 命名卷 `pgdata`，
> cookie / `proxy.secret` / 自动签的证书在 `erpdata`；重建堆栈都不丢，`docker compose down -v` 会清空两者。

**必须对齐的一处配置**

```ini
ERP_PUBLIC_HOST=erp-tools.lan:8443     # = 客户端实际访问的 host:port（写进 config.yaml 的 public_host）
```

nginx 用 `proxy_set_header Host $http_host;` 原样透传（**带端口**），应用侧逐字比对白名单：

- 用 `https://192.168.1.226:8443/mcp` 访问 → 白名单里也要有它，
  多个地址逗号分隔：`ERP_PUBLIC_HOST=erp-tools.lan:8443,192.168.1.226:8443`。
- 写成 `$host` 会丢端口 → 客户端一律收到 `421`。

**其它要点**

- **不发布 app 端口**：`app` 只 `expose: 8765`，宿主机只能通过 nginx 访问。
- **`trust_proxy: true` 必须开**（up.sh 生成的配置已带），否则限流按 nginx 的 IP 统计。
- **换纯 HTTP**：`.env` 加 `NGINX_CONF=docker/nginx-http.conf`，并把
  `ERP_PUBLIC_HOST` 改成 `erp-tools.lan:8080`。
- **端口冲突**：改 `ERP_HTTP_PORT` / `ERP_HTTPS_PORT`（默认 8080 / 8443）。

### 6.1 数据持久化：重建堆栈什么会丢、什么不会

compose 里有两类挂载，**重建（`up -d --build`）都不会丢**，区别在于删得快不快：

| 挂载 | 类型 | 内容 | 会不会丢 |
| --- | --- | --- | --- |
| `pgdata:/var/lib/postgresql/data` | **命名卷**（默认名 `erp-tools-pgdata`） | `api_keys`（api-key 的 sha256）、`mcp_services`（服务注册信息） | `up -d` / `restart` / `down` 都保留；只有 **`down -v`** 或 `docker volume rm` 才删 |
| `erpdata:/data` | **命名卷**（默认名 `erp-tools-erpdata`） | `cookies/<工号>.json`、`qxid-<工号>.json`、`proxy.secret` | 同上 |
| `${ERP_CERT_DIR:-./certs}` | 绑定挂载 | nginx 的 `.crt` / `.key` | 与容器无关，宿主机文件在就一直在 |
| `./${NGINX_CONF:-docker/nginx.conf}` | 绑定挂载 | nginx 站点配置 | 来自仓库，**跟着 git 更新** |

要点：

- **卷名被显式固定**（`erp-tools-pgdata` / `erp-tools-erpdata`），所以换目录 clone、
  或用 `-p` 指定别的项目名，都不会「凭空多出一个空卷」导致所有人 key 失效。
- `docker compose down` 只删容器和网络；**永远不要**图省事敲 `down -v`，
  那等于让所有人重新 `erp key` 一次。
- 备份两个卷（PG 建议走 `pg_dump`，比拷数据目录干净）：

  ```bash
  docker compose exec -T db pg_dump -U erp erp | gzip > backup/erp-$(date +%F).sql.gz
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
cp docker/env.example .env && vi .env    # 填 ERP_HOST / ERP_PUBLIC_HOST / POSTGRES_PASSWORD
bash scripts/up.sh                        # 生成 config.yaml + 自签证书 + 起栈 + 健康检查
#    证书默认就落到 $ERP_CERT_DIR；不需要 python、不需要额外装包

# 2) 首次构建后自检
curl --cacert /srv/erp-tools/certs/erp-tools.crt https://localhost:8443/healthz

# 3) 之后交给 deploy.sh（cron / systemd timer）
```

> 重启容器**不会**让已有的 api-key 失效：会话从卷里的 `cookies/<工号>.json` 恢复。
> 只有 ERP 会话本身过期时才需要重新 `erp key`。


## 7. 错误码

| 状态 | `error` | 含义 |
| --- | --- | --- |
| 400 | `bad_request` | 请求体缺字段 |
| 400 | `invalid_service_info` | 服务注册字段非法（名称缺失、地址不是 http(s)、传输方式未知…） |
| 400 | `invalid_account` | 平台账号用户名/密码不符合规则 |
| 400 | `invalid_erp_credentials` | 绑定 ERP 时工号或密码错 |
| 401 | `invalid_credentials` | 账号或密码错（统一文案，不区分原因） |
| 401 | `login_required` / `session_expired` | 没登录 / 登录会话已过期 |
| 401 | `missing_bearer` / `invalid_api_key` / `api_key_revoked` | key 缺失 / 不存在 / 已撤销 |
| 401 | `erp_session_expired` | ERP 会话过期，key 已一并作废 |
| 403 | `account_disabled` | 账号已被管理员禁用（会话与 token 都已失效） |
| 403 | `admin_required` | 需要管理员权限 |
| 403 | `signup_disabled` | 本实例关闭了平台账号自助注册（`--no-signup`） |
| 403 | `register_secret_invalid` | 服务端启用了注册共享密钥（`register_secret: auto` 默认生成），请求头密钥不对 |
| 404 | `token_not_found` | 撤销的 token 不属于当前账号或已撤销 |
| 404 | `user_not_found` | 管理员操作的账号不存在 |
| 409 | `username_taken` | 平台用户名已被占用 |
| 409 | `erp_not_bound` | 还没绑定 ERP 账号就申请 token |
| 409 | `cannot_modify_self` / `cannot_delete_self` | 管理员不能禁用/降级/删除自己 |
| 423 | `locked` | 同一账号连续失败 10 次，锁 15 分钟 |
| 429 | `rate_limited` | 登录/注册限流（各 5~10 次/分钟） |
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
  [quote_create/README.md](../api/src/erp_tools/tools/quote_create/README.md) 的「已知限制」。
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
cp docker/env.example .env
```

`.env` 只需要这几个（其余保持注释即可）：

```ini
ERP_HOST=http://192.168.1.199:8889
ERP_PUBLIC_HOST=erp-tools.lan:8443        # ★ 等于客户端实际访问的 host:port
POSTGRES_PASSWORD=change-me               # ★ 请用 URL 安全字符（会拼进连接串）
ERP_CERT_DIR=/srv/erp-tools/certs         # 证书放仓库外，重新 clone 也不丢
# 可选：ERP_ADMIN_USER（第一个管理员）/ ERP_CAPTCHA_HOST / ERP_HTTPS_PORT / ERP_HTTP_PORT
#       POSTGRES_USER / POSTGRES_DB
```

应用的其它参数（并发、注册接口密钥、信任代理…）都在 `config.yaml` 里，
`scripts/up.sh` 会按 `.env` 生成；要改就改 `config.yaml`，不用再往 `.env` 里塞。

证书二选一：

```powershell
# A) 在自己的 Windows 上签好再传（服务器上什么都不用装）
.\api\.venv\Scripts\python.exe scripts\make-cert.py `
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
docker compose config | grep -E 'ERP_HOST|ERP_PUBLIC_HOST|POSTGRES|source:'
cat config.yaml                    # up.sh 生成的应用配置（mode/bind/storage/pg_dsn…）
```

必须看到：`ERP_HOST` / `ERP_PUBLIC_HOST` / `POSTGRES_PASSWORD` 都已渲染出真实值，
certs 的 source 指向 `/srv/erp-tools/certs`，`config.yaml` 里 `storage: postgres`
且 `pg_dsn` 指向 `db:5432`。

### 4. 首次构建与启动

```bash
bash scripts/up.sh                # 生成 config.yaml → 缺证书就自签 → build → up → 健康检查（推荐）

# 或者手动四步：
erp init --mode mcp --bind 0.0.0.0:8765 --storage postgres \
    --pg-host db --pg-password "$POSTGRES_PASSWORD" \
    --public-host "$ERP_PUBLIC_HOST"              # 生成 config.yaml（已存在则跳过）
bash scripts/gen-cert.sh --ensure                 # nginx 用的证书
docker compose up -d --build
docker compose ps                                 # app 应为 healthy，nginx 应为 running/healthy
```

自检（不经过客户端证书信任也能验）：

```bash
curl --cacert /srv/erp-tools/certs/erp-tools.crt https://localhost:8443/healthz
# → {"status":"ok","service":"HB-MCP","version":"0.3.0","keys":0,
#    "global_concurrency":6,"store":"postgresql","register_secret_required":true}
```

`register_secret_required: true` 说明注册接口已启用共享密钥（`register_secret: auto`
自动生成）；要读它：`docker compose exec app cat /data/register.secret`。

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
        --cacert /path/to/erp-tools.crt \      # Windows 用 --cacert certs\erp-tools.crt
        --write-mcp-json ./mcp.json            # 直接把 key 写进客户端配置，不用手抄
# → stdout 输出 erp_xxx；mcp.json 里已带 Authorization: Bearer <key>
```

每个人/每台 AI 各自执行一次，各拿各的 key；密码只在那一次请求里用。
也可以完全不开终端：浏览器打开 `https://erp-tools.lan:8443/ui`，
注册 → 绑定 ERP → 新增 Token，弹窗里那份 `mcp.json` 复制即可。

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
# → 应返回 24 个工具名
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
# 备份（两处有状态：PostgreSQL + 运行时卷）
docker compose exec -T db pg_dump -U erp erp | gzip > /srv/erp-tools/backup/erp-$(date +%F).sql.gz
docker run --rm -v erp-tools-erpdata:/data -v /srv/erp-tools/backup:/backup alpine \
    tar czf /backup/erpdata-$(date +%F).tar.gz -C /data .

# 手动升级 / 强制重建（改了 .env 或 config.yaml 必须加 --force，否则代码没变不会重建）
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
| 3 | `请在 .env 里设置 ERP_HOST` | `.env` 没建或变量名拼错（compose 只从同目录 `.env` 读）；也可直接改 `config.yaml` 的 `erp.host` |
| 4 | `docker compose ps` 里 `app` 是 `unhealthy`/`Restarting` | 先看 `docker compose logs --tail=50 app`：① `配置文件 ... Permission denied` → 宿主 `chmod 644 config.yaml`（容器以 uid 10001 运行）；② `没有存储` → `config.yaml` 没有 `server:` 段，`rm config.yaml && bash scripts/up.sh` 重生成；③ psycopg 报 password 认证失败 → `POSTGRES_PASSWORD` 含特殊字符，换成 URL 安全字符后 `docker compose down -v` 重建（PG 里的旧密码已固化） |
| 4 | `421 Invalid Host header` | `ERP_PUBLIC_HOST` 与客户端实际用的 `host:port` 不一致（含端口） |
| 4 | 容器 healthy 但 `curl` 502 | 容器内到 ERP 不通，或 `ERP_HOST` 写错；`docker compose exec app python -c "import urllib.request;print(urllib.request.urlopen('http://192.168.1.199:8889/Admin/Login.aspx').status)"` |
| 4 | nginx 起不来 / `cannot load certificate` | `ERP_CERT_DIR` 下没有 `erp-tools.crt`/`.key`，或文件名不符 |
| 6 | `SSLCertVerificationError` | `--cacert` 指向的证书不对，或 SAN 不含该地址 |
| 6 | `401 invalid_credentials` | 工号密码错；连续 10 次会锁 15 分钟 |
| 7 | 客户端连不上但 `curl` 正常 | 客户端没信任自签 CA（Node 要 `NODE_EXTRA_CA_CERTS` 并重启） |
| 8 | `deploy.sh` 退出 3 | 仓库里有未提交改动（多半是 `.env` 被误改进了仓库目录并被跟踪） |

