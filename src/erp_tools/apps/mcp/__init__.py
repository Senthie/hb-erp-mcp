"""`erp-mcp` 入口。

MCP 和 HTTP 网关是**同一个进程、同一个应用**：`erp serve` 同时提供
认证端点和 `/mcp`，所以这里直接复用 `erp serve` 的参数与启动逻辑，
避免两套配置漂移。
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from ...cli import main as cli_main

    args = list(sys.argv[1:] if argv is None else argv)
    return cli_main(["serve", *args])


__all__ = ["main"]
