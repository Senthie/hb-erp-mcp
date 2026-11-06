"""ERP 工具聚合包。

分层：
  core/    通用能力（配置、会话、认证、权限探测、ASP.NET 表单）
  tools/   业务工具（每个工具 = 一个目录，注册表自动发现）
  output/  输出落盘（jsonl / markdown）
  cli.py   命令行入口
  api.py   库入口
"""

__version__ = "0.3.0"
