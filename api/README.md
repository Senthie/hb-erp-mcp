# api — HB-MCP 后端（Python）

ERP 工具聚合的后端：`core/` + `tools/` 是业务与页面逻辑，三种入口共用同一份代码
（CLI / Python API / MCP 服务）；`apps/server` 是 api-key 网关，另在 `/ui` 上托管
[`web/`](../web) 构建出的 React 界面。

仓库结构：

```
api/            ← 本目录（Python 后端）
  src/erp_tools/
    core/       配置 / 会话 / 登录与 QXID 探测 / ASP.NET 表单 / 安全 / 限流
    tools/      业务工具（每个目录都有 README.md）
    runtime/    api-key / 平台账号 / 会话 / MCP 服务注册的存储，会话池
    apps/       server（认证网关 + /ui + /mcp）、mcp（工具注册）
    output/     jsonl / csv / markdown
    cli.py      `erp` 命令行入口
    api.py      Python 库入口
  tests/        离线测试 + fixtures 真实页面夹具
  pyproject.toml / uv.lock
web/            ← React + Tailwind 可视化界面（登录 / 服务注册）
docs/           使用文档（cli / api / mcp / erp-menu / api 接口文档）
scripts/        起栈、签证书、自动部署、打包
packaging/      PyInstaller + Inno Setup
docker/         nginx 反代配置
```

## 本地开发

```powershell
cd api
uv sync --extra dev --extra server      # server：MCP + uvicorn + psycopg
uv run pytest -q                        # 全部离线，不连真实 ERP

# 起网关（可视化界面在 http://127.0.0.1:8765/ui/）
uv run erp serve --db .\dev.db          # 本地用 SQLite
uv run erp serve --pg-dsn postgresql://erp:secret@127.0.0.1:5432/erp   # 生产用 PostgreSQL
```

生产存储是 **PostgreSQL**（`ERPSRV_PG_DSN`）；`--db` 只用于本地开发与测试。
完整说明见仓库根目录的 [README.md](../README.md) 与 [docs/mcp.md](../docs/mcp.md)。
