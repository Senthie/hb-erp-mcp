"""工具契约。

一个工具只声明「名字 + 选项 + 产出记录」，认证、限流、重试、断点、落盘
全部由 core 层提供。新增工具 = 在 tools/ 下加一个实现该协议的模块并在
registry 里登记。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterator

from ..core.auth import AuthManager


@dataclass
class Option:
    """工具的选项定义，CLI 与 API 共用。"""

    name: str
    help: str = ""
    required: bool = False
    default: Any = None
    #: 同 CLI 参数映射，例如 "--month" / "-m"
    cli: str | None = None


class Tool(ABC):
    """所有 ERP 工具的基类。"""

    #: 命令行里使用的名字
    name: str = ""
    #: 一句话说明
    summary: str = ""

    def __init__(self, auth: AuthManager) -> None:
        self.auth = auth
        self.client = auth.client

    @property
    def cfg(self):
        return self.auth.cfg

    @staticmethod
    def options() -> list[Option]:
        return []

    @abstractmethod
    def run(self, **opts) -> Iterator[dict]:
        """执行工具，逐条产出记录（生成器）。"""
        raise NotImplementedError

    def collect(self, **opts) -> list[dict]:
        return list(self.run(**opts))
