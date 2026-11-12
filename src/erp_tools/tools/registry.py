"""工具注册表：新增工具只需在这里登记一次。"""

from __future__ import annotations

from typing import Type

from .attendance import AttendanceTool
from .base import Option, Tool
from .quote_index import QuoteListTool
from .quote_price import PriceDetailTool, PriceListTool, PriceNodeTool
from .quote_request import QuoteRequestTool

#: 名字 -> 实现类
_TOOLS: dict[str, Type[Tool]] = {
    QuoteListTool.name: QuoteListTool,
    PriceListTool.name: PriceListTool,
    PriceDetailTool.name: PriceDetailTool,
    PriceNodeTool.name: PriceNodeTool,
    QuoteRequestTool.name: QuoteRequestTool,
    AttendanceTool.name: AttendanceTool,
}


def tool_names() -> list[str]:
    return list(_TOOLS)


def get_tool(name: str) -> Type[Tool]:
    try:
        return _TOOLS[name]
    except KeyError:
        raise KeyError(f"未知工具 {name!r}，可用：{', '.join(_TOOLS)}") from None


def build_tool(name: str, auth) -> Tool:
    return get_tool(name)(auth)


def describe_tools() -> list[dict]:
    """给 `erp tools` 用：不需要实例化就能列出工具与选项。"""
    return [
        {
            "name": name,
            "summary": cls.summary,
            "options": [
                {"name": o.name, "cli": o.cli, "required": o.required,
                 "default": o.default, "help": o.help}
                for o in cls.options()
            ],
        }
        for name, cls in _TOOLS.items()
    ]


__all__ = ["Option", "Tool", "tool_names", "get_tool", "build_tool", "describe_tools"]
