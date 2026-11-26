"""ERP 工具聚合包。

分层：
  core/     通用能力（配置、会话、认证、权限探测、ASP.NET 表单、安全、限流）
  tools/    业务工具（每个工具 = 一个目录，注册表自动发现）
  runtime/  服务端运行时（api-key 存储、会话池、身份上下文）
  apps/     服务端入口（server: 认证网关 + MCP 挂载；mcp: 工具注册）
  output/   输出落盘（jsonl / markdown / csv）
  cli.py    命令行入口
  api.py    库入口

同一份 core/ + tools/ 同时被 CLI、HTTP 网关、MCP 三种入口复用。
"""

__version__ = "0.3.0"
