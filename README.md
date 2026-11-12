# erp-tools — 华博印刷 ERP 工具聚合

把散落在各脚本里的 ERP 逆向成果（登录、权限号、报价需求页、考勤页）收敛成一个
**可扩展的工具聚合项目**：同一份页面逻辑（`core/` + `tools/`）通过
**CLI / Python API / MCP 服务**三种入口对外提供。

## 能做什么

| 能力 | CLI | MCP 工具 | 读写 |
| --- | --- | --- | --- |
| 登录 / 权限号探测 / 自检 | `erp login` `erp doctor` `erp qxid` | `erp_whoami` `erp_capabilities` | — |
| 报价需求列表查询、导出 CSV | `erp query` | `erp_quote_list` | 读 |
| 报价需求详情 → 订单需求 JSON | `erp quote` | `erp_quote_detail` | 读 |
| 月度考勤明细 | `erp attendance` | `erp_attendance` | 读 |
| 新增报价需求（主表 + 产品明细 + 产品数量） | — | `erp_quote_create` | 写 |
| 改表头 / 改产品行 / 删单据 | — | `erp_quote_update` `erp_quote_update_product` `erp_quote_delete` | 写 |
| 表单结构 / 下拉选项 / 客户检索 | — | `erp_quote_new_form` `erp_quote_options` `erp_quote_customer_search` | 读 |

写入类接口**默认只预演**（`confirm=false`），落库时一律**写后回读校验**，不会假报成功。

## 三种入口

| 入口 | 用法 | 适用场景 | 文档 |
| --- | --- | --- | --- |
| **CLI** | `erp <工具> [选项]` | 本机跑批、导出 CSV / JSONL | [docs/cli.md](docs/cli.md) |
| **Python API** | `from erp_tools.api import ...` | 嵌进别的程序 | [docs/api.md](docs/api.md) |
| **MCP 服务** | `erp serve` → `/mcp` | AI 客户端（VS Code / Cursor）、多人共用 | [docs/mcp.md](docs/mcp.md) |

新增一个业务工具只需在 `tools/` 下加一个目录，三种入口自动都能用上。

## 目录结构

```
src/erp_tools/
├─ core/      通用能力：配置 / 会话 / 登录与 QXID 探测 / ASP.NET 表单 / 安全 / 限流
├─ tools/     业务工具（每个目录都有自己的 README.md）
│  ├─ quote_index/    列表查询 + CSV 导出（BJ_KHBJXQ/Index.aspx）
│  ├─ quote_request/  报价需求详情（BJ_KHBJXQ/Add.aspx）
│  ├─ quote_create/   报价需求读写：新增 / 改表头 / 改产品行 / 删除（BJ_KHBJXQ/Add_CPZL.aspx）
│  └─ attendance/     月度考勤（RS_KQDA_M/Add.aspx）
├─ runtime/   服务端运行时：api-key 存储、会话池、身份上下文
├─ apps/      服务端入口：server（认证网关 + /mcp）、mcp（工具注册）
├─ output/    jsonl（断点续跑）/ csv / markdown
├─ cli.py     命令行入口
└─ api.py     库入口

docker/       nginx 反代配置（HTTPS / 纯 HTTP 两套）
docs/         cli.md / api.md / mcp.md
packaging/    PyInstaller + Inno Setup 打包
scripts/      up.sh 一条命令起栈、gen-cert.sh 签证书、deploy.sh 定时拉取重建、build.ps1 打包
scripts/deploy.sh 服务器上按需重建（配合 cron / systemd timer）
tests/        离线测试 + fixtures 真实页面夹具
```

## 快速开始

```powershell
uv sync --extra dev --extra server                    # --extra server 仅 MCP 服务需要
Copy-Item packaging\config.example.yaml config.yaml   # 填 ERP 地址 / 工号 / 密码
.\.venv\Scripts\erp.exe setup                         # 向导：写配置 + 缓存 cookie
```

确认环境：

```powershell
.\.venv\Scripts\erp.exe doctor     # 端口 / 登录页 / 验证码 / OCR / cookie 有效性
.\.venv\Scripts\erp.exe login      # 登录并缓存 cookie + 探测 QXID
```

跑一次查询：

```powershell
.\.venv\Scripts\erp.exe query --customer IBL --status 需求审批 --out out\ibl.csv
```

启动 MCP 服务（可选）：

```powershell
.\.venv\Scripts\erp.exe serve                                       # 默认 127.0.0.1:8765
.\.venv\Scripts\erp.exe key --server http://127.0.0.1:8765 -u 21146  # 换一把 api-key
```

配置优先级：命令行 `--host/--user/--password` > 真实环境变量 > `config.yaml`
> 旧版 `.env` > 内置默认值。`erp config` 可随时确认生效来源，详见 [docs/cli.md](docs/cli.md)。

**密码不落盘**：CLI 登录只缓存 cookie；MCP 服务端只在 `/v1/auth/login` 那一次请求里使用密码，
库里只存 api-key 的 sha256。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/cli.md](docs/cli.md) | 全部命令的选项与示例、翻页策略、排错、页面协议速查 |
| [docs/api.md](docs/api.md) | `erp_tools.api` 函数签名、返回结构、异常体系、进阶用法 |
| [docs/mcp.md](docs/mcp.md) | MCP 服务、api-key、14 个工具、团队模式 TLS、Docker + nginx、错误码 |
| [tools/quote_index](src/erp_tools/tools/quote_index/README.md) | 列表查询字段与 CSV 输出 |
| [tools/quote_request](src/erp_tools/tools/quote_request/README.md) | 详情字段与解析规则 |
| [tools/quote_create](src/erp_tools/tools/quote_create/README.md) | 写入协议、必填链、已知限制 |
| [tools/attendance](src/erp_tools/tools/attendance/README.md) | 考勤字段与输出格式 |
| [packaging/README.md](packaging/README.md) | 打包成 exe / 安装包、体积、签名、升级 |

## 设计要点（都是踩坑换来的）

| 决策 | 原因 |
| --- | --- |
| **认证统一前置** | 报价需求页目前**不带 Cookie 也能读**（仅靠 URL 里的 `LoginUserID`），但一旦 ERP 收紧校验就会全线失效，所以统一先登录。 |
| **QXID 运行时探测** | 权限号跟账号角色绑定，写死 `876/2553` 换个人就错。登录后 GET `indexAjax.ashx?FunName=GetMenuTree` 解析菜单。注意该接口**必须用 GET**，POST 返回空。 |
| **全局限流** | 实测 ERP 约 4~8 并发即饱和（8/16 线程时单请求从 ~0.95s 涨到 ~1.6s，吞吐不再上升）。并发上限属于「ERP 实例」，不能每工具各开一个线程池。 |
| **JSONL + 主键续跑** | 每条记录一个 JSON 文件会产生 18496 个目录，重跑时无法判断完成情况；JSONL 追加写 + 按 `id` 去重，天然支持中断续跑。 |
| **夹具回归测试** | 页面改版过去只能靠跑很久才发现。现在 `tests/fixtures/` 存真实页面，解析器改动先跑单测。 |
| **`Connection: close`** | 老旧 IIS 7.5 上 Keep-Alive 极易导致连接池超时。 |
| **服务端不存密码** | 每个使用者用自己的工号密码换自己的 api-key，密码只在一次请求的调用栈里；会话过期只能重新登录。 |

## 已知限制

- 解析依赖中文标签定位；ERP 改文案需同步更新对应 `tools/*/parser.py` 并补夹具。
- 报价需求写入已跑通，但 **产品类别（树形下拉）不落库**、**多语言数量明细（`CPSLID`）不自动建**；
  细节见 [quote_create/README.md](src/erp_tools/tools/quote_create/README.md)。
- **api-key 无过期时间**：寿命等于绑定的 ERP 会话；主动作废用 `POST /v1/auth/logout`。
- **cookie 以明文保存在 `cookies/<工号>.json`**：建议收紧目录 ACL（接 DPAPI 尚未实现）。
- **限流与锁定是进程内内存态**：重启即清零，暂不支持多实例横向部署。
- 同一个 ERP 可能存在**多端口实例**（实测 `8889` 与 `8890` 数据不互通），切换前先 `erp config`。
- 更多限制见各工具文档与 [docs/mcp.md](docs/mcp.md) 的「已知限制」。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q     # 159 项，全部离线（不连真实 ERP）
```

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_security` / `test_ratelimit` | api-key 生成校验、日志脱敏、令牌桶、登录锁定 |
| `test_auth_store` / `test_sessions` | key 签发撤销与持久化；会话池复用与全局并发闸门 |
| `test_server_auth` / `test_server_config` | `/v1/*` 端到端、监听与 TLS/白名单校验 |
| `test_mcp_server` | MCP 握手、工具清单与 inputSchema、身份传递、撤销后 401 |
| `test_cli_key` / `test_auth_captcha` | 换 key 命令、验证码网络错误的分类 |
| `test_quote_create_parser` / `_qty` | 页面解析、提交体组装、产品数量回读 |
| 其余 | 各页面解析器 + CLI 配置 + CSV 输出 |

夹具 `tests/fixtures/` 存真实页面（报价详情 / 列表 / 考勤 / 客户选择 / 菜单响应），
解析器改动先跑单测。
